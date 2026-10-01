from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.proposals import VerifiedProposal


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    archive_b64: str = Field(max_length=14 * 1024 * 1024)
    proposal: VerifiedProposal
    profile: Literal["auto", "python", "javascript"] = "auto"


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["passed", "failed", "timeout", "output_limit", "resource_limit", "error"]
    exit_code: int | None
    log: str = Field(max_length=32768)
    truncated: bool
    duration_ms: int


class SandboxResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["running", "completed", "failed", "cancelled"]
    request_hash: str
    image_id: str
    archive_sha256: str | None = None
    profile: Literal["python", "javascript"] | None = None
    command: list[str] = Field(default_factory=list)
    preparation: CommandResult | None = None
    baseline: CommandResult | None = None
    patched: CommandResult | None = None
    error: str | None = None

    @model_validator(mode="after")
    def completed_phases(self) -> "SandboxResult":
        if self.status == "completed" and (
            self.preparation is None
            or self.preparation.status != "passed"
            or self.baseline is None
            or self.patched is None
            or self.profile is None
            or not self.command
            or self.archive_sha256 is None
        ):
            raise ValueError("Completed executions require preparation and both test receipts")
        return self


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    profile: Literal["auto", "python", "javascript"] = "auto"
    confirm_execution: Literal[True]
