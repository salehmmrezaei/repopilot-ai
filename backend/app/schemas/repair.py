from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent import AgentCallResponse, AgentResponse
from app.schemas.execution import ExecutionResponse


class RepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    profile: Literal["auto", "python", "javascript"] = "auto"
    max_revisions: int = Field(default=2, ge=1, le=2, strict=True)
    confirm_automatic_repair: Literal[True]


class RepairResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    request_key: UUID
    root_agent_id: UUID
    profile: str
    max_revisions: int
    revision: int
    current_agent_id: UUID
    current_execution_id: UUID | None
    status: Literal["running", "passed", "exhausted", "stopped", "cancelled"]
    reason: str | None
    created_at: datetime
    deadline: datetime
    finished_at: datetime | None


class RepairAttempt(BaseModel):
    agent: AgentResponse
    execution: ExecutionResponse | None
    calls: list[AgentCallResponse]


class RepairDetail(BaseModel):
    run: RepairResponse
    attempts: list[RepairAttempt]


class RepairList(BaseModel):
    enabled: bool
    items: list[RepairResponse]
