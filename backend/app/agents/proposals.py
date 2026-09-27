"""Validate edits against inspected, immutable source; render patches without executing code."""

import difflib
import re
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import AppError
from app.schemas.search import Evidence


def safe_path(path: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", path)) and all(
        part.lower() not in {".", "..", ".git"} for part in path.split("/")
    )


class ProposedEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=240)
    operation: Literal["replace", "create", "delete"]
    start_line: int | None = Field(ge=1, le=1000000)
    end_line: int | None = Field(ge=1, le=1000000)
    old_text: str = Field(max_length=12000)
    new_text: str = Field(max_length=12000)

    @model_validator(mode="after")
    def shape(self) -> "ProposedEdit":
        if not safe_path(self.path):
            raise ValueError("Expected a portable repository-relative path, without .git")
        if any(
            (ord(c) < 32 and c not in "\n\t") or c in "\x85\u2028\u2029"
            for c in self.old_text + self.new_text
        ):
            raise ValueError("Only LF text files are supported")
        if self.operation == "create":
            if (
                self.old_text
                or not self.new_text
                or self.start_line is not None
                or self.end_line is not None
            ):
                raise ValueError("Create requires new_text and null ranges")
        elif (
            not self.old_text
            or self.start_line is None
            or self.end_line is None
            or self.end_line < self.start_line
            or self.old_text == self.new_text
        ):
            raise ValueError("Existing edits require a nonempty exact source range and a change")
        if self.operation == "delete" and self.new_text:
            raise ValueError("Delete cannot add content")
        return self


class ProposalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    implementation_plan: list[str] = Field(min_length=1, max_length=6)
    risks: list[str] = Field(min_length=1, max_length=5)
    test_plan: list[str] = Field(min_length=1, max_length=6)
    edits: list[ProposedEdit] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def bounded(self) -> "ProposalDraft":
        if any(
            not s.strip() or len(s) > 500
            for s in self.implementation_plan + self.risks + self.test_plan
        ):
            raise ValueError("Plan, risk and test labels must contain 1–500 characters")
        if len({e.path for e in self.edits}) != len(self.edits):
            raise ValueError("At most one contiguous edit per file")
        paths = [e.path for e in self.edits]
        if any(a != b and a.startswith(b + "/") for a in paths for b in paths):
            raise ValueError("Proposed paths conflict as files and directories")
        return self


class ChangedFile(BaseModel):
    path: str
    operation: Literal["replace", "create", "delete"]
    before_sha256: str | None
    after_sha256: str | None


class VerifiedProposal(BaseModel):
    implementation_plan: list[str]
    risks: list[str]
    test_plan: list[str]
    files: list[ChangedFile]
    diff: str
    diff_sha256: str
    validation: Literal["source_checked_tests_not_run"] = "source_checked_tests_not_run"


def invalid(message: str) -> AppError:
    return AppError("proposal_source_invalid", message, 409)


def render_proposal(
    draft: ProposalDraft, files: dict[str, str], evidence: list[Evidence], commit_sha: str
) -> VerifiedProposal:
    """Pure transform. No filesystem, Git, subprocess, import, or repository mutation."""
    draft = ProposalDraft.model_validate(draft.model_dump())
    diffs: list[str] = []
    changed: list[ChangedFile] = []
    for edit in draft.edits:
        original = files.get(edit.path)
        if edit.operation == "create":
            if original is not None or any(
                p.startswith(edit.path + "/") or edit.path.startswith(p + "/") for p in files
            ):
                raise invalid("New file conflicts with an existing snapshot path.")
            updated = edit.new_text
        else:
            if original is None:
                raise invalid("Proposed file is absent from the pinned snapshot.")
            if any(c in original for c in "\r\x00\v\f\x85\u2028\u2029"):
                raise invalid("Only LF text files can be proposed in this milestone.")
            lines = original.splitlines(keepends=True)
            start, end = edit.start_line, edit.end_line
            assert start is not None and end is not None
            if end > len(lines) or "".join(lines[start - 1 : end]) != edit.old_text:
                raise invalid("Proposed old text does not exactly match the pinned source range.")
            # Every changed line must have been exposed to the model as exact source evidence.
            for number in range(start, end + 1):
                if not any(
                    e.path == edit.path
                    and e.commit_sha == commit_sha
                    and e.start_line <= number <= e.end_line
                    and e.content == "".join(lines[e.start_line - 1 : e.end_line])
                    for e in evidence
                ):
                    raise invalid("An edited line was not inspected as exact source evidence.")
            if edit.operation == "delete" and (start != 1 or end != len(lines)):
                raise invalid("Delete must cover the complete inspected file.")
            if end < len(lines) and edit.new_text and not edit.new_text.endswith("\n"):
                raise invalid("Replacement must preserve the boundary before the next line.")
            updated = "".join(lines[: start - 1]) + edit.new_text + "".join(lines[end:])
        before, after = original or "", updated
        diff = f"diff --git a/{edit.path} b/{edit.path}\n"
        if edit.operation == "create":
            diff += "new file mode 100644\n"
        for line in difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="/dev/null" if edit.operation == "create" else "a/" + edit.path,
            tofile="/dev/null" if edit.operation == "delete" else "b/" + edit.path,
            n=3,
        ):
            diff += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        diffs.append(diff)
        changed.append(
            ChangedFile(
                path=edit.path,
                operation=edit.operation,
                before_sha256=sha256(before.encode()).hexdigest() if original is not None else None,
                after_sha256=None
                if edit.operation == "delete"
                else sha256(after.encode()).hexdigest(),
            )
        )
    patch = "".join(diffs)
    if len(patch.encode()) > 64 * 1024:
        raise invalid("Patch exceeds the 64 KiB review limit.")
    return VerifiedProposal(
        implementation_plan=draft.implementation_plan,
        risks=draft.risks,
        test_plan=draft.test_plan,
        files=changed,
        diff=patch,
        diff_sha256=sha256(patch.encode()).hexdigest(),
    )
