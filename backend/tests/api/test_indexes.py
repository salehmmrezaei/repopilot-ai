import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from test_import_jobs import FakeGitHub
from test_repositories import connect, sign_in

from app.indexing.errors import LeaseLost
from app.jobs.dispatcher import dispatch_once
from app.jobs.import_repository import run_import
from app.jobs.index_repository import publish_index, run_index
from app.jobs.state import claim_job, report_stage
from app.models import CodeChunk, RepositoryFile, RepositoryIndex


def imported(client: TestClient):
    headers = sign_in(client)
    repo = connect(client, headers).json()
    asyncio.run(
        run_import(client.app.state.test_factory, FakeGitHub(), UUID(repo["job"]["id"]), 10)
    )
    return headers, repo, f"/repositories/{repo['id']}/index"


def test_index_roundtrip_idempotence_paging_and_source(auth_client: TestClient) -> None:
    headers, repo, url = imported(auth_client)
    assert auth_client.get(url).json()["latest"] is None
    assert auth_client.get(url).json()["active"] is None
    assert auth_client.get(url).json()["rebuild_available"] is False
    queued = auth_client.post(url, json={}, headers=headers)
    assert queued.status_code == 202
    assert auth_client.post(url, json={}, headers=headers).status_code == 409
    factory = auth_client.app.state.test_factory
    job_id = UUID(queued.json()["id"])
    asyncio.run(run_index(factory, job_id))
    asyncio.run(run_index(factory, job_id))
    state = auth_client.get(url).json()
    assert state["latest"]["status"] == "completed"
    assert state["active"]["chunk_count"] == 1
    assert auth_client.post(url, json={}, headers=headers).status_code == 200
    file = auth_client.get(f"/repositories/{repo['id']}/files").json()["items"][0]
    indexed = auth_client.get(url + f"/files/{file['id']}").json()
    assert indexed["commit_sha"] == "a" * 40
    assert indexed["chunks"][0]["content"] == "print('hello')"
    assert indexed["next_chunk_offset"] is None
    assert auth_client.get(url + f"/files/{file['id']}?chunk_offset=-1").status_code == 422
    assert auth_client.get(url + f"/files/{uuid4()}").status_code == 404


def test_index_authorization_and_csrf(auth_client: TestClient) -> None:
    headers, repo, url = imported(auth_client)
    assert auth_client.post(url, json={}).status_code == 403
    file = auth_client.get(f"/repositories/{repo['id']}/files").json()["items"][0]
    other = sign_in(auth_client, "other@example.com")
    assert auth_client.get(url).status_code == 404
    assert auth_client.post(url, json={}, headers=other).status_code == 404
    assert auth_client.post(url + "/cancel", json={}, headers=other).status_code == 404
    assert auth_client.get(url + f"/files/{file['id']}").status_code == 404


def test_index_requires_completed_import(auth_client: TestClient) -> None:
    headers = sign_in(auth_client)
    repo = connect(auth_client, headers).json()
    assert (
        auth_client.post(f"/repositories/{repo['id']}/index", json={}, headers=headers).status_code
        == 409
    )


