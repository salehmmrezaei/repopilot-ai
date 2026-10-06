import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from test_indexes import imported
from test_repositories import sign_in

from app.agents.contracts import ToolInput
from app.agents.tools import RepositoryTools
from app.indexing.pipeline import PIPELINE_VERSION
from app.jobs.index_repository import run_index
from app.jobs.prepare_search import run_preparation
from app.models import (
    CodeChunk,
    CodeSymbol,
    Repository,
    RepositoryFile,
    RepositoryIndex,
    SearchDocument,
)
from app.repositories.search import SearchStore
from app.services.search import SearchService


def prepare(client, name="sample.tsx"):
    headers, repo, url = imported(client)
    content = Path("tests/fixtures/indexing/" + name).read_text()

    async def fixture():
        async with client.app.state.test_factory() as db:
            await db.execute(
                update(RepositoryFile).values(
                    content=content,
                    path=name,
                    language="typescript",
                    size=len(content.encode()),
                    content_hash=sha256(content.encode()).hexdigest(),
                )
            )
            await db.commit()

    asyncio.run(fixture())
    queued = client.post(url, json={}, headers=headers)
    assert queued.status_code == 202
    asyncio.run(run_index(client.app.state.test_factory, UUID(queued.json()["id"])))
    file = client.get(f"/repositories/{repo['id']}/files").json()["items"][0]
    state = client.get(url).json()
    assert (
        state["active"]["pipeline_version"] == PIPELINE_VERSION
        and not state["active"]["diagnostics"]
    )
    return headers, repo, url, file, state["active"]


def test_tsx_worker_api_search_documents_and_ownership(auth_client):
    headers, repo, url, file, active = prepare(auth_client)
    data = auth_client.get(url + "/files/" + file["id"]).json()
    assert data["language"] == "typescript" and data["path"].endswith(".tsx")
    assert {"Card", "Container", "CardProps"} <= {s["name"] for s in data["symbols"]}
    assert any(s["kind"] == "interface" for s in data["symbols"])
    assert (
        "".join(c["content"] for c in data["chunks"])
        == Path("tests/fixtures/indexing/sample.tsx").read_text()
    )
    search_url = f"/repositories/{repo['id']}/search"
    job = auth_client.post(search_url + "/prepare", headers=headers, json={}).json()
    asyncio.run(run_preparation(auth_client.app.state.test_factory, UUID(job["id"]), None))
    assert (
        auth_client.get(search_url).json()["keyword"]["documents_stored"] == active["chunk_count"]
    )

    async def documents():
        async with auth_client.app.state.test_factory() as db:
            chunks = await SearchStore(db).chunks(UUID(active["id"]))
            assert any(
                c.name == "Card" and "<article>" in c.content and c.language == "typescript"
                for c in chunks
            )
            assert any(
                "CardProps" in d.search_text for d in await db.scalars(select(SearchDocument))
            )

    asyncio.run(documents())
    other = sign_in(auth_client, "tsx-outsider@example.com")
    assert auth_client.get(url + "/files/" + file["id"]).status_code == 404
    assert auth_client.post(url, headers=other, json={}).status_code == 404


def test_agent_symbol_lookup_including_unlinked_properties_imports_and_dollar(auth_client):
    _, repo, _, _, active = prepare(auth_client, "sample.ts")

    async def tools():
        async with auth_client.app.state.test_factory() as db:
            repository = await db.get(Repository, UUID(repo["id"]))
            source = RepositoryTools(
                db,
                SearchService(
                    db,
                    None,
                    auth_client.app.state.rate_limiter,
                    auth_client.app.state.settings,
                    None,
                ),
                repository.user_id,
                repository.id,
                UUID(active["id"]),
            )
            for name in [
                "Options.limit",
                "Catalog",
                "Catalog.#value",
                "API.fetchUser",
                "$lookup",
                "Account",
            ]:
                results = await source.execute(
                    ToolInput(tool="find_symbol", query=name, path=None, start_line=None)
                )
                assert results, name
                assert len({item.chunk_id for item in results}) == len(results)
                assert all(
                    item.path == "sample.ts" and item.commit_sha == active["commit_sha"]
                    for item in results
                )
            references = await source.execute(
                ToolInput(tool="find_references", query="$lookup", path=None, start_line=None)
            )
            assert references and "$lookup" in references[0].content

    asyncio.run(tools())


def test_pipeline_upgrade_rebuilds_without_reimport_and_retains_old_snapshot(auth_client):
    headers, repo, url, file, first = prepare(auth_client)

    async def mark_old():
        async with auth_client.app.state.test_factory() as db:
            await db.execute(
                update(RepositoryIndex).values(pipeline_version="python312-v1:source-bytes-v1")
            )
            await db.commit()

    asyncio.run(mark_old())
    state = auth_client.get(url).json()
    assert state["rebuild_available"] and state["current_pipeline_version"] == PIPELINE_VERSION
    queued = auth_client.post(url, headers=headers, json={}).json()
    assert queued["id"] != first["id"]
    assert auth_client.get(url).json()["active"]["id"] == first["id"]
    asyncio.run(run_index(auth_client.app.state.test_factory, UUID(queued["id"])))
    state = auth_client.get(url).json()
    assert not state["rebuild_available"] and state["active"]["id"] == queued["id"]

    async def retained():
        async with auth_client.app.state.test_factory() as db:
            assert await db.get(RepositoryIndex, UUID(first["id"]))
            assert await db.scalar(select(CodeChunk).where(CodeChunk.index_id == UUID(first["id"])))
            assert await db.scalar(
                select(CodeSymbol).where(CodeSymbol.index_id == UUID(queued["id"]))
            )

    asyncio.run(retained())


def test_tsx_syntax_error_falls_back_without_failing_repository_job(auth_client):
    headers, _, url = imported(auth_client)

    async def invalid():
        async with auth_client.app.state.test_factory() as db:
            await db.execute(
                update(RepositoryFile).values(
                    path="broken.tsx",
                    language="typescript",
                    content="export const Card = () => <section>",
                )
            )
            await db.commit()

    asyncio.run(invalid())
    queued = auth_client.post(url, headers=headers, json={}).json()
    asyncio.run(run_index(auth_client.app.state.test_factory, UUID(queued["id"])))
    state = auth_client.get(url).json()["active"]
    assert state["status"] == "completed" and state["symbol_count"] == 0
    assert state["diagnostics"][0]["path"] == "broken.tsx"
    assert state["diagnostics"][0]["message"].startswith("syntax_error")
