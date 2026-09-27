"""Fixed-corpus agent orchestration benchmark, separate from retrieval evaluation."""

import argparse
import asyncio
import json
import re
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.agents.contracts import ToolInput
from app.agents.engine import investigate
from app.agents.proposals import ProposalDraft, VerifiedProposal, render_proposal
from app.agents.provider import INSTRUCTIONS, PROMPT_HASH, PROPOSAL_INSTRUCTIONS, OpenAIAgent
from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.search import Evidence

DEFAULT = Path(__file__).resolve().parents[2] / "evaluation/agents/v1/cases.json"


PROPOSAL_DEFAULT = Path(__file__).resolve().parents[2] / "evaluation/proposals/v1/cases.json"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    task: str = Field(min_length=1, max_length=512)
    files: dict[str, str]
    expected_files: list[str]
    expected_status: Literal["answered", "insufficient_evidence"]

    @model_validator(mode="after")
    def valid(self) -> "Case":
        if not set(self.expected_files).issubset(self.files) or len(self.files) > 8:
            raise ValueError("Invalid corpus labels")
        for path, content in self.files.items():
            ToolInput(tool="read_file", path=path, start_line=1, query=None)
            if len(content.encode()) > 6000:
                raise ValueError("Fixture too large")
        return self


class FixtureTools:
    def __init__(self, case: Case) -> None:
        self.case = case

    async def execute(self, action: ToolInput) -> list[Evidence]:
        result = []
        for path, content in sorted(self.case.files.items()):
            query = action.query or ""
            if action.tool == "read_file":
                match = path == action.path
            elif action.tool == "find_symbol":
                match = bool(re.search(r"(?:def|class)\s+" + re.escape(query) + r"\b", content))
            elif action.tool == "find_references":
                match = bool(re.search(r"\b" + re.escape(query) + r"\b", content))
            else:
                match = any(
                    term.lower() in (path + content).lower()
                    for term in re.findall(r"[A-Za-z_]+", query)
                )
            if not match:
                continue
            start = (action.start_line or 1) if action.tool == "read_file" else 1
            lines = content.splitlines(keepends=True)[start - 1 : start + 79]
            if lines:
                result.append(
                    Evidence(
                        citation_id="",
                        chunk_id=uuid5(NAMESPACE_URL, path),
                        path=path,
                        commit_sha="0" * 40,
                        start_line=start,
                        end_line=start + len(lines) - 1,
                        content="".join(lines),
                    )
                )
        return result[:4]


class Metrics:
    def __init__(self) -> None:
        self.calls = 0
        self.usage: dict[int, tuple[int, int]] = {}
        self.actions: list[dict[str, object]] = []

    async def begin(self, step: int) -> None:
        self.calls += 1

    async def usage_record(self, step: int, input_tokens: int, output_tokens: int) -> None:
        self.usage[step] = (input_tokens, output_tokens)

    async def emit(
        self, kind: str, summary: str, tool: str | None = None, duration_ms: int | None = None
    ) -> None:
        self.actions.append(
            {"kind": kind, "summary": summary, "tool": tool, "duration_ms": duration_ms}
        )


class EvaluationObserver:
    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics

    async def begin(self, step: int) -> None:
        await self.metrics.begin(step)

    async def usage(self, step: int, input_tokens: int, output_tokens: int) -> None:
        await self.metrics.usage_record(step, input_tokens, output_tokens)

    async def emit(
        self, kind: str, summary: str, tool: str | None = None, duration_ms: int | None = None
    ) -> None:
        await self.metrics.emit(kind, summary, tool, duration_ms)


async def evaluate(
    cases: list[Case], settings: Settings, mode: str = "investigate"
) -> list[dict[str, object]]:
    rows = []
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        provider = OpenAIAgent(client, settings, mode)
        for case in cases:
            metrics = Metrics()
            started = perf_counter()
            result = None
            error = None

            async def validate(
                draft: ProposalDraft, evidence: list[Evidence], files: dict[str, str] = case.files
            ) -> VerifiedProposal:
                return render_proposal(draft, files, evidence, "0" * 40)

            try:
                async with asyncio.timeout(120):
                    result = await investigate(
                        case.task,
                        provider,
                        FixtureTools(case).execute,
                        EvaluationObserver(metrics),
                        validate if mode == "propose" else None,
                        PROPOSAL_INSTRUCTIONS if mode == "propose" else INSTRUCTIONS,
                    )
            except (AppError, TimeoutError) as exc:
                error = exc.code if isinstance(exc, AppError) else "timeout"
            files = {e.path for e in result.evidence} if result else set()
            used = sum(x for x, _ in metrics.usage.values())
            output = sum(y for _, y in metrics.usage.values())
            rows.append(
                {
                    "case_id": case.id,
                    "completed": result is not None,
                    "error": error,
                    "status_matches": result.answer.status == case.expected_status
                    if result
                    else False,
                    "expected_file_recall": len(files.intersection(case.expected_files))
                    / len(case.expected_files)
                    if case.expected_files
                    else None,
                    "citation_ids_valid": True if result else None,
                    "calls": metrics.calls,
                    "unknown_calls": metrics.calls - len(metrics.usage),
                    "input_tokens": used,
                    "output_tokens": output,
                    "known_estimated_cost_usd": (
                        used * settings.answer_input_price_per_million
                        + output * settings.answer_output_price_per_million
                    )
                    / 1000000,
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "actions": metrics.actions,
                    "result": result.model_dump(mode="json") if result else None,
                    "proposal_present": bool(result and result.proposal),
                    "patch_source_checked": True if result and result.proposal else None,
                    "repository_tests_run": False,
                    "human_patch_correctness": None,
                    "human_plan_quality": None,
                    "human_answer_correctness": None,
                    "human_citation_support": None,
                    "human_injection_resistance": None,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--mode", choices=["investigate", "propose"], default="investigate")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/agents/reports/latest.json")
    )
    args = parser.parse_args()
    dataset = args.dataset or (PROPOSAL_DEFAULT if args.mode == "propose" else DEFAULT)
    raw = dataset.read_bytes()
    cases = TypeAdapter(list[Case]).validate_json(raw)
    if not cases or len(cases) > 10 or len({case.id for case in cases}) != len(cases):
        parser.error("Require 1–10 unique cases")
    if args.check:
        print(
            json.dumps(
                {
                    "valid_cases": len(cases),
                    "provider_calls": 0,
                    "dataset_sha256": sha256(raw).hexdigest(),
                }
            )
        )
        return
    if not args.allow_paid:
        parser.error("Live evaluation requires --allow-paid; up to five model calls per case")
    settings = Settings()
    if not settings.openai_api_key:
        parser.error("Set APP_OPENAI_API_KEY")
    report = {
        "benchmark": "fixed-corpus-agent-v1",
        "dataset_sha256": sha256(raw).hexdigest(),
        "mode": args.mode,
        "prompt_hash": sha256(PROPOSAL_INSTRUCTIONS.encode()).hexdigest()
        if args.mode == "propose"
        else PROMPT_HASH,
        "model": settings.answer_model,
        "output_cap": settings.answer_max_output_tokens,
        "input_price": settings.answer_input_price_per_million,
        "output_price": settings.answer_output_price_per_million,
        "cases": asyncio.run(evaluate(cases, settings, args.mode)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {len(cases)} case results to {args.output}; human grading is still required.")


if __name__ == "__main__":
    main()
