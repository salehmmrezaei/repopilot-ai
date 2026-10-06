"""A bounded, immutable PR snapshot assembled from GitHub's merge base and head."""

import asyncio
import difflib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from app.core.errors import AppError
from app.indexing.archive import read_archive
from app.indexing.pipeline import index_source
from app.integrations.github.client import Commit, GitHubClient, RepositorySource
from app.schemas.search import Evidence


class Pull(BaseModel):
    number: int
    changed_files: int = Field(ge=0, le=8)
    head: Commit
    base: Commit


class ChangedFile(BaseModel):
    status: Literal["added", "removed", "modified", "renamed", "copied", "changed", "unchanged"]
    filename: str = Field(max_length=512)
    previous_filename: str | None = Field(default=None, max_length=512)


class Comparison(BaseModel):
    merge_base_commit: Commit
    files: list[ChangedFile] = Field(max_length=8)


async def snapshot(
    github: GitHubClient, owner: str, name: str, number: int
) -> tuple[str, str, list[Evidence], str]:
    endpoint = f"https://api.github.com/repos/{owner}/{name}"
    try:
        pull = Pull.model_validate_json(
            await github._get(f"{endpoint}/pulls/{number}", 1024 * 1024)
        )
        comparison = Comparison.model_validate_json(
            await github._get(f"{endpoint}/compare/{pull.base.sha}...{pull.head.sha}", 1024 * 1024)
        )
        if pull.number != number or len(comparison.files) != pull.changed_files:
            raise ValueError("incomplete")
    except (ValidationError, ValueError):
        raise AppError(
            "review_size_limit",
            "Review requires at most eight changed files and a complete GitHub comparison.",
            422,
        ) from None
    if not comparison.files:
        raise AppError("review_empty", "This pull request has no changed files to review.", 422)
    base, head = comparison.merge_base_commit.sha, pull.head.sha
    old = await asyncio.to_thread(
        read_archive, await github.download(RepositorySource(0, owner, name, "", base))
    )
    new = await asyncio.to_thread(
        read_archive, await github.download(RepositorySource(0, owner, name, "", head))
    )
    before, after = {f.path: f for f in old.files}, {f.path: f for f in new.files}
    evidence: list[Evidence] = []
    diffs: list[dict[str, object]] = []
    for changed in comparison.files:
        path = changed.filename
        prior_path = changed.previous_filename or path
        left, right = before.get(prior_path), after.get(path)
        if (
            (left is None and changed.status not in {"added", "copied"})
            or (right is None and changed.status != "removed")
            or (left is None and right is None)
        ):
            raise AppError(
                "review_unsupported_file",
                "A changed file is excluded, binary, or unsupported. Review it manually.",
                422,
            )
        for f, sha in [(left, base), (right, head)]:
            if f is not None:
                if f.size > 8192:
                    raise AppError(
                        "review_size_limit",
                        "Changed files must be at most 8 KiB for this bounded review.",
                        422,
                    )
                evidence.append(
                    Evidence(
                        citation_id=f"E{len(evidence) + 1}",
                        chunk_id=uuid4(),
                        path=f.path,
                        commit_sha=sha,
                        start_line=1,
                        end_line=max(1, len(f.content.splitlines())),
                        content=f.content,
                    )
                )
        diff = "".join(
            difflib.unified_diff(
                (left.content if left else "").splitlines(keepends=True),
                (right.content if right else "").splitlines(keepends=True),
                fromfile=prior_path,
                tofile=path,
            )
        )
        symbols = (
            await asyncio.to_thread(index_source, right.content, right.language, path)
            if right
            else None
        )
        diffs.append(
            {
                "path": path,
                "diff": diff,
                "symbols": [s.qualified_name for s in symbols.symbols[:50]] if symbols else [],
            }
        )
    # Include a few small nearby test files, clearly labeled as head evidence.
    changed_paths = {f.filename for f in comparison.files}
    stems = {p.rsplit("/", 1)[-1].split(".")[0] for p in changed_paths}
    for f in sorted(new.files, key=lambda f: f.path):
        if len(evidence) >= 20:
            break
        if (
            f.path not in changed_paths
            and f.size <= 4096
            and "test" in f.path.lower()
            and any(stem in f.path for stem in stems)
        ):
            evidence.append(
                Evidence(
                    citation_id=f"E{len(evidence) + 1}",
                    chunk_id=uuid4(),
                    path=f.path,
                    commit_sha=head,
                    start_line=1,
                    end_line=max(1, len(f.content.splitlines())),
                    content=f.content,
                )
            )
    return base, head, evidence, json.dumps(diffs, ensure_ascii=False)
