from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CodeChunk, CodeSymbol, RepositoryFile, RepositoryIndex


class IndexStore:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def latest(self, repository_id: UUID, source_id: UUID) -> RepositoryIndex | None:
        return (
            await self.db.scalars(
                select(RepositoryIndex).where(
                    RepositoryIndex.repository_id == repository_id,
                    RepositoryIndex.import_job_id == source_id,
                    RepositoryIndex.is_current.is_(True),
                )
            )
        ).one_or_none()

    async def active(self, repository_id: UUID, source_id: UUID) -> RepositoryIndex | None:
        return (
            await self.db.scalars(
                select(RepositoryIndex)
                .where(
                    RepositoryIndex.repository_id == repository_id,
                    RepositoryIndex.import_job_id == source_id,
                    RepositoryIndex.status == "completed",
                )
                .order_by(
                    RepositoryIndex.is_current.desc(),
                    RepositoryIndex.finished_at.desc().nullslast(),
                    RepositoryIndex.created_at.desc(),
                    RepositoryIndex.id,
                )
                .limit(1)
            )
        ).one_or_none()

    async def file(self, file_id: UUID, source_id: UUID) -> RepositoryFile | None:
        return (
            await self.db.scalars(
                select(RepositoryFile).where(
                    RepositoryFile.id == file_id, RepositoryFile.import_job_id == source_id
                )
            )
        ).one_or_none()

    async def symbols(self, index_id: UUID, file_id: UUID, offset: int) -> list[CodeSymbol]:
        return list(
            (
                await self.db.scalars(
                    select(CodeSymbol)
                    .where(CodeSymbol.index_id == index_id, CodeSymbol.file_id == file_id)
                    .order_by(CodeSymbol.ordinal)
                    .offset(offset)
                    .limit(51)
                )
            ).all()
        )

    async def chunks(self, index_id: UUID, file_id: UUID, offset: int) -> list[CodeChunk]:
        return list(
            (
                await self.db.scalars(
                    select(CodeChunk)
                    .where(CodeChunk.index_id == index_id, CodeChunk.file_id == file_id)
                    .order_by(CodeChunk.ordinal)
                    .offset(offset)
                    .limit(21)
                )
            ).all()
        )
