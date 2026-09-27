import asyncio
import json
import subprocess
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.agents.contracts import ProposalDecision, StepResult
from app.agents.proposals import ProposalDraft, ProposedEdit, render_proposal
from app.agents.provider import OpenAIAgent, configuration
from app.core.config import Settings
from app.core.errors import AppError
from app.core.provider_usage import ProviderUsageError
from app.schemas.search import Evidence

COMMIT = "a" * 40


def edit(
    path="main.py", old="print('old')\n", new="print('new')\n", operation="replace", start=1, end=1
):
    return dict(
        path=path, operation=operation, old_text=old, new_text=new, start_line=start, end_line=end
    )


def draft(edits):
    return ProposalDraft(
        implementation_plan=["Change the greeting."],
        risks=["Review callers."],
        test_plan=["Check output manually; tests not run."],
        edits=edits,
    )


def evidence(content="print('old')\n", path="main.py", commit=COMMIT):
    return Evidence(
        citation_id="C1",
        chunk_id=uuid4(),
        path=path,
        commit_sha=commit,
        start_line=1,
        end_line=len(content.splitlines()),
        content=content,
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("print('old')\n", "print('new')\n"),
        ("print('old')", "print('new')"),
        ("x = 'café'\n", "x = 'été'\n"),
        ("print('old')\n", ""),
    ],
)
def test_patch_applies_exactly_without_executing_fixture(tmp_path, old, new):
    # Git is used only on literal test fixtures in an isolated temporary directory.
    (tmp_path / "main.py").write_text(old)
    value = render_proposal(
        draft([edit(old=old, new=new)]), {"main.py": old}, [evidence(old)], COMMIT
    )
    patch = tmp_path / "change.patch"
    patch.write_text(value.diff)
    subprocess.run(
        ["git", "apply", "--check", str(patch)], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / "main.py").read_text() == new
    assert value.files[0].after_sha256 == sha256(new.encode()).hexdigest()
    assert value.validation == "source_checked_tests_not_run"


def test_create_delete_and_executable_source_diff(tmp_path):
    old = "print('old')\n"
    path = tmp_path / "main.py"
    path.write_text(old)
    path.chmod(0o755)
    value = render_proposal(
        draft(
            [
                edit(new="", operation="delete"),
                edit(
                    path="tests/new_test.py",
                    old="",
                    new="assert True\n",
                    operation="create",
                    start=None,
                    end=None,
                ),
            ]
        ),
        {"main.py": old},
        [evidence()],
        COMMIT,
    )
    patch = tmp_path / "change.patch"
    patch.write_text(value.diff)
    subprocess.run(
        ["git", "apply", "--check", str(patch)], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True, capture_output=True)
    assert not path.exists()
    assert (tmp_path / "tests/new_test.py").read_text() == "assert True\n"
    assert value.files[0].after_sha256 is None
    assert value.files[1].before_sha256 is None


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        ".git/config",
        ".GIT/config",
        "a/../b",
        "a b.py",
        "a\\b",
        "a\n+++ x",
        "a//b",
    ],
)
def test_unsafe_diff_paths_rejected(path):
    with pytest.raises(ValidationError):
        ProposedEdit.model_validate(edit(path=path))


@pytest.mark.parametrize(
    "failure",
    ["mismatch", "missing", "unread", "foreign_commit", "partial_delete", "crlf", "line_boundary"],
)
def test_source_validation_fails_closed(failure):
    files = {"main.py": "print('old')\n"}
    item = edit()
    seen = [evidence()]
    if failure == "mismatch":
        item["old_text"] = "hallucinated\n"
    if failure == "missing":
        files = {}
    if failure == "unread":
        seen = []
    if failure == "foreign_commit":
        seen = [evidence(commit="b" * 40)]
    if failure == "partial_delete":
        files["main.py"] += "tail\n"
        item.update(operation="delete", new_text="")
    if failure == "crlf":
        files["main.py"] = "print('old')\r\n"
    if failure == "line_boundary":
        files["main.py"] += "tail\n"
        item["new_text"] = "no newline"
    with pytest.raises(AppError) as caught:
        render_proposal(draft([item]), files, seen, COMMIT)
    assert caught.value.code == "proposal_source_invalid"


@pytest.mark.parametrize("files", [{"new.py": ""}, {"new.py/child": ""}, {"new.py": "old"}])
def test_create_never_overwrites_snapshot(files):
    with pytest.raises(AppError):
        render_proposal(
            draft(
                [edit(path="new.py", old="", new="new", operation="create", start=None, end=None)]
            ),
            files,
            [],
            COMMIT,
        )


def test_multiple_edits_or_file_directory_conflicts_rejected():
    with pytest.raises(ValidationError):
        draft([edit(), edit()])
    with pytest.raises(ValidationError):
        draft(
            [
                edit(path="x", old="", operation="create", start=None, end=None),
                edit(path="x/y", old="", operation="create", start=None, end=None),
            ]
        )


@pytest.mark.parametrize("invalid", [False, True])
def test_proposal_provider_schema_and_usage(invalid):
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/test",
        openai_api_key="unused",
        answer_model="fixture",
    )

    def handle(request):
        body = json.loads(request.content)
        assert body["text"]["format"]["schema"] == ProposalDecision.model_json_schema()
        assert "review artifact" in body["instructions"]
        assert body["tools"] == [] and body["store"] is False
        decision = dict(
            plan=["Inspect source"],
            action=None,
            answer=dict(
                status="answered",
                claims=[dict(text="Change greeting", citation_ids=["C1"])],
                limitation="",
            ),
            proposal=draft([edit()]).model_dump(),
        )
        return httpx.Response(
            200,
            json=dict(
                status="completed",
                model="fixture",
                usage=dict(input_tokens=100, output_tokens=50, total_tokens=150),
                output=[
                    dict(
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            dict(
                                type="output_text",
                                text="invalid" if invalid else json.dumps(decision),
                            )
                        ],
                    )
                ],
            ),
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await OpenAIAgent(client, settings, "propose").decide("{}")

    if invalid:
        with pytest.raises(ProviderUsageError) as caught:
            asyncio.run(run())
        assert caught.value.output_tokens == 50
    else:
        result = asyncio.run(run())
        assert isinstance(StepResult.model_validate(result.model_dump()).decision, ProposalDecision)
    assert configuration(settings) != configuration(settings, "propose")
