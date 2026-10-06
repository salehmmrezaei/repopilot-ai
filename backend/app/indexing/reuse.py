"""Reuse only immutable, same-repository, same-pipeline file snapshots."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.indexing.chunks import Chunk
from app.indexing.parser import Symbol
from app.indexing.pipeline import IndexedSource
from app.models import CodeChunk, CodeSymbol, RepositoryFile, RepositoryIndex


async def reusable_sources(
    db: AsyncSession, job: RepositoryIndex
) -> dict[tuple[str, str, str], IndexedSource]:
    previous = await db.scalar(
        select(RepositoryIndex)
        .where(
            RepositoryIndex.repository_id == job.repository_id,
            RepositoryIndex.id != job.id,
            RepositoryIndex.status == "completed",
            RepositoryIndex.pipeline_version == job.pipeline_version,
        )
        .order_by(RepositoryIndex.finished_at.desc(), RepositoryIndex.id)
        .limit(1)
    )
    if previous is None:
        return {}
    files = list(
        await db.scalars(
            select(RepositoryFile).where(
                RepositoryFile.import_job_id == previous.import_job_id,
                RepositoryFile.repository_id == job.repository_id,
            )
        )
    )
    symbols: dict[object, list[Symbol]] = {}
    chunks: dict[object, list[Chunk]] = {}
    for s in await db.scalars(
        select(CodeSymbol).where(CodeSymbol.index_id == previous.id).order_by(CodeSymbol.ordinal)
    ):
        symbols.setdefault(s.file_id, []).append(
            Symbol(
                s.name,
                s.qualified_name,
                s.kind,
                s.parent_ordinal,
                s.start_line,
                s.end_line,
                s.signature,
                s.docstring,
            )
        )
    for c in await db.scalars(
        select(CodeChunk).where(CodeChunk.index_id == previous.id).order_by(CodeChunk.ordinal)
    ):
        chunks.setdefault(c.file_id, []).append(
            Chunk(
                c.ordinal,
                c.start_offset,
                c.end_offset,
                c.start_line,
                c.end_line,
                c.symbol_ordinal,
                c.kind,
                c.content,
                c.content_hash,
            )
        )
    diagnostics = {d["path"]: d["message"] for d in previous.diagnostics}
    return {
        (f.path, f.language, f.content): IndexedSource(
            symbols.get(f.id, []),
            chunks.get(f.id, []),
            diagnostics.get(f.path),
        )
        for f in files
    }
