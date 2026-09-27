from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import AgentEvent, AgentRun, Conversation, Repository


async def owned(db: AsyncSession, user_id: UUID, run_id: UUID) -> AgentRun:
    run = await db.scalar(
        select(AgentRun)
        .join(Conversation)
        .join(Repository)
        .where(
            AgentRun.id == run_id, Conversation.user_id == user_id, Repository.user_id == user_id
        )
    )
    if run is None:
        raise AppError("not_found", "Investigation not found.", 404)
    return run


async def event(
    db: AsyncSession,
    run_id: UUID,
    kind: str,
    summary: str,
    tool: str | None = None,
    duration_ms: int | None = None,
) -> None:
    # Caller holds the run write lock. Sequence and state publish in the same transaction.
    sequence = (
        await db.scalar(select(func.max(AgentEvent.sequence)).where(AgentEvent.run_id == run_id))
        or 0
    ) + 1
    db.add(
        AgentEvent(
            run_id=run_id,
            sequence=sequence,
            kind=kind,
            summary=summary[:1000],
            tool=tool,
            duration_ms=duration_ms,
        )
    )
    await db.flush()
