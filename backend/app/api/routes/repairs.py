from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db
from app.api.routes.indexes import Reader, Writer
from app.core.rate_limits import RateLimiter
from app.schemas.index import IndexAction
from app.schemas.repair import RepairDetail, RepairList, RepairRequest, RepairResponse
from app.services.repairs import RepairService

router = APIRouter(tags=["repairs"])


def service(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> RepairService:
    return RepairService(
        db, request.app.state.settings, cast(RateLimiter, request.app.state.rate_limiter)
    )


Repairs = Annotated[RepairService, Depends(service)]


@router.get("/agent-runs/{agent_id}/repairs", response_model=RepairList)
async def list_runs(agent_id: UUID, identity: Reader, repairs: Repairs) -> RepairList:
    return await repairs.list(identity.user.id, agent_id)


@router.post("/agent-runs/{agent_id}/repairs", response_model=RepairResponse)
async def submit(
    agent_id: UUID, data: RepairRequest, identity: Writer, repairs: Repairs, response: Response
) -> RepairResponse:
    result, created = await repairs.submit(identity.user.id, agent_id, data)
    response.status_code = 202 if created else 200
    return result


@router.get("/repairs/{run_id}", response_model=RepairDetail)
async def get(run_id: UUID, identity: Reader, repairs: Repairs) -> RepairDetail:
    return await repairs.detail(identity.user.id, run_id)


@router.post("/repairs/{run_id}/cancel", response_model=RepairResponse)
async def cancel(
    run_id: UUID, data: IndexAction, identity: Writer, repairs: Repairs
) -> RepairResponse:
    return await repairs.cancel(identity.user.id, run_id)
