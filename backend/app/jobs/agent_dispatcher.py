import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AgentRun
from app.repositories.agent import event

logger = logging.getLogger("repopilot.agent_dispatcher")


async def dispatch_agents(
    factory: async_sessionmaker[AsyncSession], publish: Callable[[UUID], Awaitable[None]]
) -> None:
    now = datetime.now(UTC)
    async with factory() as db:
        # Never reclaim running paid work: a crashed worker may already have spent money.
        expired = list(
            (
                await db.scalars(
                    update(AgentRun)
                    .where(AgentRun.status == "running", AgentRun.lease_expires_at < now)
                    .values(
                        status="failed",
                        finished_at=now,
                        lease_token=None,
                        lease_expires_at=None,
                        error_code="agent_worker_lost",
                        error_message=(
                            "Worker stopped; usage is unknown. No automatic retry was made."
                        ),
                    )
                    .returning(AgentRun.id)
                )
            ).all()
        )
        for run_id in expired:
            await event(
                db, run_id, "failed", "Investigation expired. No automatic model retry was made."
            )
        expired = list(
            (
                await db.scalars(
                    update(AgentRun)
                    .where(
                        AgentRun.status == "queued",
                        AgentRun.created_at <= now - timedelta(hours=1),
                    )
                    .values(
                        status="failed",
                        finished_at=now,
                        error_code="agent_queue_expired",
                        error_message="Run waited over an hour. Check worker availability.",
                    )
                    .returning(AgentRun.id)
                )
            ).all()
        )
        for run_id in expired:
            await event(
                db, run_id, "failed", "Investigation expired. No automatic model retry was made."
            )
        ids = list(
            (
                await db.scalars(
                    select(AgentRun.id)
                    .where(
                        AgentRun.status == "queued",
                        or_(
                            AgentRun.last_dispatched_at.is_(None),
                            AgentRun.last_dispatched_at < now - timedelta(seconds=30),
                        ),
                    )
                    .order_by(AgentRun.created_at)
                    .limit(50)
                )
            ).all()
        )
        await db.commit()
    for run_id in ids:
        try:
            await publish(run_id)
        except Exception as exc:
            logger.warning(
                "agent_publish_failed",
                extra={"job_id": str(run_id), "error_type": type(exc).__name__},
            )
            continue
        async with factory() as db:
            await db.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.status == "queued")
                .values(last_dispatched_at=now)
            )
            await db.commit()
