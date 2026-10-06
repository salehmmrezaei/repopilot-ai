from typing import Annotated, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db
from app.api.routes.indexes import Reader, Writer
from app.core.rate_limits import RateLimiter
from app.generation.contracts import AnswerProvider
from app.integrations.github.client import GitHubClient
from app.integrations.github.credentials import repository_token
from app.models import PullReview
from app.repositories.repository import RepositoryStore
from app.services.reviews import get_review, review_pull

router = APIRouter(prefix="/repositories/{repository_id}/reviews", tags=["PR review"])
DB = Annotated[AsyncSession, Depends(get_db)]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pull_number: int = Field(ge=1, le=2147483647)
    request_key: UUID


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    pull_number: int
    status: str
    base_sha: str | None
    head_sha: str | None
    result: dict[str, object] | None
    error_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None


@router.get("", response_model=list[ReviewResponse])
async def list_reviews(repository_id: UUID, identity: Reader, db: DB) -> list[PullReview]:
    await RepositoryStore(db).owned(identity.user.id, repository_id)
    return list(
        await db.scalars(
            select(PullReview)
            .where(PullReview.repository_id == repository_id)
            .order_by(PullReview.created_at.desc())
            .limit(20)
        )
    )


@router.get("/{review_id}", response_model=ReviewResponse)
async def read_review(repository_id: UUID, review_id: UUID, identity: Reader, db: DB) -> PullReview:
    return await get_review(db, identity.user.id, repository_id, review_id)


@router.post("", response_model=ReviewResponse)
async def create_review(
    repository_id: UUID, data: ReviewRequest, identity: Writer, request: Request, db: DB
) -> PullReview:
    await RepositoryStore(db).owned(identity.user.id, repository_id)
    settings = request.app.state.settings
    token = await repository_token(db, settings, repository_id)
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        return await review_pull(
            db,
            identity.user.id,
            repository_id,
            data.pull_number,
            data.request_key,
            settings,
            cast(RateLimiter, request.app.state.rate_limiter),
            GitHubClient(client, settings.import_download_bytes, token),
            cast(AnswerProvider | None, request.app.state.answer_provider),
        )
