import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.contracts import AgentProvider, InvestigationResult, ToolInput
from app.agents.engine import investigate
from app.agents.proposals import ProposalDraft, VerifiedProposal, render_proposal
from app.agents.provider import INSTRUCTIONS, PROPOSAL_INSTRUCTIONS, configuration
from app.agents.tools import RepositoryTools
from app.core.config import Settings
from app.core.errors import AppError
from app.models import AgentCall, AgentRun, Conversation, RepositoryFile, RepositoryIndex
from app.repositories.agent import event
from app.repositories.repository import RepositoryStore
from app.schemas.search import Evidence


class RunObserver:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        run_id: UUID,
        token: UUID,
        settings: Settings,
    ) -> None:
        self.factory, self.run_id, self.token, self.settings = factory, run_id, token, settings

    async def active(self, db: AsyncSession) -> AgentRun:
        run = await db.scalar(select(AgentRun).where(AgentRun.id == self.run_id).with_for_update())
        if (
            run is None
            or run.status != "running"
            or run.lease_token != self.token
            or run.lease_expires_at is None
            or run.lease_expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
        ):
            raise AppError("agent_inactive", "Investigation is no longer active.", 409)
        return run

    async def begin(self, step: int) -> None:
        async with self.factory() as db:
            await self.active(db)
            db.add(
                AgentCall(
                    run_id=self.run_id,
                    step=step,
                    input_rate=Decimal(str(self.settings.answer_input_price_per_million)),
                    output_rate=Decimal(str(self.settings.answer_output_price_per_million)),
                )
            )
            await event(db, self.run_id, "model_started", f"Model call {step} of 5.")
            await db.commit()

    async def usage(self, step: int, input_tokens: int, output_tokens: int) -> None:
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or min(input_tokens, output_tokens) < 0
        ):
            raise AppError("agent_usage_invalid", "Provider usage failed validation.", 502)
        async with self.factory() as db:
            row = await db.get(AgentCall, (self.run_id, step))
            if row is None:
                return
            cost = (input_tokens * row.input_rate + output_tokens * row.output_rate) / Decimal(
                1000000
            )
            await db.execute(
                update(AgentCall)
                .where(
                    AgentCall.run_id == self.run_id,
                    AgentCall.step == step,
                    AgentCall.input_tokens.is_(None),
                )
                .values(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()

    async def emit(
        self, kind: str, summary: str, tool: str | None = None, duration_ms: int | None = None
    ) -> None:
        async with self.factory() as db:
            await self.active(db)
            await event(db, self.run_id, kind, summary, tool, duration_ms)
            await db.commit()

    async def finish(self, result: InvestigationResult) -> None:
        async with self.factory() as db:
            run = await self.active(db)
            run.result = result.model_dump(mode="json")
            run.status, run.finished_at, run.lease_token, run.lease_expires_at = (
                "completed",
                datetime.now(UTC),
                None,
                None,
            )
            await event(
                db,
                self.run_id,
                "completed",
                "Investigation completed with validated source references.",
            )
            await db.commit()


async def run_agent(
    factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    settings: Settings,
    provider: AgentProvider,
    build_tools: Callable[[AsyncSession, UUID, UUID, UUID], RepositoryTools],
) -> None:
    now, token = datetime.now(UTC), uuid4()
    async with factory() as db:
        changed = await db.scalar(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "queued",
                AgentRun.created_at > now - timedelta(hours=1),
            )
            .values(
                status="running",
                started_at=now,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=180),
            )
            .returning(AgentRun.id)
        )
        if changed is None:
            return
        await event(db, run_id, "running", "Read-only investigation started.")
        await db.commit()
    observer = RunObserver(factory, run_id, token, settings)
    try:
        async with asyncio.timeout(120):
            async with factory() as db:
                run = await db.get(AgentRun, run_id)
                if run is None:
                    return
                conv = await db.get(Conversation, run.conversation_id)
                if conv is None:
                    return
                if not settings.agents_enabled or run.config_hash != configuration(
                    settings, run.mode
                ):
                    raise AppError(
                        "agent_config_changed",
                        "Agent configuration changed. Submit a new investigation.",
                        409,
                    )
                mode, commit_sha = run.mode, run.commit_sha
                user_id, repository_id, source_id, question = (
                    conv.user_id,
                    conv.repository_id,
                    run.source_index_id,
                    run.question,
                )

            async def execute(action: ToolInput) -> list[Evidence]:
                async with factory() as db:
                    # Check cancellation before each read; tools also authorize the source.
                    await observer.active(db)
                    await db.commit()
                    return await build_tools(db, user_id, repository_id, source_id).execute(action)

            async def validate_proposal(
                draft: ProposalDraft, evidence: list[Evidence]
            ) -> VerifiedProposal:
                async with factory() as db:
                    await observer.active(db)
                    await RepositoryStore(db).owned(user_id, repository_id)
                    source = await db.scalar(
                        select(RepositoryIndex).where(
                            RepositoryIndex.id == source_id,
                            RepositoryIndex.repository_id == repository_id,
                            RepositoryIndex.status == "completed",
                            RepositoryIndex.commit_sha == commit_sha,
                        )
                    )
                    if source is None:
                        raise AppError(
                            "agent_source_missing", "Pinned source is no longer available.", 409
                        )
                    # Load paths for collisions; content only for the four edited files.
                    rows = await db.execute(
                        select(RepositoryFile.path, RepositoryFile.content).where(
                            RepositoryFile.repository_id == repository_id,
                            RepositoryFile.import_job_id == source.import_job_id,
                            RepositoryFile.path.in_([e.path for e in draft.edits]),
                        )
                    )
                    files = dict(rows.tuples().all())
                    paths = await db.scalars(
                        select(RepositoryFile.path).where(
                            RepositoryFile.repository_id == repository_id,
                            RepositoryFile.import_job_id == source.import_job_id,
                        )
                    )
                    for path in paths:
                        files.setdefault(path, "")
                    return render_proposal(draft, files, evidence, commit_sha)

            result = await investigate(
                question,
                provider,
                execute,
                observer,
                validate_proposal if mode == "propose" else None,
                PROPOSAL_INSTRUCTIONS if mode == "propose" else INSTRUCTIONS,
            )
            await observer.finish(result)
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, AppError)
            else ("agent_timeout" if isinstance(exc, TimeoutError) else "agent_worker_error")
        )
        message = (
            exc.message
            if isinstance(exc, AppError)
            else "Investigation stopped. Check status and usage before retrying."
        )
        logging.getLogger("repopilot.agent").warning(
            "agent_failed",
            extra={"job_id": str(run_id), "error_code": code, "error_type": type(exc).__name__},
        )
        async with factory() as db:
            changed = await db.scalar(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "running",
                    AgentRun.lease_token == token,
                )
                .values(
                    status="failed",
                    error_code=code,
                    error_message=message,
                    finished_at=datetime.now(UTC),
                    lease_token=None,
                    lease_expires_at=None,
                )
                .returning(AgentRun.id)
            )
            if changed is not None:
                await event(db, run_id, "failed", message)
            await db.commit()
