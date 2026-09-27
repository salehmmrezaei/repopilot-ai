import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import configure_logging
from app.database.session import create_engine
from app.jobs.agent_dispatcher import dispatch_agents
from app.jobs.answer_dispatcher import dispatch_answers
from app.models import ImportJob, RepositoryIndex, SearchIndex

logger = logging.getLogger("repopilot.dispatcher")


async def dispatch_once(
    factory: async_sessionmaker[AsyncSession],
    publish: Callable[[UUID], Awaitable[None]],
    model: type[ImportJob] | type[RepositoryIndex] | type[SearchIndex] = ImportJob,
) -> None:
    now = datetime.now(UTC)
    async with factory() as db:
        await db.execute(
            update(model)
            .where(
                model.status == "running",
                model.lease_expires_at < now,
                model.attempts >= 3,
            )
            .values(
                status="failed",
                stage="failed",
                error_code="worker_lost",
                error_message="Worker stopped repeatedly. Retry the job.",
                finished_at=now,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        jobs = list(
            (
                await db.scalars(
                    select(model.id)
                    .where(
                        model.is_current.is_(True),
                        model.available_at <= now,
                        or_(
                            model.status == "queued",
                            and_(model.status == "running", model.lease_expires_at < now),
                        ),
                        or_(
                            model.last_dispatched_at.is_(None),
                            model.last_dispatched_at < now - timedelta(seconds=30),
                        ),
                    )
                    .order_by(model.created_at)
                    .limit(50)
                )
            ).all()
        )
        await db.commit()
    for job_id in jobs:
        try:
            await publish(job_id)
        except Exception as exc:
            logger.warning(
                "queue_publish_failed",
                extra={"job_id": str(job_id), "error_type": type(exc).__name__},
            )
            continue
        async with factory() as db:
            await db.execute(update(model).where(model.id == job_id).values(last_dispatched_at=now))
            await db.commit()


async def main() -> None:
    from app.jobs.celery_app import celery_app

    configure_logging()
    settings = Settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def publish(job_id: UUID) -> None:
        await asyncio.to_thread(
            celery_app.send_task, "repopilot.import_repository", args=[str(job_id)]
        )

    async def publish_index(job_id: UUID) -> None:
        await asyncio.to_thread(
            celery_app.send_task, "repopilot.index_repository", args=[str(job_id)]
        )

    async def publish_search(job_id: UUID) -> None:
        await asyncio.to_thread(
            celery_app.send_task, "repopilot.prepare_search", args=[str(job_id)]
        )

    async def publish_answer(run_id: UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, "repopilot.answer_run", args=[str(run_id)])

    async def publish_agent(run_id: UUID) -> None:
        await asyncio.to_thread(celery_app.send_task, "repopilot.agent_run", args=[str(run_id)])

    try:
        while True:
            try:
                await dispatch_once(factory, publish)
                await dispatch_once(factory, publish_index, RepositoryIndex)
                await dispatch_once(factory, publish_search, SearchIndex)
                await dispatch_answers(factory, publish_answer)
                await dispatch_agents(factory, publish_agent)
            except Exception as exc:
                logger.error("dispatch_cycle_failed", extra={"error_type": type(exc).__name__})
            await asyncio.sleep(settings.dispatcher_interval_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
