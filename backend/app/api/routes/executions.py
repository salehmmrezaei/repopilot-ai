from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db
from app.api.routes.indexes import Reader, Writer
from app.core.rate_limits import RateLimiter
from app.execution.contracts import ExecutionRequest
from app.schemas.execution import ExecutionList, ExecutionResponse
from app.schemas.index import IndexAction
from app.services.executions import ExecutionService

router = APIRouter(tags=["executions"])


def service(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> ExecutionService:
    return ExecutionService(
        db, request.app.state.settings, cast(RateLimiter, request.app.state.rate_limiter)
    )


Executions = Annotated[ExecutionService, Depends(service)]


@router.get("/agent-runs/{agent_id}/executions", response_model=ExecutionList)
async def list_runs(agent_id: UUID, identity: Reader, executions: Executions) -> ExecutionList:
    return await executions.list(identity.user.id, agent_id)


@router.post("/agent-runs/{agent_id}/executions", response_model=ExecutionResponse)
async def submit(
    agent_id: UUID,
    data: ExecutionRequest,
    identity: Writer,
    executions: Executions,
    response: Response,
) -> ExecutionResponse:
    result, created = await executions.submit(identity.user.id, agent_id, data)
    response.status_code = 202 if created else 200
    return result


@router.get("/executions/{run_id}", response_model=ExecutionResponse)
async def get(run_id: UUID, identity: Reader, executions: Executions) -> ExecutionResponse:
    return ExecutionResponse.model_validate(await executions.get(identity.user.id, run_id))


@router.post("/executions/{run_id}/cancel", response_model=ExecutionResponse)
async def cancel(
    run_id: UUID, data: IndexAction, identity: Writer, executions: Executions
) -> ExecutionResponse:
    return await executions.cancel(identity.user.id, run_id)
