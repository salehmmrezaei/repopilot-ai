from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import InvestigationResult


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    mode: Literal["investigate", "propose"] = "investigate"
    feedback_execution_id: UUID | None = None
    question: str = Field(min_length=1, max_length=512, pattern=r"\S")


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    request_key: UUID
    question: str
    mode: Literal["investigate", "propose"]
    source_index_id: UUID
    commit_sha: str
    model: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    result: InvestigationResult | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AgentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sequence: int
    kind: str
    summary: str
    tool: str | None
    duration_ms: int | None
    created_at: datetime


class AgentCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    step: int
    input_rate: Decimal
    output_rate: Decimal
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    created_at: datetime
    finished_at: datetime | None


class AgentDetail(BaseModel):
    run: AgentResponse
    events: list[AgentEventResponse]
    calls: list[AgentCallResponse]


class AgentList(BaseModel):
    enabled: bool
    items: list[AgentResponse]
