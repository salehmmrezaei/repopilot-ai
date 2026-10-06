from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.retrieval.text import query_terms, symbol_terms


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["keyword", "hybrid"] = "keyword"


class SearchRequest(PrepareRequest):
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=8, ge=1, le=20)
    context_token_budget: int = Field(default=6000, ge=100, le=12000)

    @field_validator("query")
    @classmethod
    def usable_query(cls, value: str) -> str:
        if not query_terms(value) and not symbol_terms(value):
            raise ValueError("query must contain searchable words or identifiers")
        return value.strip()


class SearchJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_index_id: UUID
    commit_sha: str
    mode: Literal["keyword", "hybrid"]
    provider_profile: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    stage: str
    documents_stored: int
    reserved_tokens: int
    input_tokens: int
    token_budget: int
    price_per_million: float
    error_code: str | None
    error_message: str | None


class SearchState(BaseModel):
    enabled_semantic: bool
    latest: SearchJobResponse | None
    keyword: SearchJobResponse | None
    hybrid: SearchJobResponse | None


class SearchHit(BaseModel):
    chunk_id: UUID
    file_id: UUID
    path: str
    language: str
    symbol: str | None
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    content: str
    score: float
    channel_ranks: dict[str, int]


class Evidence(BaseModel):
    citation_id: str
    chunk_id: UUID
    path: str
    commit_sha: str
    start_line: int
    end_line: int
    content: str


class SearchResponse(BaseModel):
    search_index_id: UUID
    source_index_id: UUID
    commit_sha: str
    mode: Literal["keyword", "hybrid"]
    pipeline_version: str
    provider_profile: str
    results: list[SearchHit]
    context: list[Evidence]
    context_tokens: int
    context_omitted: int
    query_tokens: int
    estimated_query_cost_usd: float
    duration_ms: float
