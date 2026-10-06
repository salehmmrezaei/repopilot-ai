import asyncio
import logging
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.embeddings.provider import EmbeddingProvider, aggregate
from app.embeddings.tokens import token_parts
from app.indexing.errors import LeaseLost
from app.jobs.state import Claim, claim_job, lease_condition, report_stage
from app.models import SearchDocument, SearchIndex
from app.repositories.search import SearchStore
from app.retrieval.text import VERSION, embedding_text, lexical_text

logger = logging.getLogger("repopilot.search_index")


async def reserve(factory: async_sessionmaker[AsyncSession], claim: Claim, tokens: int) -> None:
    async with factory() as db:
        found = await db.scalar(
            update(SearchIndex)
            .where(
                lease_condition(claim, SearchIndex),
                SearchIndex.reserved_tokens + tokens <= SearchIndex.token_budget,
            )
            .values(reserved_tokens=SearchIndex.reserved_tokens + tokens)
            .returning(SearchIndex.id)
        )
        if found is None:
            raise AppError(
                "embedding_budget_exceeded",
                "Embedding budget exhausted or job cancelled. "
                "Review usage and the configured budget before retrying.",
                409,
            )
        await db.commit()


async def checkpoint(
    factory: async_sessionmaker[AsyncSession],
    claim: Claim,
    documents: list[SearchDocument],
    usage: int,
) -> None:
    async with factory() as db:
        found = await db.scalar(
            update(SearchIndex)
            .where(lease_condition(claim, SearchIndex))
            .values(
                documents_stored=SearchIndex.documents_stored + len(documents),
                input_tokens=SearchIndex.input_tokens + usage,
            )
            .returning(SearchIndex.id)
        )
        if found is None:
            raise LeaseLost()
        db.add_all(documents)
        await db.commit()


