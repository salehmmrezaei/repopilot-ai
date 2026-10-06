from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.embeddings.provider import PROFILE
from app.models import Repository, RepositoryIndex, SearchIndex
from app.repositories.index import IndexStore
from app.repositories.repository import RepositoryStore
from app.repositories.search import SearchStore
from app.retrieval.text import VERSION
from app.schemas.search import SearchJobResponse, SearchState


class PreparationService:
    def __init__(self, db: AsyncSession, limiter: RateLimiter, settings: Settings) -> None:
        self.db, self.limiter, self.settings = db, limiter, settings
        self.store = SearchStore(db)

    async def source(self, user_id: UUID, repository_id: UUID) -> RepositoryIndex:
        _, imported = await RepositoryStore(self.db).source(user_id, repository_id)
        source = await IndexStore(self.db).active(repository_id, imported.id)
        if source is None:
            raise AppError("index_required", "Build the static source index first.", 409)
        return source

    async def state(self, user_id: UUID, repository_id: UUID) -> SearchState:
        source = await self.source(user_id, repository_id)
        latest = await self.store.latest(repository_id)
        hybrid = await self.store.active(repository_id, source.id, "hybrid")
        keyword = await self.store.active(repository_id, source.id, "keyword") or hybrid
        return SearchState(
            enabled_semantic=self.settings.embeddings_enabled,
            latest=SearchJobResponse.model_validate(latest) if latest else None,
            keyword=SearchJobResponse.model_validate(keyword) if keyword else None,
            hybrid=SearchJobResponse.model_validate(hybrid) if hybrid else None,
        )

    async def start(
        self, user_id: UUID, repository_id: UUID, mode: str
    ) -> tuple[SearchJobResponse, bool]:
        await self.db.execute(
            select(Repository.id)
            .where(Repository.id == repository_id, Repository.user_id == user_id)
            .with_for_update()
        )
        source = await self.source(user_id, repository_id)
        if mode == "hybrid" and not self.settings.embeddings_enabled:
            raise AppError(
                "embeddings_disabled",
                "Enable embeddings in server configuration to prepare semantic search.",
                409,
            )
        profile = PROFILE if mode == "hybrid" else "none"
        latest = await self.store.latest(repository_id)
        if latest and latest.status in {"queued", "running"}:
            raise AppError("search_active", "Search preparation is already active.", 409)
        existing = await self.store.active(repository_id, source.id, mode)
        if existing and existing.provider_profile == profile:
            return SearchJobResponse.model_validate(existing), False
        limits = [Limit("search-build:user:" + str(user_id), 5, 60)]
        if mode == "hybrid":
            limits.append(Limit("semantic-build:user:" + str(user_id), 3, 86400))
        await self.limiter.check(limits)
        if latest and (
            latest.source_index_id,
            latest.mode,
            latest.provider_profile,
            latest.pipeline_version,
        ) == (source.id, mode, profile, VERSION):
            # Explicit retry resumes persisted batches and preserves all usage reservations.
            latest.status, latest.stage = "queued", "queued"
            latest.attempts = 0
            latest.error_code = latest.error_message = None
            latest.lease_token = latest.lease_expires_at = latest.finished_at = (
                latest.last_dispatched_at
            ) = None
            latest.available_at = datetime.now(UTC)
            latest.token_budget = max(latest.token_budget, self.settings.embedding_token_budget)
            job = latest
        else:
            if latest:
                latest.is_current = False
                await self.db.flush()
            job = SearchIndex(
                repository_id=repository_id,
                source_index_id=source.id,
                commit_sha=source.commit_sha,
                pipeline_version=VERSION,
                mode=mode,
                provider_profile=profile,
                token_budget=self.settings.embedding_token_budget,
                price_per_million=self.settings.embedding_price_per_million
                if mode == "hybrid"
                else 0,
            )
            self.db.add(job)
        await self.db.flush()
        result = SearchJobResponse.model_validate(job)
        await self.db.commit()
        return result, True

    async def cancel(self, user_id: UUID, repository_id: UUID) -> None:
        await RepositoryStore(self.db).owned(user_id, repository_id)
        found = await self.db.scalar(
            update(SearchIndex)
            .where(
                SearchIndex.repository_id == repository_id,
                SearchIndex.is_current.is_(True),
                SearchIndex.status.in_(["queued", "running"]),
            )
            .values(
                status="cancelled",
                stage="cancelled",
                lease_token=None,
                lease_expires_at=None,
                finished_at=datetime.now(UTC),
            )
            .returning(SearchIndex.id)
        )
        if found is None:
            raise AppError("search_inactive", "No active search preparation to cancel.", 409)
        await self.db.commit()
