"""Synthetic coding benchmark using the real bounded agent and isolated runner."""

import argparse
import asyncio
import base64
import gzip
import html
import io
import json
import tarfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, TypeAdapter, model_validator

from app.agents.contracts import AgentProvider, InvestigationResult
from app.agents.engine import investigate
from app.agents.proposals import ProposalDraft, VerifiedProposal, render_proposal
from app.agents.provider import PROPOSAL_INSTRUCTIONS, OpenAIAgent, configuration
from app.core.config import Settings
from app.core.errors import AppError
from app.evaluation.agents import Case, EvaluationObserver, FixtureTools, Metrics
from app.execution.client import SandboxClient
from app.execution.contracts import SandboxRequest, SandboxResult
from app.repair.policy import POLICY_VERSION, feedback, outcome
from app.schemas.search import Evidence

DEFAULT = Path(__file__).resolve().parents[2] / "evaluation/coding/v1/cases.json"


class CodingCase(Case):
    editable_paths: list[str] = Field(min_length=1, max_length=4)
    profile: Literal["python", "javascript"]
    reference: ProposalDraft

    @model_validator(mode="after")
    def labels(self) -> "CodingCase":
        if not set(self.editable_paths).issubset(self.files):
            raise ValueError("Editable paths must exist")
        if not set(self.expected_files).issubset(self.editable_paths):
            raise ValueError("Expected paths must be editable")
        if any(e.path not in self.editable_paths for e in self.reference.edits):
            raise ValueError("Reference edits protected files")
        if not any(p.startswith("test_") for p in self.files):
            raise ValueError("Regression tests required")
        return self


class Attempt(BaseModel):
    number: int
    calls: int = 0
    unknown_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    known_estimated_cost_usd: float = 0
    latency_ms: int = 0
    error: str | None = None
    proposal: VerifiedProposal | None = None
    sandbox: SandboxResult | None = None


class CaseReport(BaseModel):
    case_id: str
    attempts: list[Attempt] = Field(default_factory=list)
    stop_reason: str = "revision_limit"
    first_attempt_passed: bool = False
    final_passed: bool = False
    baseline_failed: bool = False
    latency_ms: int = 0
    human_patch_correctness: int | None = None
    human_notes: str | None = None


class Executor(Protocol):
    async def execute(self, case: CodingCase, proposal: VerifiedProposal) -> SandboxResult: ...