async def run_preparation(
    factory: async_sessionmaker[AsyncSession],
    job_id: UUID,
    provider: EmbeddingProvider | None,
    timeout_seconds: int = 90,
) -> None:
    claim = await claim_job(factory, job_id, SearchIndex)
    if claim is None:
        return
    started = perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            async with factory() as db:
                job = await db.get(SearchIndex, job_id)
                if job is None:
                    raise LeaseLost()
                if job.pipeline_version != VERSION:
                    raise AppError(
                        "search_version_mismatch",
                        "Restart workers with the current application image.",
                        409,
                    )
                if job.mode == "hybrid" and (
                    provider is None or provider.profile != job.provider_profile
                ):
                    raise AppError(
                        "embedding_configuration",
                        "Enable the matching embedding provider before retrying.",
                        409,
                    )
                cached: dict[str, SearchDocument] = {}
                previous = await db.scalar(
                    select(SearchIndex)
                    .where(
                        SearchIndex.repository_id == job.repository_id,
                        SearchIndex.id != job.id,
                        SearchIndex.status == "completed",
                        SearchIndex.mode == job.mode,
                        SearchIndex.provider_profile == job.provider_profile,
                        SearchIndex.pipeline_version == job.pipeline_version,
                    )
                    .order_by(SearchIndex.finished_at.desc(), SearchIndex.id)
                    .limit(1)
                )
                if previous:
                    cached = {
                        d.input_hash: d
                        for d in await db.scalars(
                            select(SearchDocument).where(
                                SearchDocument.search_index_id == previous.id,
                            )
                        )
                    }
                chunks = await SearchStore(db).chunks(job.source_index_id)
                done = set(
                    (
                        await db.scalars(
                            select(SearchDocument.chunk_id).where(
                                SearchDocument.search_index_id == job_id
                            )
                        )
                    ).all()
                )
            pending = [c for c in chunks if c.id not in done]
            reused: list[SearchDocument] = []
            fresh = []
            for chunk in pending:
                value = embedding_text(chunk.path, chunk.name, chunk.signature, chunk.content)
                key = sha256(value.encode()).hexdigest()
                old = cached.get(key)
                if old is not None and (job.mode == "keyword" or old.embedding is not None):
                    reused.append(
                        SearchDocument(
                            search_index_id=job_id,
                            source_index_id=job.source_index_id,
                            chunk_id=chunk.id,
                            search_text=lexical_text(value),
                            input_hash=key,
                            token_count=old.token_count,
                            embedding=old.embedding,
                        )
                    )
                else:
                    fresh.append(chunk)
            for offset in range(0, len(reused), 100):
                await checkpoint(factory, claim, reused[offset : offset + 100], 0)
            pending = fresh
            tokenizer = (
                provider.tokenize if job.mode == "hybrid" and provider is not None else token_parts
            )
            if job.mode == "hybrid":
                required = await asyncio.to_thread(
                    lambda: sum(
                        sum(
                            map(
                                len,
                                tokenizer(embedding_text(c.path, c.name, c.signature, c.content)),
                            )
                        )
                        for c in pending
                    )
                )
                if job.reserved_tokens + required > job.token_budget:
                    raise AppError(
                        "embedding_budget_exceeded",
                        "Remaining source exceeds the embedding token budget. "
                        "Increase the configured budget and retry, or use keyword search.",
                        409,
                    )
            await report_stage(
                factory, claim, "embedding" if job.mode == "hybrid" else "lexical", SearchIndex
            )
            for start in range(0, len(pending), 8):
                batch = pending[start : start + 8]
                texts = [embedding_text(c.path, c.name, c.signature, c.content) for c in batch]
                parts = [await asyncio.to_thread(tokenizer, value) for value in texts]
                flat = [part for document in parts for part in document]
                vectors: list[list[float] | None] = [None] * len(batch)
                usage = 0
                if job.mode == "hybrid":
                    if provider is None:
                        raise AppError(
                            "embedding_configuration", "Embedding provider is disabled.", 409
                        )
                    await reserve(factory, claim, sum(map(len, flat)))
                    response = await provider.embed(flat)
                    if len(response.vectors) != len(flat) or response.input_tokens != sum(
                        map(len, flat)
                    ):
                        raise AppError(
                            "embedding_invalid", "Embedding batch did not match its inputs.", 502
                        )
                    usage = response.input_tokens
                    offset = 0
                    for position, document in enumerate(parts):
                        vectors[position] = aggregate(
                            response.vectors[offset : offset + len(document)],
                            list(map(len, document)),
                        )
                        offset += len(document)
                documents = [
                    SearchDocument(
                        search_index_id=job_id,
                        source_index_id=job.source_index_id,
                        chunk_id=chunk.id,
                        search_text=lexical_text(value),
                        input_hash=sha256(value.encode("utf-8")).hexdigest(),
                        token_count=sum(map(len, document)),
                        embedding=vector,
                    )
                    for chunk, value, document, vector in zip(
                        batch, texts, parts, vectors, strict=True
                    )
                ]
                await checkpoint(factory, claim, documents, usage)
            async with factory() as db:
                found = await db.scalar(
                    update(SearchIndex)
                    .where(lease_condition(claim, SearchIndex))
                    .values(
                        status="completed",
                        stage="completed",
                        finished_at=datetime.now(UTC),
                        lease_token=None,
                        lease_expires_at=None,
                    )
                    .returning(SearchIndex.id)
                )
                if found is None:
                    raise LeaseLost()
                await db.commit()
        logger.info(
            "search_prepared",
            extra={"job_id": str(job_id), "duration_ms": round((perf_counter() - started) * 1000)},
        )
    except LeaseLost:
        logger.info("search_claim_lost", extra={"job_id": str(job_id)})
    except Exception as exc:
        failure = (
            exc
            if isinstance(exc, AppError)
            else AppError(
                "search_timeout" if isinstance(exc, TimeoutError) else "search_preparation_failed",
                "Search preparation failed. Retry to resume completed batches.",
                503,
            )
        )
        logger.warning(
            "search_preparation_failed",
            extra={
                "job_id": str(job_id),
                "error_code": failure.code,
                "error_type": type(exc).__name__,
            },
        )
        async with factory() as db:
            await db.execute(
                update(SearchIndex)
                .where(lease_condition(claim, SearchIndex))
                .values(
                    status="failed",
                    stage="failed",
                    error_code=failure.code,
                    error_message=failure.message,
                    lease_token=None,
                    lease_expires_at=None,
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()
