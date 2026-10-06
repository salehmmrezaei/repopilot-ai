import asyncio
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_import_jobs import FakeGitHub
from test_indexes import imported
from test_repositories import sign_in

from app.jobs.import_repository import run_import
from app.jobs.index_repository import run_index
from app.models import CodeChunk, RepositoryIndex


def test_refresh_keeps_source_and_reuses_exact_unchanged_files(
    auth_client: TestClient, monkeypatch
):
    headers, repo, url = imported(auth_client)
    factory = auth_client.app.state.test_factory
    first = auth_client.post(url, json={}, headers=headers).json()
    asyncio.run(run_index(factory, UUID(first["id"])))
    files_url = f"/repositories/{repo['id']}/files"
    old_file = auth_client.get(files_url).json()["items"][0]
    refresh = auth_client.post(f"/repositories/{repo['id']}/refresh", json={}, headers=headers)
    assert refresh.status_code == 202
    assert (
        auth_client.post(
            f"/repositories/{repo['id']}/refresh", json={}, headers=headers
        ).status_code
        == 409
    )
    assert auth_client.get(files_url).json()["items"][0]["id"] == old_file["id"]
    assert auth_client.get(url).json()["active"]["id"] == first["id"]
    asyncio.run(run_import(factory, FakeGitHub(), UUID(refresh.json()["job"]["id"]), 10))
    new_file = auth_client.get(files_url).json()["items"][0]
    assert new_file["id"] != old_file["id"]
    second = auth_client.post(url, json={}, headers=headers)
    assert second.status_code == 202, second.text

    def forbidden(*args):
        raise AssertionError("Unchanged source must not be parsed")

    monkeypatch.setattr("app.jobs.index_repository.index_source", forbidden)
    asyncio.run(run_index(factory, UUID(second.json()["id"])))
    state = auth_client.get(url).json()["active"]
    assert state["id"] == second.json()["id"]

    async def inspect():
        async with factory() as db:
            job = await db.get(RepositoryIndex, UUID(state["id"]))
            assert job.files_skipped == 1
            chunks = list(await db.scalars(select(CodeChunk).where(CodeChunk.index_id == job.id)))
            assert chunks[0].file_id == UUID(new_file["id"])
            assert chunks[0].content == "print('hello')"

    asyncio.run(inspect())
    assert auth_client.get(files_url + "/" + old_file["id"]).status_code == 404


def test_refresh_cancel_and_foreign_owner(auth_client):
    headers, repo, _ = imported(auth_client)
    url = f"/repositories/{repo['id']}"
    assert auth_client.post(url + "/refresh", json={}).status_code == 403
    assert auth_client.post(url + "/refresh", json={}, headers=headers).status_code == 202
    assert auth_client.post(url + "/cancel", json={}, headers=headers).status_code == 204
    assert auth_client.get(url + "/files").json()["items"]
    other = sign_in(auth_client, "other@example.com")
    assert auth_client.post(url + "/refresh", json={}, headers=other).status_code == 404


def test_oauth_start_requires_csrf_and_state_is_bound(auth_client):
    settings = auth_client.app.state.settings
    settings.github_oauth_enabled = True
    settings.github_client_id = "test-client"
    settings.github_client_secret = SecretStr("test-secret")
    settings.github_token_key = SecretStr(Fernet.generate_key().decode())
    headers = sign_in(auth_client)
    assert auth_client.post("/auth/github/start", json={}).status_code == 403
    response = auth_client.post(
        "/auth/github/start", json={"private_access": True}, headers=headers
    )
    assert response.status_code == 200, response.text
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(response.json()["url"]).query)
    assert query["scope"] == ["repo"]
    assert query["code_challenge_method"] == ["S256"]
    auth_client.cookies.delete("repopilot_oauth")
    assert (
        auth_client.get(
            "/auth/github/callback", params={"state": query["state"][0], "code": "bad"}
        ).status_code
        == 400
    )


