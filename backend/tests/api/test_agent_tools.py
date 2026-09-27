import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_search import indexed

from app.agents.contracts import ToolInput
from app.agents.tools import RepositoryTools
from app.core.errors import AppError
from app.jobs.prepare_search import run_preparation
from app.models import CodeChunk, Repository, RepositoryIndex
from app.services.search import SearchService


def test_all_tools_are_scoped_to_the_pinned_repository(auth_client):
    headers, repo, url = indexed(
        auth_client, "def hello():\n    return 'hello'\n\nprint(hello())\n"
    )
    job = auth_client.post(url + "/prepare", json={}, headers=headers).json()
    factory = auth_client.app.state.test_factory
    asyncio.run(run_preparation(factory, UUID(job["id"]), None))

    class Candidates:
        def __init__(self, ids):
            self.ids = ids

        async def lexical(self, index_id, terms):
            return self.ids

        async def symbols(self, index_id, terms):
            return []

        async def vector(self, index_id, vector):
            raise AssertionError("Agent tools are keyword-only")

    async def scenario():
        async with factory() as db:
            repository = await db.get(Repository, UUID(repo["id"]))
            source = await db.scalar(select(RepositoryIndex))
            ids = list((await db.scalars(select(CodeChunk.id))).all())
            search = SearchService(
                db,
                Candidates(ids),
                auth_client.app.state.rate_limiter,
                auth_client.app.state.settings,
                None,
            )
            tools = RepositoryTools(db, search, repository.user_id, repository.id, source.id)
            for name in ("search_code", "find_symbol", "find_references"):
                evidence = await tools.execute(
                    ToolInput(tool=name, query="hello", path=None, start_line=None)
                )
                assert evidence and all(e.commit_sha == source.commit_sha for e in evidence)
            evidence = await tools.execute(
                ToolInput(tool="read_file", query=None, path=evidence[0].path, start_line=1)
            )
            assert "def hello()" in evidence[0].content
            assert (
                await tools.execute(
                    ToolInput(tool="read_file", query=None, path="absent.py", start_line=1)
                )
                == []
            )
            with pytest.raises(AppError):
                await tools.execute(
                    ToolInput(tool="find_references", query="hello()", path=None, start_line=None)
                )
            tools.user_id = uuid4()
            with pytest.raises(AppError) as caught:
                await tools.execute(
                    ToolInput(tool="read_file", query=None, path="main.py", start_line=1)
                )
            assert caught.value.status == 404

    asyncio.run(scenario())
