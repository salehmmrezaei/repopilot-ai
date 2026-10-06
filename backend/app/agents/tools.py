"""Read only the pinned database snapshot, never the host filesystem."""

from uuid import UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import ToolInput
from app.core.errors import AppError
from app.models import CodeChunk, CodeSymbol, RepositoryFile, RepositoryIndex
from app.repositories.repository import RepositoryStore
from app.schemas.search import Evidence, SearchRequest
from app.services.search import SearchService


class RepositoryTools:
    def __init__(
        self,
        db: AsyncSession,
        search: SearchService,
        user_id: UUID,
        repository_id: UUID,
        index_id: UUID,
    ) -> None:
        self.db, self.search, self.user_id, self.repository_id, self.index_id = (
            db,
            search,
            user_id,
            repository_id,
            index_id,
        )

    async def execute(self, action: ToolInput) -> list[Evidence]:
        action = ToolInput.model_validate(action.model_dump())
        await RepositoryStore(self.db).owned(self.user_id, self.repository_id)
        source = await self.db.scalar(
            select(RepositoryIndex).where(
                RepositoryIndex.id == self.index_id,
                RepositoryIndex.repository_id == self.repository_id,
                RepositoryIndex.status == "completed",
            )
        )
        if source is None:
            raise AppError(
                "agent_source_missing", "Pinned source index is no longer available.", 409
            )
        if action.tool == "search_code":
            try:
                request = SearchRequest(
                    query=action.query or "", top_k=4, context_token_budget=1800
                )
            except ValidationError:
                raise AppError(
                    "agent_tool_input", "Search requires searchable words or identifiers.", 422
                ) from None
            result = await self.search.search(
                self.user_id,
                self.repository_id,
                request,
            )
            if result.source_index_id != self.index_id:
                raise AppError(
                    "agent_source_changed", "Source index changed; submit a new investigation.", 409
                )
            return result.context
        if action.tool == "read_file":
            file = await self.db.scalar(
                select(RepositoryFile).where(
                    RepositoryFile.repository_id == self.repository_id,
                    RepositoryFile.import_job_id == source.import_job_id,
                    RepositoryFile.path == action.path,
                )
            )
            if file is None:
                return []
            start = action.start_line or 1
            lines = file.content.splitlines(keepends=True)
            selected: list[str] = []
            for line in lines[start - 1 : start + 79]:
                if len(("".join(selected) + line).encode()) > 6000:
                    break
                selected.append(line)
            if not selected:
                return []
            return [
                Evidence(
                    citation_id="",
                    chunk_id=uuid5(file.id, f"{start}:{len(selected)}"),
                    path=file.path,
                    commit_sha=source.commit_sha,
                    start_line=start,
                    end_line=start + len(selected) - 1,
                    content="".join(selected),
                )
            ]
        query = action.query or ""
        statement = (
            select(CodeChunk, RepositoryFile.path)
            .join(RepositoryFile, CodeChunk.file_id == RepositoryFile.id)
            .where(
                CodeChunk.index_id == self.index_id,
                RepositoryFile.repository_id == self.repository_id,
            )
        )
        if action.tool == "find_symbol":
            matching_symbol = (
                select(CodeSymbol.id)
                .where(
                    CodeSymbol.index_id == self.index_id,
                    CodeSymbol.file_id == CodeChunk.file_id,
                    (CodeSymbol.name == query) | (CodeSymbol.qualified_name == query),
                    or_(
                        CodeSymbol.ordinal == CodeChunk.symbol_ordinal,
                        CodeSymbol.start_line.between(CodeChunk.start_line, CodeChunk.end_line),
                    ),
                )
                .exists()
            )
            statement = statement.where(matching_symbol)
        else:
            if len(query) > 100 or not query.removeprefix("#").replace("$", "_").isidentifier():
                raise AppError("agent_tool_input", "References require one source identifier.", 422)
            statement = statement.where(CodeChunk.content.contains(query, autoescape=True))
        rows = (
            await self.db.execute(
                statement.order_by(RepositoryFile.path, CodeChunk.start_line).limit(4)
            )
        ).all()
        return [
            Evidence(
                citation_id="",
                chunk_id=chunk.id,
                path=path,
                commit_sha=source.commit_sha,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
            )
            for chunk, path in rows
            if len(chunk.content.encode()) <= 6000
        ]
