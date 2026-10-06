from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import ImportJob, Repository


class RepositoryStore:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def owned(self, user_id: UUID, repository_id: UUID) -> tuple[Repository, ImportJob]:
        row = (
            await self.db.execute(
                select(Repository, ImportJob)
                .join(ImportJob, ImportJob.repository_id == Repository.id)
                .where(
                    Repository.id == repository_id,
                    Repository.user_id == user_id,
                    ImportJob.is_current.is_(True),
                )
            )
        ).first()
        if row is None:
            raise AppError("not_found", "Repository not found.", 404)
        return row[0], row[1]

    async def list_owned(self, user_id: UUID) -> list[tuple[Repository, ImportJob]]:
        rows = (
            await self.db.execute(
                select(Repository, ImportJob)
                .join(ImportJob, ImportJob.repository_id == Repository.id)
                .where(Repository.user_id == user_id, ImportJob.is_current.is_(True))
                .order_by(Repository.created_at.desc())
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    async def source(self, user_id: UUID, repository_id: UUID) -> tuple[Repository, ImportJob]:
        repo, current = await self.owned(user_id, repository_id)
        completed = await self.db.scalar(
            select(ImportJob)
            .where(
                ImportJob.repository_id == repository_id,
                ImportJob.status == "completed",
            )
            .order_by(ImportJob.finished_at.desc(), ImportJob.created_at.desc(), ImportJob.id)
            .limit(1)
        )
        return repo, completed or current