def archive(case: CodingCase) -> bytes:
    """Deterministic synthetic corpus archive; never imports or runs fixture code here."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
        for path, text in sorted(case.files.items()):
            data = text.encode()
            info = tarfile.TarInfo("fixture/" + path)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            bundle.addfile(info, io.BytesIO(data))
    return gzip.compress(stream.getvalue(), mtime=0)


async def reference(case: CodingCase) -> VerifiedProposal:
    evidence = []
    tools = FixtureTools(case)
    from app.agents.contracts import ToolInput

    for path in case.editable_paths:
        evidence.extend(
            await tools.execute(ToolInput(tool="read_file", path=path, start_line=1, query=None))
        )
    return render_proposal(case.reference, case.files, evidence, "0" * 40)


class RemoteExecutor:
    def __init__(self, client: SandboxClient, image_id: str):
        self.client, self.image_id = client, image_id

    async def execute(self, case: CodingCase, proposal: VerifiedProposal) -> SandboxResult:
        blob = archive(case)
        request = SandboxRequest(
            commit_sha="0" * 40,
            archive_b64=base64.b64encode(blob).decode(),
            proposal=proposal,
            profile=case.profile,
        )
        fingerprint = sha256(request.model_dump_json().encode()).hexdigest()
        run_id = uuid4()
        finished = False
        try:
            async with asyncio.timeout(270):
                result = await self.client.request("POST", run_id, request)
                while True:
                    if (
                        result.request_hash != fingerprint
                        or result.image_id != self.image_id
                        or (
                            result.archive_sha256 is not None
                            and result.archive_sha256 != sha256(blob).hexdigest()
                        )
                    ):
                        raise AppError(
                            "sandbox_provenance",
                            "Runner receipt does not match benchmark inputs.",
                            502,
                        )
                    if result.status != "running":
                        finished = True
                        return result
                    await asyncio.sleep(2)
                    result = await self.client.request("GET", run_id)
        finally:
            if not finished:
                try:
                    await self.client.request("DELETE", run_id)
                except (AppError, httpx.HTTPError, ValueError):
                    pass  # Remote expiry/reaper remains the final cleanup backstop.


def passing(result: SandboxResult | None) -> bool:
    return bool(
        result
        and outcome(result) == "passed"
        and result.baseline
        and result.baseline.status == "failed"
        and result.baseline.exit_code not in {None, 0}
    )


async def evaluate_case(
    case: CodingCase,
    settings: Settings,
    provider: AgentProvider | None,
    executor: Executor,
    max_revisions: int = 2,
) -> CaseReport:
    if max_revisions not in {0, 1, 2}:
        raise ValueError("Benchmark revisions must be between zero and two")
    row = CaseReport(case_id=case.id)
    started = perf_counter()
    seen: set[str] = set()
    saved_feedback = None
    for revision in range((max_revisions if provider else 0) + 1):
        metrics = Metrics()
        attempt = Attempt(number=revision + 1)
        tick = perf_counter()
        row.attempts.append(attempt)

        async def validate(draft: ProposalDraft, evidence: list[Evidence]) -> VerifiedProposal:
            if any(edit.path not in case.editable_paths for edit in draft.edits):
                raise AppError(
                    "protected_file", "Benchmark tests and configuration cannot be edited.", 409
                )
            return render_proposal(draft, case.files, evidence, "0" * 40)

        try:
            async with asyncio.timeout(120):
                if provider is None:
                    attempt.proposal = await reference(case)
                else:
                    result: InvestigationResult = await investigate(
                        case.task,
                        provider,
                        FixtureTools(case).execute,
                        EvaluationObserver(metrics),
                        validate,
                        PROPOSAL_INSTRUCTIONS,
                        saved_feedback,
                    )
                    attempt.proposal = result.proposal
            if attempt.proposal is None:
                row.stop_reason = "proposal_unavailable"
                break
            if attempt.proposal.diff_sha256 in seen:
                row.stop_reason = "repeated_patch"
                break
            seen.add(attempt.proposal.diff_sha256)
            attempt.sandbox = await executor.execute(case, attempt.proposal)
            decision = outcome(attempt.sandbox)
            row.baseline_failed = bool(
                attempt.sandbox.baseline
                and attempt.sandbox.baseline.status == "failed"
                and attempt.sandbox.baseline.exit_code not in {None, 0}
            )
            if not row.baseline_failed:
                row.stop_reason = "invalid_baseline"
                break
            if decision == "passed":
                row.first_attempt_passed, row.final_passed = revision == 0, True
                row.stop_reason = "test_command_passed"
                break
            if decision == "stopped":
                row.stop_reason = "execution_unavailable_or_resource_limit"
                break
            saved_feedback = feedback(attempt.sandbox, attempt.proposal)
        except (AppError, TimeoutError, ValueError, httpx.HTTPError) as exc:
            attempt.error = exc.code if isinstance(exc, AppError) else type(exc).__name__
            row.stop_reason = "error"
            break
        finally:
            attempt.calls, attempt.unknown_calls = metrics.calls, metrics.calls - len(metrics.usage)
            attempt.input_tokens = sum(x for x, _ in metrics.usage.values())
            attempt.output_tokens = sum(y for _, y in metrics.usage.values())
            attempt.known_estimated_cost_usd = (
                attempt.input_tokens * settings.answer_input_price_per_million
                + attempt.output_tokens * settings.answer_output_price_per_million
            ) / 1_000_000
            attempt.latency_ms = round((perf_counter() - tick) * 1000)
    row.latency_ms = round((perf_counter() - started) * 1000)
    return row


def summary(rows: list[CaseReport]) -> dict[str, int | float | None]:
    attempts = [attempt for row in rows for attempt in row.attempts]
    n = len(rows)
    return {
        "cases": n,
        "first_attempt_pass_rate": sum(row.first_attempt_passed for row in rows) / n if n else None,
        "final_pass_rate": sum(row.final_passed for row in rows) / n if n else None,
        "valid_baselines": sum(row.baseline_failed for row in rows),
        "mean_attempts": len(attempts) / n if n else None,
        "provider_calls": sum(a.calls for a in attempts),
        "unknown_calls": sum(a.unknown_calls for a in attempts),
        "known_estimated_cost_usd": sum(a.known_estimated_cost_usd for a in attempts),
        "mean_latency_ms": sum(row.latency_ms for row in rows) / n if n else None,
    }


def dashboard(report: dict[str, object], rows: list[CaseReport]) -> str:
    body = "".join(
        f"<tr><td>{html.escape(row.case_id)}</td><td>{row.first_attempt_passed}</td><td>{row.final_passed}</td><td>{len(row.attempts)}</td><td>{html.escape(row.stop_reason)}</td></tr>"
        for row in rows
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; sty'
        "le-src 'unsafe-inline'\">"
        "<title>RepoPilot coding benchmark</title><style>body{font:16px system-ui;ma"
        "x-width:1100px;margin:3rem auto;padding:1rem}td,th{padding:.7rem;border:1px"
        " solid #ddd;text-align:left}table{border-collapse:collapse}pre{white-space:"
        "pre-wrap;overflow-wrap:anywhere}</style>"
        "<h1>Coding benchmark</h1><p>Synthetic development tasks; not held out. Pass"
        "ing a fixed test command is not proof of correctness. Unknown usage is excl"
        "uded from known cost. Reference verification is not a model-quality score.<"
        "/p>"
        "<table><caption>Per-task results</caption><tr><th>Task</th><th>First pass</"
        "th><th>Final pass</th><th>Attempts</th><th>Stop reason</th></tr>"
        + body
        + "</table><h2>Summary and provenance</h2><pre>"
        + html.escape(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
        + "</pre></html>"
    )


def load(path: Path) -> tuple[bytes, list[CodingCase]]:
    raw = path.read_bytes()
    cases = TypeAdapter(list[CodingCase]).validate_json(raw)
    if not 1 <= len(cases) <= 10 or len({c.id for c in cases}) != len(cases):
        raise ValueError("Require 1–10 unique cases")
    return raw, cases


async def run(
    cases: list[CodingCase], settings: Settings, image_id: str, verify: bool, revisions: int
) -> list[CaseReport]:
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
        executor = RemoteExecutor(SandboxClient(http, settings), image_id)
        rows = []
        for case in cases:
            provider = None if verify else OpenAIAgent(http, settings, "propose")
            rows.append(await evaluate_case(case, settings, provider, executor, revisions))
        return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--allow-execution", action="store_true")
    parser.add_argument("--verify-fixtures", action="store_true")
    parser.add_argument("--max-revisions", type=int, choices=[0, 1, 2], default=2)
    parser.add_argument("--image-id")
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/coding/reports/latest.json")
    )
    args = parser.parse_args()
    raw, cases = load(args.dataset)
    for case in cases:
        asyncio.run(reference(case))
    if args.check:
        print(
            json.dumps(
                {
                    "valid_cases": len(cases),
                    "provider_calls": 0,
                    "sandbox_runs": 0,
                    "dataset_sha256": sha256(raw).hexdigest(),
                }
            )
        )
        return
    import re

    if (
        not args.allow_execution
        or not args.image_id
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id)
    ):
        parser.error(
            "Requires --allow-execution and immutable --image-id; use the dedicated runner"
        )
    if not args.verify_fixtures and not args.allow_paid:
        parser.error("Requires --allow-paid: up to 15 model calls and three executions per case")
    if args.output.suffix != ".json":
        parser.error("Output must end in .json")
    settings = Settings()
    if not settings.sandbox_token or (not args.verify_fixtures and not settings.openai_api_key):
        parser.error("Configure sandbox token and (for live models) APP_OPENAI_API_KEY")
    rows = asyncio.run(
        run(cases, settings, args.image_id, args.verify_fixtures, args.max_revisions)
    )
    report: dict[str, object] = {
        "benchmark": "synthetic-coding-v1",
        "mode": "reference-verification" if args.verify_fixtures else "live-model",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": sha256(raw).hexdigest(),
        "corpus_sha256": {c.id: sha256(archive(c)).hexdigest() for c in cases},
        "policy": POLICY_VERSION,
        "max_revisions": 0 if args.verify_fixtures else args.max_revisions,
        "model": None if args.verify_fixtures else settings.answer_model,
        "config_hash": configuration(settings, "propose"),
        "prompt_sha256": sha256(PROPOSAL_INSTRUCTIONS.encode()).hexdigest(),
        "output_cap": settings.answer_max_output_tokens,
        "image_id": args.image_id,
        "input_price_per_million": settings.answer_input_price_per_million,
        "output_price_per_million": settings.answer_output_price_per_million,
        "summary": summary(rows),
        "cases": [row.model_dump(mode="json") for row in rows],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.with_suffix(".html").write_text(dashboard(report, rows))
    print(f"Wrote {args.output} and HTML dashboard. Human patch review remains required.")
    if args.verify_fixtures and not all(r.final_passed for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
