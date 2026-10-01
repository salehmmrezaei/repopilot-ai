import asyncio
import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.agents.contracts import ProposalDecision, StepResult
from app.core.config import Settings
from app.core.errors import AppError
from app.evaluation.coding import (
    DEFAULT,
    Attempt,
    CaseReport,
    RemoteExecutor,
    archive,
    dashboard,
    evaluate_case,
    load,
    reference,
    summary,
)
from app.execution.contracts import CommandResult, SandboxResult
from app.repair.policy import outcome


def receipt(status="passed"):
    def command(state):
        return CommandResult(
            status=state,
            exit_code=0 if state == "passed" else 1,
            log="untrusted <script>x</script>",
            truncated=False,
            duration_ms=1,
        )

    return SandboxResult(
        status="completed",
        request_hash="fixture",
        image_id="sha256:" + "a" * 64,
        archive_sha256="b" * 64,
        profile="python",
        command=["pytest"],
        preparation=command("passed"),
        baseline=command("failed"),
        patched=command(status),
    )


class Executor:
    def __init__(self, statuses):
        self.statuses, self.calls = statuses, 0

    async def execute(self, case, proposal):
        result = receipt(self.statuses[self.calls])
        self.calls += 1
        return result


class Provider:
    def __init__(self, case, protected=False, repeat=False):
        self.case, self.protected, self.repeat = case, protected, repeat
        self.calls = 0
        self.payloads = []

    async def decide(self, payload):
        data = json.loads(payload)
        self.payloads.append(data)
        self.calls += 1
        if self.calls % 2:
            value = dict(
                plan=["Read source"],
                action=dict(
                    tool="read_file", path=self.case.editable_paths[0], start_line=1, query=None
                ),
                answer=None,
                proposal=None,
            )
        else:
            draft = self.case.reference.model_copy(deep=True)
            if self.protected:
                draft.edits[0].path = "test_solution.py"
            if not self.repeat:
                draft.edits[0].new_text += f"\n# attempt {self.calls // 2}\n"
            value = dict(
                plan=[],
                action=None,
                answer=dict(
                    status="answered",
                    claims=[
                        dict(text="Fix source", citation_ids=[data["evidence"][0]["citation_id"]])
                    ],
                    limitation="Tests pending",
                ),
                proposal=draft.model_dump(),
            )
        return StepResult(
            decision=ProposalDecision.model_validate(value), input_tokens=100, output_tokens=10
        )


def settings():
    return Settings(database_url="postgresql+asyncpg://x:y@localhost/db")


def test_dataset_references_archives_and_protected_paths():
    _, cases = load(DEFAULT)
    assert len(cases) == 4
    for case in cases:
        proposal = asyncio.run(reference(case))
        assert proposal.diff and archive(case) == archive(case)
        assert {f.path for f in proposal.files}.issubset(case.editable_paths)
    broken = cases[0].model_dump()
    broken["editable_paths"] = ["missing.py"]
    with pytest.raises(ValidationError):
        type(cases[0]).model_validate(broken)


def test_coding_loop_feedback_usage_and_first_vs_final():
    case = load(DEFAULT)[1][0]
    provider, executor = Provider(case), Executor(["failed", "passed"])
    result = asyncio.run(evaluate_case(case, settings(), provider, executor))
    assert result.final_passed and not result.first_attempt_passed
    assert len(result.attempts) == 2 and executor.calls == 2
    assert provider.payloads[2]["execution_feedback"]["notice"].startswith("UNTRUSTED")
    report = summary([result])
    assert report["provider_calls"] == 4 and report["unknown_calls"] == 0
    assert report["known_estimated_cost_usd"] > 0 and report["final_pass_rate"] == 1


@pytest.mark.parametrize(
    ("statuses", "repeat", "reason", "runs"),
    [
        (["failed"], True, "repeated_patch", 1),
        (["failed", "failed", "failed"], False, "revision_limit", 3),
        (["timeout"], False, "execution_unavailable_or_resource_limit", 1),
    ],
)
def test_coding_bounds(statuses, repeat, reason, runs):
    case = load(DEFAULT)[1][0]
    executor = Executor(statuses)
    result = asyncio.run(evaluate_case(case, settings(), Provider(case, repeat=repeat), executor))
    assert result.stop_reason == reason and executor.calls == runs
    assert not result.final_passed


def test_protected_tests_and_unknown_provider_usage():
    case = load(DEFAULT)[1][0]
    executor = Executor([])
    result = asyncio.run(evaluate_case(case, settings(), Provider(case, protected=True), executor))
    assert result.attempts[0].error == "protected_file" and executor.calls == 0

    class Broken:
        async def decide(self, payload):
            raise AppError("provider_failed", "uncertain", 502)

    result = asyncio.run(evaluate_case(case, settings(), Broken(), executor))
    assert result.attempts[0].unknown_calls == 1
    assert result.attempts[0].known_estimated_cost_usd == 0


def test_reference_mode_no_provider_and_invalid_baseline():
    case = load(DEFAULT)[1][0]
    result = asyncio.run(evaluate_case(case, settings(), None, Executor(["passed"])))
    assert result.final_passed and result.attempts[0].calls == 0

    class AlreadyPassing:
        async def execute(self, case, proposal):
            result = receipt()
            result.baseline.status, result.baseline.exit_code = "passed", 0
            return result

    result = asyncio.run(evaluate_case(case, settings(), None, AlreadyPassing()))
    assert result.stop_reason == "invalid_baseline" and not result.final_passed


def test_dashboard_escapes_labels_and_reports_unknown_separately():
    rows = [
        CaseReport(
            case_id="<script>evil()</script>",
            attempts=[Attempt(number=1, calls=1, unknown_calls=1)],
        )
    ]
    page = dashboard({"mode": "reference-verification", "summary": summary(rows)}, rows)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "reference-verification" in page and "unknown_calls" in page
    assert summary([])["final_pass_rate"] is None


def test_remote_executor_provenance_and_cleanup():
    case = load(DEFAULT)[1][0]
    proposal = asyncio.run(reference(case))

    class Client:
        def __init__(self, mismatch=False):
            self.methods, self.mismatch = [], mismatch

        async def request(self, method, run_id, body=None):
            self.methods.append(method)
            result = receipt()
            if method == "POST":
                result.request_hash = sha256(body.model_dump_json().encode()).hexdigest()
                result.archive_sha256 = sha256(archive(case)).hexdigest()
                if self.mismatch:
                    result.image_id = "changed"
            return result

    client = Client()
    result = asyncio.run(RemoteExecutor(client, "sha256:" + "a" * 64).execute(case, proposal))
    assert outcome(result) == "passed" and client.methods == ["POST"]
    client = Client(True)
    with pytest.raises(AppError, match="receipt"):
        asyncio.run(RemoteExecutor(client, "sha256:" + "a" * 64).execute(case, proposal))
    assert client.methods == ["POST", "DELETE"]
