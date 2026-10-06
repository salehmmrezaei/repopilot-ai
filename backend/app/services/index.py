from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.indexing.pipeline import PIPELINE_VERSION
from app.models import Repository, RepositoryIndex
from app.repositories.index import IndexStore
from app.repositories.repository import RepositoryStore
from app.schemas.index import (
    ChunkResponse,
    IndexedFilePage,
    IndexResponse,
    IndexState,
    SymbolResponse,
)


class IndexService:
    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db, self.store, self.repositories, self.limiter = (
            db,
            IndexStore(db),
            RepositoryStore(db),
            limiter,
        )

    async def state(self, user_id: UUID, repository_id: UUID) -> IndexState:
        _, source = await self.repositories.owned(user_id, repository_id)
        latest = await self.store.latest(repository_id, source.id)
        active = await self.store.active(repository_id, source.id)
        return IndexState(
            latest=IndexResponse.model_validate(latest) if latest else None,
            active=IndexResponse.model_validate(active) if active else None,
            current_pipeline_version=PIPELINE_VERSION,
            rebuild_available=bool(active and active.pipeline_version != PIPELINE_VERSION),
        )

    async def start(self, user_id: UUID, repository_id: UUID) -> tuple[IndexResponse, bool]:
        # Ownership is part of the row lock predicate, not an unchecked frontend ID.
        await self.db.execute(
            select(Repository.id)
            .where(Repository.id == repository_id, Repository.user_id == user_id)
            .with_for_update()
        )
        repo, source = await self.repositories.owned(user_id, repository_id)
        if source.status != "completed" or repo.last_commit_sha is None:
            raise AppError("import_required", "Complete the repository import first.", 409)
        latest = await self.store.latest(repository_id, source.id)
        if latest and latest.status in {"queued", "running"}:
            raise AppError("index_active", "Indexing is already active.", 409)
        if latest and latest.status == "completed" and latest.pipeline_version == PIPELINE_VERSION:
            return IndexResponse.model_validate(latest), False
        await self.limiter.check([Limit("indexes:user:" + str(user_id), 5, 60)])
        if latest:
            latest.is_current = False
            await self.db.flush()
        job = RepositoryIndex(
            repository_id=repository_id,
            import_job_id=source.id,
            commit_sha=repo.last_commit_sha,
            pipeline_version=PIPELINE_VERSION,
        )
        self.db.add(job)
        await self.db.flush()
        result = IndexResponse.model_validate(job)
        await self.db.commit()
        return result, True

    async def cancel(self, user_id: UUID, repository_id: UUID) -> None:
        _, source = await self.repositories.owned(user_id, repository_id)
        found = await self.db.scalar(
            update(RepositoryIndex)
            .where(
                RepositoryIndex.repository_id == repository_id,
                RepositoryIndex.import_job_id == source.id,
                RepositoryIndex.is_current.is_(True),
                RepositoryIndex.status.in_(["queued", "running"]),
            )
            .values(
                status="cancelled",
                stage="cancelled",
                finished_at=datetime.now(UTC),
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(RepositoryIndex.id)
        )
        if found is None:
            raise AppError("index_inactive", "No active indexing job to cancel.", 409)
        await self.db.commit()

    async def file(
        self,
        user_id: UUID,
        repository_id: UUID,
        file_id: UUID,
        symbol_offset: int,
        chunk_offset: int,
    ) -> IndexedFilePage:
        _, source = await self.repositories.owned(user_id, repository_id)
        file = await self.store.file(file_id, source.id)
        if file is None:
            raise AppError("not_found", "File not found.", 404)
        index = await self.store.active(repository_id, source.id)
        if index is None:
            raise AppError("index_required", "Complete indexing first.", 409)
        symbols = await self.store.symbols(index.id, file_id, symbol_offset)
        chunks = await self.store.chunks(index.id, file_id, chunk_offset)
        return IndexedFilePage(
            index_id=index.id,
            file_id=file_id,
            path=file.path,
            language=file.language,
            commit_sha=index.commit_sha,
            symbols=[SymbolResponse.model_validate(s) for s in symbols[:50]],
            chunks=[ChunkResponse.model_validate(c) for c in chunks[:20]],
            next_symbol_offset=symbol_offset + 50 if len(symbols) > 50 else None,
            next_chunk_offset=chunk_offset + 20 if len(chunks) > 20 else None,
        )
