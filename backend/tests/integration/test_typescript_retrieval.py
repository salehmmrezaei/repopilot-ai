import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.rate_limits import MemoryRateLimiter
from app.database.session import create_engine
from app.evaluation.fixtures import seed
from app.evaluation.retrieval import DATASET
from app.jobs.prepare_search import run_preparation
from app.models import User
from app.repositories.search import SearchStore
from app.retrieval.postgres import PostgresCandidates
from app.retrieval.text import symbol_terms
from app.services.search_preparation import PreparationService


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1", reason="requires migrated isolated PostgreSQL"
)
def test_typescript_symbol_retrieval_uses_pinned_source_and_exact_identifiers():
    async def scenario():
        settings = Settings()
        assert settings.database_url.get_secret_value().split("?")[0].endswith("_test")
        engine = create_engine(settings)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user = uuid4()
        try:
            repo, _ = await seed(factory, DATASET.parent / "typescript-v1/corpus", user)
            async with factory() as db:
                job, _ = await PreparationService(db, MemoryRateLimiter(), settings).start(
                    user, repo, "keyword"
                )
            await run_preparation(factory, job.id, None)
            async with factory() as db:
                chunks = {c.id: c for c in await SearchStore(db).chunks(job.source_index_id)}
                for name in [
                    "$lookup",
                    "API.pathForUser",
                    "Catalog.findByPrefix",
                    "Card",
                    "LookupOptions",
                    "RecordId",
                ]:
                    hits = await PostgresCandidates(db).symbols(job.id, symbol_terms(name))
                    assert hits and set(hits) <= chunks.keys()
                    assert any(chunks[key].name == name for key in hits), name
                assert not await PostgresCandidates(db).symbols(uuid4(), symbol_terms("Card"))
        finally:
            async with engine.begin() as connection:
                await connection.execute(delete(User).where(User.id == user))
            await engine.dispose()

    asyncio.run(scenario())
