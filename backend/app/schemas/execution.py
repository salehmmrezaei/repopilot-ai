from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.execution.contracts import SandboxResult


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    agent_run_id: UUID
    request_key: UUID
    profile: str
    patch_sha256: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    stage: str
    result: SandboxResult | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionList(BaseModel):
    enabled: bool
    items: list[ExecutionResponse]
