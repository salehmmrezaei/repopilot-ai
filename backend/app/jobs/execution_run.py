import asyncio
import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.contracts import InvestigationResult
from app.core.config import Settings
from app.core.errors import AppError
from app.execution.contracts import SandboxRequest, SandboxResult
from app.integrations.github.client import GitHubClient, RepositorySource
from app.models import AgentRun, Conversation, ExecutionRun, Repository


class Runner(Protocol):
    async def request(
        self, method: str, run_id: UUID, body: SandboxRequest | None = None
    ) -> SandboxResult: ...


async def run_execution(
    factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    settings: Settings,
    github: GitHubClient,
    runner: Runner,
    interval: float = 2,
) -> None:
    now, token = datetime.now(UTC), uuid4()
    async with factory() as db:
        changed = await db.scalar(
            update(ExecutionRun)
            .where(ExecutionRun.id == run_id, ExecutionRun.status == "queued")
            .values(
                status="running",
                stage="fetching_pinned_archive",
                started_at=now,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=300),
            )
            .returning(ExecutionRun.id)
        )
        await db.commit()
        if changed is None:
            return

    async def active() -> bool:
        async with factory() as db:
            row = await db.get(ExecutionRun, run_id)
            return bool(row and row.status == "running" and row.lease_token == token)

    try:
        async with asyncio.timeout(270):
            if not settings.executions_enabled:
                raise AppError("execution_disabled", "Execution is disabled.", 409)
            async with factory() as db:
                run = await db.get(ExecutionRun, run_id)
                assert run is not None
                agent = await db.get(AgentRun, run.agent_run_id)
                if agent is None or agent.result is None:
                    raise AppError("proposal_missing", "Proposal is no longer available.", 409)
                conversation = await db.get(Conversation, agent.conversation_id)
                assert conversation is not None
                repo = await db.get(Repository, conversation.repository_id)
                assert repo is not None
                proposal = InvestigationResult.model_validate(agent.result).proposal
                if (
                    proposal is None
                    or sha256(proposal.diff.encode()).hexdigest() != run.patch_sha256
                ):
                    raise AppError("proposal_changed", "Proposal fingerprint mismatch.", 409)
                source = RepositorySource(
                    repo.github_repository_id or 0, repo.owner, repo.name, "", agent.commit_sha
                )
                profile = run.profile
            archive = await github.download(source)  # exact immutable commit; never default branch
            body = SandboxRequest.model_validate(
                dict(
                    commit_sha=source.sha,
                    archive_b64=base64.b64encode(archive).decode(),
                    proposal=proposal,
                    profile=profile,
                )
            )
            if not await active():
                await runner.request("DELETE", run_id)
                return
            result = await runner.request("POST", run_id, body)
            expected_hash = sha256(body.model_dump_json().encode()).hexdigest()
            while True:
                if not await active():
                    await runner.request("DELETE", run_id)
                    return
                if result.request_hash != expected_hash or (
                    result.archive_sha256 is not None
                    and result.archive_sha256 != sha256(archive).hexdigest()
                ):
                    raise AppError("sandbox_invalid", "Sandbox input fingerprint mismatch.", 502)
                stage = (
                    "preparing"
                    if result.preparation is None
                    else ("baseline" if result.baseline is None else "patched")
                )
                async with factory() as db:
                    await db.execute(
                        update(ExecutionRun)
                        .where(
                            ExecutionRun.id == run_id,
                            ExecutionRun.status == "running",
                            ExecutionRun.lease_token == token,
                        )
                        .values(stage=stage, result=result.model_dump(mode="json"))
                    )
                    await db.commit()
                if result.status != "running":
                    async with factory() as db:
                        await db.execute(
                            update(ExecutionRun)
                            .where(
                                ExecutionRun.id == run_id,
                                ExecutionRun.status == "running",
                                ExecutionRun.lease_token == token,
                            )
                            .values(
                                status=result.status,
                                stage="finished",
                                error=result.error,
                                finished_at=datetime.now(UTC),
                                lease_token=None,
                                lease_expires_at=None,
                            )
                        )
                        await db.commit()
                    return
                await asyncio.sleep(interval)
                result = await runner.request("GET", run_id)
    except Exception:
        try:
            await runner.request("DELETE", run_id)
        except Exception:
            pass  # Runner-side deadlines and orphan reaper bound abandoned work.
        async with factory() as db:
            await db.execute(
                update(ExecutionRun)
                .where(
                    ExecutionRun.id == run_id,
                    ExecutionRun.status == "running",
                    ExecutionRun.lease_token == token,
                )
                .values(
                    status="failed",
                    stage="failed",
                    error=(
                        "Execution stopped or runner communication was uncertain. "
                        "No automatic retry; inspect runner logs."
                    ),
                    finished_at=datetime.now(UTC),
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            await db.commit()
