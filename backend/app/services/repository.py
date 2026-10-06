from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.integrations.github.urls import parse_repository_url
from app.models import ImportJob, Repository, RepositoryFile, User
from app.repositories.repository import RepositoryStore
from app.schemas.repository import (
    FileContentResponse,
    FilePage,
    FileResponse,
    JobResponse,
    RepositoryResponse,
)


class RepositoryService:
    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db, self.store, self.limiter = db, RepositoryStore(db), limiter

    def response(self, repository: Repository, job: ImportJob) -> RepositoryResponse:
        return RepositoryResponse(
            id=repository.id,
            owner=repository.owner,
            name=repository.name,
            url=f"https://github.com/{repository.owner}/{repository.name}",
            github_repository_id=repository.github_repository_id,
            default_branch=repository.default_branch,
            last_commit_sha=repository.last_commit_sha,
            imported_at=repository.imported_at,
            job=JobResponse.model_validate(job),
        )

    async def list(self, user_id: UUID) -> list[RepositoryResponse]:
        return [self.response(repo, job) for repo, job in await self.store.list_owned(user_id)]

    async def get(self, user_id: UUID, repository_id: UUID) -> RepositoryResponse:
        return self.response(*await self.store.owned(user_id, repository_id))

    async def _quota(self, user_id: UUID) -> None:
        # Serialize create/retry quotas for this user in PostgreSQL.
        await self.db.execute(select(User.id).where(User.id == user_id).with_for_update())
        recent = await self.db.scalar(
            select(func.count())
            .select_from(ImportJob)
            .join(Repository, Repository.id == ImportJob.repository_id)
            .where(
                Repository.user_id == user_id,
                ImportJob.created_at > datetime.now(UTC) - timedelta(hours=1),
            )
        )
        if recent is not None and recent >= 5:
            raise AppError(
                "import_rate_limited", "Import limit reached. Try again later (five per hour).", 429
            )

    async def create(self, user_id: UUID, url: str) -> RepositoryResponse:
        await self.limiter.check([Limit("imports:user:" + str(user_id), 5, 3600)])
        await self._quota(user_id)
        count = await self.db.scalar(
            select(func.count()).select_from(Repository).where(Repository.user_id == user_id)
        )
        if count is not None and count >= 20:
            raise AppError(
                "repository_limit", "Limit of twenty repositories reached. Remove one first.", 409
            )
        owner, name = parse_repository_url(url)
        repo = Repository(user_id=user_id, source_key=f"{owner}/{name}", owner=owner, name=name)
        self.db.add(repo)
        try:
            await self.db.flush()
            job = ImportJob(repository_id=repo.id)
            self.db.add(job)
            await self.db.flush()
            result = self.response(repo, job)
            await self.db.commit()
            return result
        except IntegrityError:
            await self.db.rollback()
            raise AppError(
                "repository_exists", "This repository is already connected.", 409
            ) from None

    async def retry(self, user_id: UUID, repository_id: UUID) -> RepositoryResponse:
        await self.limiter.check([Limit("imports:user:" + str(user_id), 5, 3600)])
        await self._quota(user_id)
        repo, job = await self.store.owned(user_id, repository_id)
        changed = await self.db.scalar(
            update(ImportJob)
            .where(
                ImportJob.id == job.id,
                ImportJob.is_current.is_(True),
                ImportJob.status.in_(["failed", "cancelled"]),
            )
            .values(is_current=False)
            .returning(ImportJob.id)
        )
        if changed is None:
            raise AppError(
                "invalid_job_state", "Only failed or cancelled imports can be retried.", 409
            )
        new_job = ImportJob(repository_id=repo.id)
        self.db.add(new_job)
        await self.db.flush()
        result = self.response(repo, new_job)
        await self.db.commit()
        return result

    async def refresh(self, user_id: UUID, repository_id: UUID) -> RepositoryResponse:
        await self.limiter.check([Limit("imports:user:" + str(user_id), 5, 3600)])
        await self._quota(user_id)
        repo, job = await self.store.owned(user_id, repository_id)
        changed = await self.db.scalar(
            update(ImportJob)
            .where(
                ImportJob.id == job.id,
                ImportJob.is_current.is_(True),
                ImportJob.status == "completed",
            )
            .values(is_current=False)
            .returning(ImportJob.id)
        )
        if changed is None:
            raise AppError("invalid_job_state", "Refresh requires a completed import.", 409)
        new_job = ImportJob(repository_id=repo.id)
        self.db.add(new_job)
        await self.db.flush()
        result = self.response(repo, new_job)
        await self.db.commit()
        return result

    async def cancel(self, user_id: UUID, repository_id: UUID) -> None:
        _, job = await self.store.owned(user_id, repository_id)
        changed = await self.db.scalar(
            update(ImportJob)
            .where(ImportJob.id == job.id, ImportJob.status.in_(["queued", "running"]))
            .values(
                status="cancelled",
                stage="cancelled",
                finished_at=datetime.now(UTC),
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(ImportJob.id)
        )
        if changed is None:
            raise AppError("invalid_job_state", "This import is no longer active.", 409)
        await self.db.commit()

    async def remove(self, user_id: UUID, repository_id: UUID) -> None:
        await self.store.owned(user_id, repository_id)
        await self.db.execute(
            delete(Repository).where(Repository.id == repository_id, Repository.user_id == user_id)
        )
        await self.db.commit()

    async def files(self, user_id: UUID, repository_id: UUID, offset: int, limit: int) -> FilePage:
        _, job = await self.store.source(user_id, repository_id)
        rows = list(
            (
                await self.db.scalars(
                    select(RepositoryFile)
                    .where(
                        RepositoryFile.repository_id == repository_id,
                        RepositoryFile.import_job_id == job.id,
                    )
                    .order_by(RepositoryFile.path)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        )
        return FilePage(
            items=[FileResponse.model_validate(row) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    async def content(
        self, user_id: UUID, repository_id: UUID, file_id: UUID
    ) -> FileContentResponse:
        repo, job = await self.store.source(user_id, repository_id)
        file = (
            await self.db.scalars(
                select(RepositoryFile).where(
                    RepositoryFile.id == file_id,
                    RepositoryFile.repository_id == repository_id,
                    RepositoryFile.import_job_id == job.id,
                )
            )
        ).one_or_none()
        if file is None or repo.last_commit_sha is None:
            raise AppError("not_found", "File not found.", 404)
        return FileContentResponse(
            **FileResponse.model_validate(file).model_dump(),
            content=file.content,
            commit_sha=job.commit_sha or repo.last_commit_sha,
        )
