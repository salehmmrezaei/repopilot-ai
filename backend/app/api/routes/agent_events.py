from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies.auth import SESSION_COOKIE
from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.services.agent_stream import AgentStream

router = APIRouter(tags=["investigations"])


@router.get("/agent-runs/{run_id}/events", response_class=StreamingResponse)
async def events(
    run_id: UUID,
    request: Request,
    after: Annotated[int, Query(ge=0, le=32)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=3)] = None,
) -> StreamingResponse:
    if request.headers.get("x-repopilot-request") != "1":
        raise AppError("csrf_failed", "Use the application to open this stream.", 403)
    if last_event_id is not None:
        if (
            not last_event_id.isascii()
            or not last_event_id.isdigit()
            or not 0 <= int(last_event_id) <= 32
        ):
            raise AppError("event_cursor_invalid", "Invalid event cursor.", 422)
        after = int(last_event_id)
    state = request.app.state
    stream = AgentStream(
        cast(async_sessionmaker[AsyncSession], state.session_factory),
        state.settings,
        state.dummy_password_hash,
    )
    raw = request.cookies.get(SESSION_COOKIE)
    user_id, _, _ = await stream.page(raw, run_id, after)
    await cast(RateLimiter, state.rate_limiter).check(
        [
            Limit("agent-stream:minute:" + str(user_id), 30, 60),
            Limit("agent-stream:hour:" + str(user_id), 300, 3600),
        ]
    )
    return StreamingResponse(
        stream.frames(raw, run_id, after, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
