"""Short transactional transitions; existing workers perform all paid/execution work."""

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limits import RateLimiter
from app.models.repair import RepairRun
from app.services.repairs import RepairService

logger = logging.getLogger("repopilot.repairs")


async def advance_repairs(
    factory: async_sessionmaker[AsyncSession], settings: Settings, limiter: RateLimiter
) -> None:
    async with factory() as db:
        ids = list(
            await db.scalars(
                select(RepairRun.id)
                .where(RepairRun.status == "running")
                .order_by(RepairRun.created_at)
                .limit(50)
            )
        )
    for run_id in ids:
        async with factory() as db:
            run = await db.scalar(
                select(RepairRun)
                .where(RepairRun.id == run_id, RepairRun.status == "running")
                .with_for_update(skip_locked=True)
            )
            if run is None:
                continue
            service = RepairService(db, settings, limiter)
            try:
                async with db.begin_nested():
                    await service.advance(run)
            except (AppError, ValidationError) as exc:
                await db.refresh(run)
                await service.stop(
                    run, "stopped", exc.code if isinstance(exc, AppError) else "invalid_receipt"
                )
            await db.commit()
