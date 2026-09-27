"""Bounded orchestration independent from HTTP, workers and the model vendor."""

import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Protocol

from app.agents.contracts import AgentProvider, InvestigationResult, StepResult, ToolInput
from app.agents.provider import INSTRUCTIONS
from app.core.errors import AppError
from app.core.provider_usage import ProviderUsageError
from app.embeddings.tokens import count_tokens
from app.generation.context import validate_citations
from app.schemas.search import Evidence


class Tools(Protocol):
    async def execute(self, action: ToolInput) -> list[Evidence]: ...


class Observer(Protocol):
    async def begin(self, step: int) -> None: ...
    async def usage(self, step: int, input_tokens: int, output_tokens: int) -> None: ...
    async def emit(
        self, kind: str, summary: str, tool: str | None = None, duration_ms: int | None = None
    ) -> None: ...


async def investigate(
    question: str,
    provider: AgentProvider,
    execute: Callable[[ToolInput], Awaitable[list[Evidence]]],
    observer: Observer,
) -> InvestigationResult:
    evidence: list[Evidence] = []
    observations: list[dict[str, object]] = []
    seen: set[str] = set()
    plan: list[str] = []
    for step in range(1, 6):
        payload = json.dumps(
            {
                "task": question,
                "steps_remaining": 6 - step,
                "plan": plan,
                "observations": observations,
                "evidence": [e.model_dump(mode="json") for e in evidence],
            },
            ensure_ascii=False,
        )
        if len(payload.encode()) > 64 * 1024 or count_tokens(INSTRUCTIONS + payload) > 8000:
            raise AppError(
                "agent_context_limit", "Investigation context reached its safe limit.", 409
            )
        await observer.begin(step)  # durable unknown receipt and live lease check before every call
        try:
            raw = await provider.decide(payload)
        except ProviderUsageError as exc:
            await observer.usage(step, exc.input_tokens, exc.output_tokens)
            raise
        result = StepResult.model_validate(raw.model_dump())
        await observer.usage(step, result.input_tokens, result.output_tokens)
        decision = result.decision
        if step == 1:
            if not decision.plan:
                raise AppError(
                    "agent_plan_invalid", "Agent did not supply a public action plan.", 502
                )
            plan = decision.plan
            await observer.emit("plan", " | ".join(plan))
        if decision.answer is not None:
            validate_citations(decision.answer, evidence)
            return InvestigationResult(answer=decision.answer, evidence=evidence)
        if step == 5:
            raise AppError(
                "agent_step_limit",
                "Investigation reached its five-call limit without a final answer.",
                409,
            )
        action = decision.action
        assert action is not None
        fingerprint = action.model_dump_json()
        if fingerprint in seen:
            raise AppError(
                "agent_repeated_tool",
                "Agent repeated an identical tool request. Investigation stopped.",
                409,
            )
        seen.add(fingerprint)
        await observer.emit(
            "tool_started", f"{action.tool}: {action.path or action.query}", action.tool
        )
        started = perf_counter()
        try:
            found = await execute(action)
        except AppError as exc:
            if exc.status != 422:
                raise
            await observer.emit(
                "tool_failed", exc.message, action.tool, round((perf_counter() - started) * 1000)
            )
            observations.append({"tool": action.model_dump(), "error": exc.code})
            continue
        ids: list[str] = []
        for item in found:
            existing = next(
                (
                    e
                    for e in evidence
                    if (e.chunk_id, e.start_line, e.end_line)
                    == (item.chunk_id, item.start_line, item.end_line)
                ),
                None,
            )
            if existing:
                ids.append(existing.citation_id)
                continue
            if (
                sum(count_tokens(e.content) for e in evidence) + count_tokens(item.content) > 5000
                or len(evidence) >= 16
            ):
                continue
            item = item.model_copy(update={"citation_id": f"C{len(evidence) + 1}"})
            evidence.append(item)
            ids.append(item.citation_id)
        summary = f"Returned {len(ids)} evidence regions; files: " + ", ".join(
            sorted({e.path for e in evidence if e.citation_id in ids})
        )
        await observer.emit(
            "tool_completed", summary, action.tool, round((perf_counter() - started) * 1000)
        )
        observations.append(
            {
                "tool": action.model_dump(),
                "citation_ids": ids,
                "note": "Results bounded; references are lexical, not semantic.",
            }
        )
    raise AssertionError("Unreachable bounded loop")
