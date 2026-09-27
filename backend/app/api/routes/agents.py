from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db
from app.api.routes.indexes import Reader, Writer
from app.core.rate_limits import RateLimiter
from app.schemas.agent import AgentDetail, AgentList, AgentRequest, AgentResponse
from app.schemas.health import ErrorResponse
from app.schemas.index import IndexAction
from app.services.agents import AgentService

router = APIRouter(
    tags=["investigations"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)


def service(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AgentService:
    return AgentService(
        db, request.app.state.settings, cast(RateLimiter, request.app.state.rate_limiter)
    )


Agents = Annotated[AgentService, Depends(service)]


@router.get("/conversations/{conversation_id}/investigations", response_model=AgentList)
async def list_runs(conversation_id: UUID, identity: Reader, agents: Agents) -> AgentList:
    return await agents.list(identity.user.id, conversation_id)


@router.post(
    "/conversations/{conversation_id}/investigations",
    response_model=AgentResponse,
    responses={202: {"model": AgentResponse}},
)
async def submit(
    conversation_id: UUID, data: AgentRequest, identity: Writer, agents: Agents, response: Response
) -> AgentResponse:
    run, created = await agents.submit(identity.user.id, conversation_id, data)
    response.status_code = 202 if created else 200
    return run


@router.get("/agent-runs/{run_id}", response_model=AgentDetail)
async def get(run_id: UUID, identity: Reader, agents: Agents) -> AgentDetail:
    return await agents.get(identity.user.id, run_id)


@router.post("/agent-runs/{run_id}/cancel", response_model=AgentResponse)
async def cancel(
    run_id: UUID, data: IndexAction, identity: Writer, agents: Agents
) -> AgentResponse:
    return await agents.cancel(identity.user.id, run_id)
