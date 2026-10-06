import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.indexing.errors import ImportFailure, LeaseLost
from app.indexing.pipeline import PIPELINE_VERSION, index_source
from app.jobs.state import Claim, claim_job, lease_condition, report_stage
from app.models import CodeChunk, CodeSymbol, RepositoryFile, RepositoryIndex

logger = logging.getLogger("repopilot.index")


async def publish_index(
    factory: async_sessionmaker[AsyncSession],
    claim: Claim,
    symbols: list[CodeSymbol],
    chunks: list[CodeChunk],
    diagnostics: list[dict[str, str]],
    file_count: int,
) -> None:
    async with factory() as db:
        found = await db.scalar(
            update(RepositoryIndex)
            .where(lease_condition(claim, RepositoryIndex))
            .values(
                status="completed",
                stage="completed",
                finished_at=datetime.now(UTC),
                lease_token=None,
                lease_expires_at=None,
                files_scanned=file_count,
                files_stored=file_count,
                symbol_count=len(symbols),
                chunk_count=len(chunks),
                diagnostics=diagnostics,
            )
            .returning(RepositoryIndex.id)
        )
        if found is None:
            raise LeaseLost()
        # Independent ORM mappers do not imply insertion order from bare foreign
        # keys. Publish symbols before dependent chunks, within this transaction.
        db.add_all(symbols)
        await db.flush()
        db.add_all(chunks)
        await db.commit()


async def run_index(
    factory: async_sessionmaker[AsyncSession], job_id: UUID, timeout_seconds: int = 90
) -> None:
    claim = await claim_job(factory, job_id, RepositoryIndex)
    if claim is None:
        return
    started = perf_counter()
    logger.info("index_started", extra={"job_id": str(job_id), "attempt": claim.attempt})
    try:
        async with asyncio.timeout(timeout_seconds):
            async with factory() as db:
                job = await db.get(RepositoryIndex, job_id)
                if job is None:
                    raise LeaseLost()
                if job.pipeline_version != PIPELINE_VERSION:
                    raise ImportFailure(
                        "index_version_mismatch",
                        "Restart workers using the current application image.",
                    )
                source_job = job.import_job_id
                files = list(
                    (
                        await db.scalars(
                            select(RepositoryFile)
                            .where(
                                RepositoryFile.import_job_id == source_job,
                                RepositoryFile.repository_id == claim.repository_id,
                            )
                            .order_by(RepositoryFile.path)
                        )
                    ).all()
                )
            symbols: list[CodeSymbol] = []
            chunks: list[CodeChunk] = []
            diagnostics: list[dict[str, str]] = []
            await report_stage(factory, claim, "parsing", RepositoryIndex)
            for file in files:
                result = await asyncio.to_thread(
                    index_source, file.content, file.language, file.path
                )
                if (
                    len(symbols) + len(result.symbols) > 10000
                    or len(chunks) + len(result.chunks) > 10000
                ):
                    raise ImportFailure(
                        "index_size_limit",
                        "Index exceeds 10000 symbols or chunks. Use a smaller repository.",
                    )
                if result.diagnostic:
                    diagnostics.append(
                        {"file_id": str(file.id), "path": file.path, "message": result.diagnostic}
                    )
                for ordinal, symbol in enumerate(result.symbols):
                    data = asdict(symbol)
                    data["parent_ordinal"] = data.pop("parent")
                    symbols.append(
                        CodeSymbol(
                            index_id=job_id,
                            import_job_id=source_job,
                            file_id=file.id,
                            ordinal=ordinal,
                            **data,
                        )
                    )
                chunks.extend(
                    CodeChunk(
                        index_id=job_id, import_job_id=source_job, file_id=file.id, **asdict(chunk)
                    )
                    for chunk in result.chunks
                )
            await report_stage(factory, claim, "storing", RepositoryIndex)
            await publish_index(factory, claim, symbols, chunks, diagnostics, len(files))
        logger.info(
            "index_completed",
            extra={
                "job_id": str(job_id),
                "symbol_count": len(symbols),
                "chunk_count": len(chunks),
                "duration_ms": round((perf_counter() - started) * 1000),
            },
        )
    except LeaseLost:
        logger.info("index_claim_lost", extra={"job_id": str(job_id)})
    except Exception as exc:
        failure = (
            exc
            if isinstance(exc, ImportFailure)
            else ImportFailure(
                "index_timeout" if isinstance(exc, TimeoutError) else "index_failed",
                "Indexing failed. Retry or inspect worker logs.",
            )
        )
        logger.warning(
            "index_failed",
            extra={
                "job_id": str(job_id),
                "error_code": failure.code,
                "error_type": type(exc).__name__,
            },
        )
        async with factory() as db:
            await db.execute(
                update(RepositoryIndex)
                .where(lease_condition(claim, RepositoryIndex))
                .values(
                    status="failed",
                    stage="failed",
                    error_code=failure.code,
                    error_message=failure.message,
                    finished_at=datetime.now(UTC),
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            await db.commit()
