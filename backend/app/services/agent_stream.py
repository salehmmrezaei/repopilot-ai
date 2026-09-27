import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.models import AgentEvent
from app.repositories.agent import owned
from app.schemas.agent import AgentEventResponse
from app.services.auth import AuthService


class AgentStream:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], settings: Settings, dummy_hash: str
    ) -> None:
        self.factory, self.settings, self.dummy_hash = factory, settings, dummy_hash

    async def page(
        self, raw: str | None, run_id: UUID, cursor: int
    ) -> tuple[UUID, list[AgentEventResponse], bool]:
        async with self.factory() as db:
            identity = await AuthService(
                db, self.settings.session_lifetime_hours, self.dummy_hash
            ).authenticate(raw)
            run = await owned(db, identity.user.id, run_id)
            rows = list(
                (
                    await db.scalars(
                        select(AgentEvent)
                        .where(AgentEvent.run_id == run_id)
                        .order_by(AgentEvent.sequence)
                    )
                ).all()
            )
            if cursor > (rows[-1].sequence if rows else 0):
                raise AppError(
                    "event_cursor_invalid", "Cursor is ahead of this investigation.", 409
                )
            return (
                identity.user.id,
                [AgentEventResponse.model_validate(row) for row in rows if row.sequence > cursor],
                run.status in {"completed", "failed", "cancelled"},
            )

    async def frames(
        self,
        raw: str | None,
        run_id: UUID,
        cursor: int,
        disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[str]:
        try:
            for tick in range(25):
                if await disconnected():
                    return
                _, events, terminal = await self.page(raw, run_id, cursor)
                for event in events:
                    cursor = event.sequence
                    yield f"id: {cursor}\nevent: agent.action\ndata: {event.model_dump_json()}\n\n"
                if terminal:
                    yield "event: stream.end\ndata: {}\n\n"
                    return
                if tick % 5 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)
            yield "event: stream.reconnect\ndata: {}\n\n"
        except Exception as exc:
            logging.getLogger("repopilot.agent_stream").warning(
                "agent_stream_failed",
                extra={"job_id": str(run_id), "error_type": type(exc).__name__},
            )
            code = (
                "unauthenticated"
                if isinstance(exc, AppError) and exc.status == 401
                else "stream_unavailable"
            )
            yield f'event: stream.error\ndata: {{"code":"{code}"}}\n\n'
