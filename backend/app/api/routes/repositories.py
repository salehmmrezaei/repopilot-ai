from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import current_identity, get_db, require_csrf
from app.core.rate_limits import RateLimiter
from app.schemas.health import ErrorResponse
from app.schemas.repository import (
    FileContentResponse,
    FilePage,
    RepositoryCreate,
    RepositoryResponse,
)
from app.services.auth import Identity
from app.services.repository import RepositoryService

router = APIRouter(
    prefix="/repositories",
    tags=["repositories"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 503)},
)


def get_repository_service(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> RepositoryService:
    return RepositoryService(db, cast(RateLimiter, request.app.state.rate_limiter))


Service = Annotated[RepositoryService, Depends(get_repository_service)]
Reader = Annotated[Identity, Depends(current_identity)]
Writer = Annotated[Identity, Depends(require_csrf)]


@router.get("", response_model=list[RepositoryResponse])
async def list_repositories(identity: Reader, service: Service) -> list[RepositoryResponse]:
    return await service.list(identity.user.id)


@router.post("", status_code=202, response_model=RepositoryResponse)
async def create_repository(
    data: RepositoryCreate, identity: Writer, service: Service
) -> RepositoryResponse:
    return await service.create(identity.user.id, data.url)


@router.get("/{repository_id}", response_model=RepositoryResponse)
async def get_repository(
    repository_id: UUID, identity: Reader, service: Service
) -> RepositoryResponse:
    return await service.get(identity.user.id, repository_id)


@router.post("/{repository_id}/retry", status_code=202, response_model=RepositoryResponse)
async def retry_import(
    repository_id: UUID, identity: Writer, service: Service
) -> RepositoryResponse:
    return await service.retry(identity.user.id, repository_id)


@router.post("/{repository_id}/cancel", status_code=204)
async def cancel_import(repository_id: UUID, identity: Writer, service: Service) -> Response:
    await service.cancel(identity.user.id, repository_id)
    return Response(status_code=204)


@router.delete("/{repository_id}", status_code=204)
async def delete_repository(repository_id: UUID, identity: Writer, service: Service) -> Response:
    await service.remove(identity.user.id, repository_id)
    return Response(status_code=204)


@router.get("/{repository_id}/files", response_model=FilePage)
async def list_files(
    repository_id: UUID,
    identity: Reader,
    service: Service,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> FilePage:
    return await service.files(identity.user.id, repository_id, offset, limit)


@router.get("/{repository_id}/files/{file_id}", response_model=FileContentResponse)
async def read_file(
    repository_id: UUID, file_id: UUID, identity: Reader, service: Service
) -> FileContentResponse:
    return await service.content(identity.user.id, repository_id, file_id)


@router.post("/{repository_id}/refresh", status_code=202, response_model=RepositoryResponse)
async def refresh_repository(
    repository_id: UUID, identity: Writer, service: Service
) -> RepositoryResponse:
    return await service.refresh(identity.user.id, repository_id)
