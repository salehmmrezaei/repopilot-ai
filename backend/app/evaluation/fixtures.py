import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.indexing.archive import LANGUAGES
from app.indexing.pipeline import PIPELINE_VERSION
from app.jobs.index_repository import run_index
from app.models import ImportJob, Repository, RepositoryFile, RepositoryIndex, User


def read_corpus(corpus: Path) -> list[tuple[str, str]]:
    return [
        (path.relative_to(corpus).as_posix(), path.read_text())
        for path in sorted(corpus.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".py", ".ts", ".tsx", ".mts", ".cts"}
    ]


async def seed(
    factory: async_sessionmaker[AsyncSession], corpus: Path, user_id: UUID
) -> tuple[UUID, str]:
    files = await asyncio.to_thread(read_corpus, corpus)
    digest = sha256("".join(path + "\0" + content for path, content in files).encode()).hexdigest()
    repo_id, source_id, index_id = uuid4(), uuid4(), uuid4()
    async with factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"{user_id}@evaluation.invalid",
                name="Evaluation fixture",
                password_hash="not-an-authentication-account",
            )
        )
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                user_id=user_id,
                source_key="evaluation/fixture",
                owner="evaluation",
                name="fixture",
                last_commit_sha=digest[:40],
            )
        )
        await db.flush()
        db.add(
            ImportJob(id=source_id, repository_id=repo_id, status="completed", stage="completed")
        )
        await db.flush()
        db.add_all(
            RepositoryFile(
                repository_id=repo_id,
                import_job_id=source_id,
                path=path,
                language=LANGUAGES[Path(path).suffix.lower()],
                content=content,
                size=len(content.encode()),
                content_hash=sha256(content.encode()).hexdigest(),
            )
            for path, content in files
        )
        db.add(
            RepositoryIndex(
                id=index_id,
                repository_id=repo_id,
                import_job_id=source_id,
                commit_sha=digest[:40],
                pipeline_version=PIPELINE_VERSION,
            )
        )
        await db.commit()
    await run_index(factory, index_id)
    async with factory() as db:
        status = await db.scalar(
            select(RepositoryIndex.status).where(RepositoryIndex.id == index_id)
        )
        if status != "completed":
            raise RuntimeError("Fixture indexing failed")
    return repo_id, digest
