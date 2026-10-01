from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ExecutionRun


async def dispatch_executions(
    factory: async_sessionmaker[AsyncSession], publish: Callable[[UUID], Awaitable[None]]
) -> None:
    now = datetime.now(UTC)
    async with factory() as db:
        await db.execute(
            update(ExecutionRun)
            .where(
                ((ExecutionRun.status == "running") & (ExecutionRun.lease_expires_at < now))
                | (
                    (ExecutionRun.status == "queued")
                    & (ExecutionRun.created_at < now - timedelta(hours=1))
                )
            )
            .values(
                status="failed",
                stage="expired",
                error="Execution worker or queue expired. No automatic execution retry.",
                finished_at=now,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        ids = list(
            (
                await db.scalars(
                    select(ExecutionRun.id)
                    .where(
                        ExecutionRun.status == "queued",
                        or_(
                            ExecutionRun.last_dispatched_at.is_(None),
                            ExecutionRun.last_dispatched_at < now - timedelta(seconds=30),
                        ),
                    )
                    .order_by(ExecutionRun.created_at)
                    .limit(20)
                )
            ).all()
        )
        await db.commit()
    for run_id in ids:
        try:
            await publish(run_id)
        except Exception:
            continue
        async with factory() as db:
            await db.execute(
                update(ExecutionRun)
                .where(ExecutionRun.id == run_id, ExecutionRun.status == "queued")
                .values(last_dispatched_at=now)
            )
            await db.commit()