def test_cancel_and_expired_leases_fence_index_workers(auth_client: TestClient) -> None:
    headers, _, url = imported(auth_client)
    queued = auth_client.post(url, json={}, headers=headers).json()
    factory = auth_client.app.state.test_factory
    job_id = UUID(queued["id"])
    first = asyncio.run(claim_job(factory, job_id, RepositoryIndex))
    assert first is not None

    async def reclaim():
        async with factory() as db:
            await db.execute(
                update(RepositoryIndex).values(
                    lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
            await db.commit()
        second = await claim_job(factory, job_id, RepositoryIndex)
        assert second is not None and second.attempt == 2
        with pytest.raises(LeaseLost):
            await report_stage(factory, first, "storing", RepositoryIndex)
        return second

    second = asyncio.run(reclaim())
    assert auth_client.post(url + "/cancel", json={}, headers=headers).status_code == 204
    with pytest.raises(LeaseLost):
        asyncio.run(publish_index(factory, second, [], [], [], 0))
    assert auth_client.post(url, json={}, headers=headers).status_code == 202


def test_failed_replacement_preserves_previous_index(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _, url = imported(auth_client)
    factory = auth_client.app.state.test_factory
    first = auth_client.post(url, json={}, headers=headers).json()
    asyncio.run(run_index(factory, UUID(first["id"])))
    # Simulate a pipeline upgrade. Failed new generation must not erase the old one.
    monkeypatch.setattr("app.services.index.PIPELINE_VERSION", "new-version")
    second = auth_client.post(url, json={}, headers=headers).json()
    assert first["id"] != second["id"]
    asyncio.run(run_index(factory, UUID(second["id"])))
    state = auth_client.get(url).json()
    assert state["latest"]["error_code"] == "index_version_mismatch"
    assert state["active"]["id"] == first["id"]


def test_diagnostics_are_visible(auth_client: TestClient) -> None:
    headers, _, url = imported(auth_client)
    factory = auth_client.app.state.test_factory

    async def break_source():
        async with factory() as db:
            await db.execute(update(RepositoryFile).values(content="def broken(:\n"))
            await db.commit()

    asyncio.run(break_source())
    job = auth_client.post(url, json={}, headers=headers).json()
    asyncio.run(run_index(factory, UUID(job["id"])))
    state = auth_client.get(url).json()
    assert state["active"]["diagnostics"][0]["message"].startswith("syntax_error")
    assert state["active"]["symbol_count"] == 0


def test_dispatches_indexes_and_cascade_removes_outputs(auth_client: TestClient) -> None:
    headers, repo, url = imported(auth_client)
    job = auth_client.post(url, json={}, headers=headers).json()
    factory = auth_client.app.state.test_factory
    sent = []

    async def publish(job_id):
        sent.append(str(job_id))

    asyncio.run(dispatch_once(factory, publish, RepositoryIndex))
    assert sent == [job["id"]]
    asyncio.run(run_index(factory, UUID(job["id"])))
    assert auth_client.delete(f"/repositories/{repo['id']}", headers=headers).status_code == 204

    async def check():
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(CodeChunk)) == 0
            assert await db.scalar(select(func.count()).select_from(RepositoryIndex)) == 0

    asyncio.run(check())


def test_database_rejects_cross_snapshot_chunk(auth_client: TestClient) -> None:
    headers, repo, url = imported(auth_client)
    job = auth_client.post(url, json={}, headers=headers).json()
    factory = auth_client.app.state.test_factory
    file = auth_client.get(f"/repositories/{repo['id']}/files").json()["items"][0]

    async def invalid():
        async with factory() as db:
            db.add(
                CodeChunk(
                    index_id=UUID(job["id"]),
                    import_job_id=uuid4(),
                    file_id=UUID(file["id"]),
                    ordinal=0,
                    start_offset=0,
                    end_offset=1,
                    start_line=1,
                    end_line=1,
                    kind="text",
                    content="x",
                    content_hash="0" * 64,
                )
            )
            with pytest.raises(IntegrityError):
                await db.flush()

    asyncio.run(invalid())


def test_publish_constraint_failure_rolls_back_entire_generation(auth_client: TestClient) -> None:
    headers, repo, url = imported(auth_client)
    queued = auth_client.post(url, json={}, headers=headers).json()
    factory = auth_client.app.state.test_factory
    job_id = UUID(queued["id"])
    claim = asyncio.run(claim_job(factory, job_id, RepositoryIndex))
    assert claim is not None
    file = auth_client.get(f"/repositories/{repo['id']}/files").json()["items"][0]
    common = dict(
        index_id=job_id,
        import_job_id=UUID(repo["job"]["id"]),
        start_offset=0,
        end_offset=1,
        start_line=1,
        end_line=1,
        kind="text",
        content="x",
        content_hash="0" * 64,
    )

    async def scenario():
        with pytest.raises(IntegrityError):
            await publish_index(
                factory,
                claim,
                [],
                [
                    CodeChunk(file_id=UUID(file["id"]), ordinal=0, **common),
                    CodeChunk(file_id=uuid4(), ordinal=1, **common),
                ],
                [],
                1,
            )
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(CodeChunk)) == 0
            assert (
                await db.scalar(select(RepositoryIndex.status).where(RepositoryIndex.id == job_id))
                == "running"
            )

    asyncio.run(scenario())


def test_persists_symbols_signatures_and_symbol_chunk_links(auth_client: TestClient) -> None:
    from pathlib import Path

    headers, repo, url = imported(auth_client)
    factory = auth_client.app.state.test_factory
    content = Path("tests/fixtures/indexing/sample.py").read_text()

    async def fixture():
        async with factory() as db:
            await db.execute(update(RepositoryFile).values(content=content))
            await db.commit()

    asyncio.run(fixture())
    job = auth_client.post(url, json={}, headers=headers).json()
    asyncio.run(run_index(factory, UUID(job["id"])))
    file = auth_client.get(f"/repositories/{repo['id']}/files").json()["items"][0]
    response = auth_client.get(url + f"/files/{file['id']}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["symbols"]) == 7
    assert data["symbols"][4]["signature"] == "def find(name: str) -> str"
    assert data["symbols"][5]["parent_ordinal"] == 4
    assert any(c["symbol_ordinal"] == 4 for c in data["chunks"])
    assert "".join(c["content"] for c in data["chunks"]) == content
    assert auth_client.delete(f"/repositories/{repo['id']}", headers=headers).status_code == 204
