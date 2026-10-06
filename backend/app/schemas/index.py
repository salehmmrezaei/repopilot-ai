from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.repository import JobResponse


class Diagnostic(BaseModel):
    file_id: str
    path: str
    message: str


class IndexResponse(JobResponse):
    commit_sha: str
    pipeline_version: str
    symbol_count: int
    chunk_count: int
    diagnostics: list[Diagnostic]


class IndexState(BaseModel):
    latest: IndexResponse | None
    active: IndexResponse | None
    current_pipeline_version: str
    rebuild_available: bool


class SymbolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    file_id: UUID
    ordinal: int
    name: str
    qualified_name: str
    kind: Literal[
        "class",
        "function",
        "method",
        "import",
        "variable",
        "interface",
        "type",
        "enum",
        "namespace",
        "property",
    ]
    signature: str | None
    docstring: str | None
    parent_ordinal: int | None
    start_line: int
    end_line: int


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    file_id: UUID
    ordinal: int
    symbol_ordinal: int | None
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    kind: Literal["symbol", "module", "text", "fallback"]
    content: str
    content_hash: str


class IndexedFilePage(BaseModel):
    index_id: UUID
    file_id: UUID
    path: str
    language: str
    commit_sha: str
    symbols: list[SymbolResponse]
    chunks: list[ChunkResponse]
    next_symbol_offset: int | None
    next_chunk_offset: int | None


class IndexAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
