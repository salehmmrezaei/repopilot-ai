import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.indexing.archive import ArchiveResult, read_archive
from app.indexing.errors import ImportFailure, LeaseLost
from app.integrations.github.client import GitHubClient, RepositorySource
from app.integrations.github.credentials import repository_token
from app.jobs.state import Claim, claim_job, lease_condition, report_stage
from app.models import ImportJob, Repository, RepositoryFile

logger = logging.getLogger("repopilot.import")


async def publish_result(
    factory: async_sessionmaker[AsyncSession],
    claim: Claim,
    source: RepositorySource,
    result: ArchiveResult,
) -> None:
    async with factory() as db:
        # Conditional update locks the job row and fences old/cancelled workers.
        found = await db.scalar(
            update(ImportJob)
            .where(lease_condition(claim))
            .values(
                status="completed",
                stage="completed",
                finished_at=datetime.now(UTC),
                lease_token=None,
                lease_expires_at=None,
                commit_sha=source.sha,
                files_scanned=result.scanned,
                files_skipped=result.skipped,
                files_stored=len(result.files),
            )
            .returning(ImportJob.id)
        )
        if found is None:
            raise LeaseLost()
        db.add_all(
            [
                RepositoryFile(
                    repository_id=claim.repository_id,
                    import_job_id=claim.job_id,
                    path=item.path,
                    language=item.language,
                    content=item.content,
                    size=item.size,
                    content_hash=item.content_hash,
                )
                for item in result.files
            ]
        )
        await db.execute(
            update(Repository)
            .where(Repository.id == claim.repository_id)
            .values(
                owner=source.owner,
                name=source.name,
                github_repository_id=source.github_id,
                default_branch=source.branch,
                last_commit_sha=source.sha,
                imported_at=datetime.now(UTC),
            )
        )
        await db.commit()


async def record_failure(
    factory: async_sessionmaker[AsyncSession], claim: Claim, failure: ImportFailure
) -> None:
    retry = failure.retryable and claim.attempt < 3
    async with factory() as db:
        await db.execute(
            update(ImportJob)
            .where(lease_condition(claim))
            .values(
                status="queued" if retry else "failed",
                stage="retry_wait" if retry else "failed",
                error_code=failure.code,
                error_message=failure.message,
                lease_token=None,
                lease_expires_at=None,
                last_dispatched_at=None,
                available_at=datetime.now(UTC) + timedelta(seconds=30 * claim.attempt),
                finished_at=None if retry else datetime.now(UTC),
            )
        )
        await db.commit()


async def run_import(
    factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    job_id: UUID,
    timeout_seconds: int,
    settings: "Settings | None" = None,
) -> None:
    claim = await claim_job(factory, job_id)
    if claim is None:
        return
    logger.info("import_started", extra={"job_id": str(job_id), "attempt": claim.attempt})
    try:
        async with asyncio.timeout(timeout_seconds):
            if settings is not None:
                async with factory() as db:
                    github.token = await repository_token(db, settings, claim.repository_id)
            source = await github.resolve(claim.owner, claim.name)
            await report_stage(factory, claim, "downloading")
            archive = await github.download(source)
            await report_stage(factory, claim, "validating")
            result = await asyncio.to_thread(read_archive, archive)
            await report_stage(factory, claim, "storing")
            await publish_result(factory, claim, source, result)
        logger.info(
            "import_completed", extra={"job_id": str(job_id), "files_stored": len(result.files)}
        )
    except LeaseLost:
        logger.info("import_claim_lost", extra={"job_id": str(job_id)})
    except TimeoutError:
        await record_failure(
            factory, claim, ImportFailure("import_timeout", "Repository import timed out.", True)
        )
    except ImportFailure as exc:
        await record_failure(factory, claim, exc)
        logger.warning("import_failed", extra={"job_id": str(job_id), "error_code": exc.code})
    except Exception as exc:
        logger.error(
            "import_internal_error", extra={"job_id": str(job_id), "error_type": type(exc).__name__}
        )
        await record_failure(
            factory,
            claim,
            ImportFailure(
                "import_failed", "Import failed unexpectedly. Retry or inspect the worker logs."
            ),
        )