def test_metrics_private_and_low_cardinality(auth_client):
    assert auth_client.get("/metrics").status_code == 404
    auth_client.app.state.settings.metrics_token = SecretStr("m" * 40)
    auth_client.get("/not-a-route-with-secret-value")
    response = auth_client.get("/metrics", headers={"Authorization": "Bearer " + "m" * 40})
    assert response.status_code == 200, response.text
    assert "repopilot_jobs" in response.text
    assert "unmatched" in response.text
    assert "not-a-route-with-secret-value" not in response.text


def test_reviews_disabled_and_owner_bound(auth_client):
    headers, repo, _ = imported(auth_client)
    url = f"/repositories/{repo['id']}/reviews"
    assert (
        auth_client.post(
            url, json={"pull_number": 1, "request_key": str(uuid4())}, headers=headers
        ).status_code
        == 409
    )
    other = sign_in(auth_client, "other@example.com")
    assert auth_client.get(url).status_code == 404
    assert (
        auth_client.post(
            url, json={"pull_number": 1, "request_key": str(uuid4())}, headers=other
        ).status_code
        == 404
    )


def test_refresh_reuses_embedding_vectors_without_provider_calls(auth_client):
    from test_search import FakeProvider, indexed

    from app.jobs.prepare_search import run_preparation

    headers, repo, url = indexed(auth_client)
    auth_client.app.state.settings.embeddings_enabled = True
    factory = auth_client.app.state.test_factory
    provider = FakeProvider()
    first = auth_client.post(url + "/prepare", json={"mode": "hybrid"}, headers=headers).json()
    asyncio.run(run_preparation(factory, UUID(first["id"]), provider))
    assert provider.calls == 1
    refreshed = auth_client.post(
        f"/repositories/{repo['id']}/refresh", json={}, headers=headers
    ).json()
    asyncio.run(run_import(factory, FakeGitHub(), UUID(refreshed["job"]["id"]), 10))
    next_index = auth_client.post(
        f"/repositories/{repo['id']}/index", json={}, headers=headers
    ).json()
    asyncio.run(run_index(factory, UUID(next_index["id"])))
    second = auth_client.post(url + "/prepare", json={"mode": "hybrid"}, headers=headers)
    assert second.status_code == 202, second.text
    asyncio.run(run_preparation(factory, UUID(second.json()["id"]), provider))
    assert provider.calls == 1
    current = auth_client.get(url).json()["hybrid"]
    assert current["documents_stored"] == 1
    assert current["input_tokens"] == current["reserved_tokens"] == 0


def test_incremental_reparses_changed_source_and_drops_deleted_files(auth_client, monkeypatch):
    import io
    import tarfile

    from app.indexing.pipeline import index_source
    from app.integrations.github.client import RepositorySource

    headers, repo, url = imported(auth_client)
    factory = auth_client.app.state.test_factory
    first = auth_client.post(url, json={}, headers=headers).json()
    asyncio.run(run_index(factory, UUID(first["id"])))

    class Changed(FakeGitHub):
        async def resolve(self, owner, name):
            return RepositorySource(42, owner, name, "main", "b" * 40)

        async def download(self, source):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                data = b"def replacement():\n    return 42\n"
                info = tarfile.TarInfo("r/new.py")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            return stream.getvalue()

    refreshed = auth_client.post(
        f"/repositories/{repo['id']}/refresh", json={}, headers=headers
    ).json()
    asyncio.run(run_import(factory, Changed(), UUID(refreshed["job"]["id"]), 10))
    second = auth_client.post(url, json={}, headers=headers).json()
    called = []

    def parse(content, language, path):
        called.append(path)
        return index_source(content, language, path)

    monkeypatch.setattr("app.jobs.index_repository.index_source", parse)
    asyncio.run(run_index(factory, UUID(second["id"])))
    assert called == ["new.py"]
    files = auth_client.get(f"/repositories/{repo['id']}/files").json()["items"]
    assert [f["path"] for f in files] == ["new.py"]
    state = auth_client.get(url).json()["active"]
    assert state["commit_sha"] == "b" * 40
    indexed_file = auth_client.get(url + "/files/" + files[0]["id"]).json()
    assert indexed_file["symbols"][0]["name"] == "replacement"
