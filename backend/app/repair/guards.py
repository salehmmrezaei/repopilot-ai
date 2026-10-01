from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.repair import RepairRun


async def require_active(
    db: AsyncSession, settings: Settings, kind: Literal["agent", "execution"], child_id: UUID
) -> None:
    column = RepairRun.current_agent_id if kind == "agent" else RepairRun.current_execution_id
    # Do not lock the parent after a child lock: controller/cancel lock parent first.
    parent = await db.scalar(
        select(RepairRun).where(column == child_id).order_by(RepairRun.created_at.desc()).limit(1)
    )
    if parent is not None and (
        parent.status != "running"
        or parent.deadline.replace(tzinfo=UTC) <= datetime.now(UTC)
        or not settings.repairs_enabled
        or not settings.agents_enabled
        or not settings.executions_enabled
    ):
        raise AppError("repair_inactive", "Repair authorization expired or was cancelled.", 409)
