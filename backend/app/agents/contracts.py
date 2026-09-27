from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.generation.contracts import AnswerDraft
from app.schemas.search import Evidence

ToolName = Literal["search_code", "read_file", "find_symbol", "find_references"]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: ToolName
    query: str | None = Field(max_length=512)
    path: str | None = Field(max_length=512)
    start_line: int | None = Field(ge=1, le=1000000)

    @model_validator(mode="after")
    def arguments(self) -> "ToolInput":
        if self.tool == "read_file":
            if self.query is not None or not self.path or self.start_line is None:
                raise ValueError("read_file requires path and start_line only")
            if (
                self.path.startswith("/")
                or "\\" in self.path
                or any(p in {"", ".", ".."} for p in self.path.split("/"))
            ):
                raise ValueError("Expected repository-relative path")
        elif (
            not self.query
            or not self.query.strip()
            or self.path is not None
            or self.start_line is not None
        ):
            raise ValueError("Search tools require query only")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan: list[str] = Field(max_length=5)
    action: ToolInput | None
    answer: AnswerDraft | None

    @model_validator(mode="after")
    def exactly_one(self) -> "Decision":
        if (self.action is None) == (self.answer is None):
            raise ValueError("Choose exactly one action or final answer")
        if any(not x.strip() or len(x) > 180 for x in self.plan):
            raise ValueError("Plan contains only short public action labels")
        return self


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Decision
    input_tokens: int = Field(ge=0, le=100000)
    output_tokens: int = Field(ge=0, le=2000)


class InvestigationResult(BaseModel):
    answer: AnswerDraft
    evidence: list[Evidence]


class AgentProvider(Protocol):
    async def decide(self, payload: str) -> StepResult: ...
