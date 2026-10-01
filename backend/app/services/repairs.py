from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import InvestigationResult
from app.agents.provider import configuration
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.execution.contracts import ExecutionRequest, SandboxResult
from app.models import AgentCall, AgentRun, Conversation, ExecutionRun
from app.models.repair import RepairRun
from app.repair.policy import outcome
from app.repositories.agent import owned
from app.schemas.agent import AgentCallResponse, AgentRequest, AgentResponse
from app.schemas.execution import ExecutionResponse
from app.schemas.repair import (
    RepairAttempt,
    RepairDetail,
    RepairList,
    RepairRequest,
    RepairResponse,
)
from app.services.agents import AgentService
from app.services.executions import ExecutionService


def child_key(run: RepairRun, kind: str, revision: int) -> UUID:
    return uuid5(run.id, f"{kind}:{revision}")


class RepairService:
    def __init__(self, db: AsyncSession, settings: Settings, limiter: RateLimiter):
        self.db, self.settings, self.limiter = db, settings, limiter

    @property
    def enabled(self) -> bool:
        return (
            self.settings.repairs_enabled
            and self.settings.agents_enabled
            and self.settings.executions_enabled
        )

    async def get(self, user: UUID, run_id: UUID, *, lock: bool = False) -> RepairRun:
        run = await self.db.scalar(select(RepairRun).where(RepairRun.id == run_id))
        if run is None:
            raise AppError("not_found", "Repair not found.", 404)
        await owned(self.db, user, run.root_agent_id)
        if lock:
            run = await self.db.scalar(
                select(RepairRun)
                .where(RepairRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None:
                raise AppError("not_found", "Repair not found.", 404)
        return run

    async def list(self, user: UUID, agent_id: UUID) -> RepairList:
        await owned(self.db, user, agent_id)
        rows = await self.db.scalars(
            select(RepairRun)
            .where(RepairRun.root_agent_id == agent_id)
            .order_by(RepairRun.created_at.desc())
            .limit(20)
        )
        return RepairList(
            enabled=self.enabled, items=[RepairResponse.model_validate(r) for r in rows]
        )

    async def submit(
        self, user: UUID, agent_id: UUID, data: RepairRequest
    ) -> tuple[RepairResponse, bool]:
        agent = await owned(self.db, user, agent_id)
        await self.db.execute(
            select(Conversation.id)
            .where(Conversation.id == agent.conversation_id)
            .with_for_update()
        )
        previous = await self.db.scalar(
            select(RepairRun).where(
                RepairRun.root_agent_id == agent_id, RepairRun.request_key == data.request_key
            )
        )
        if previous:
            if previous.profile != data.profile or previous.max_revisions != data.max_revisions:
                raise AppError(
                    "idempotency_conflict", "Repair key already used with other limits.", 409
                )
            return RepairResponse.model_validate(previous), False
        if not self.enabled:
            raise AppError("repair_disabled", "Automatic repair is disabled.", 409)
        if (
            agent.mode != "propose"
            or agent.status != "completed"
            or not agent.result
            or not InvestigationResult.model_validate(agent.result).proposal
        ):
            raise AppError("proposal_required", "A completed patch proposal is required.", 409)
        if int(str((agent.execution_feedback or {}).get("depth", 0))) + data.max_revisions > 2:
            raise AppError(
                "feedback_limit", "This proposal has insufficient remaining revision budget.", 409
            )
        active = await self.db.scalar(
            select(RepairRun.id).where(
                RepairRun.conversation_id == agent.conversation_id, RepairRun.status == "running"
            )
        )
        count = await self.db.scalar(
            select(func.count()).select_from(RepairRun).where(RepairRun.root_agent_id == agent_id)
        )
        if active or (count or 0) >= 20:
            raise AppError(
                "repair_limit", "A repair is active or this proposal reached its repair limit.", 409
            )
        await self.limiter.check([Limit("repair:user:day:" + str(user), 5, 86400)])
        run = RepairRun(
            root_agent_id=agent_id,
            conversation_id=agent.conversation_id,
            request_key=data.request_key,
            profile=data.profile,
            max_revisions=data.max_revisions,
            current_agent_id=agent_id,
            deadline=datetime.now(UTC) + timedelta(minutes=20),
        )
        self.db.add(run)
        await self.db.flush()
        response = RepairResponse.model_validate(run)
        await self.db.commit()
        return response, True

    async def detail(self, user: UUID, run_id: UUID) -> RepairDetail:
        run = await self.get(user, run_id)
        attempts = []
        for revision in range(run.revision + 1):
            agent = (
                await self.db.get(AgentRun, run.root_agent_id)
                if revision == 0
                else await self.db.scalar(
                    select(AgentRun).where(
                        AgentRun.conversation_id == run.conversation_id,
                        AgentRun.request_key == child_key(run, "agent", revision),
                    )
                )
            )
            if agent is None:
                continue
            execution = await self.db.scalar(
                select(ExecutionRun).where(
                    ExecutionRun.agent_run_id == agent.id,
                    ExecutionRun.request_key == child_key(run, "execution", revision),
                )
            )
            calls = await self.db.scalars(
                select(AgentCall).where(AgentCall.run_id == agent.id).order_by(AgentCall.step)
            )
            attempts.append(
                RepairAttempt(
                    agent=AgentResponse.model_validate(agent),
                    execution=ExecutionResponse.model_validate(execution) if execution else None,
                    calls=[AgentCallResponse.model_validate(c) for c in calls],
                )
            )
        return RepairDetail(run=RepairResponse.model_validate(run), attempts=attempts)

    async def stop(self, run: RepairRun, status: str, reason: str) -> None:
        # Lock order matches the controller: repair, agent, execution. Fence active children
        # in this same transaction so no child is queued after cancellation commits.
        children: list[tuple[type[AgentRun] | type[ExecutionRun], UUID | None]] = [
            (AgentRun, run.current_agent_id),
            (ExecutionRun, run.current_execution_id),
        ]
        for model, key in children:
            if key is None:
                continue
            child = await self.db.scalar(
                select(model)
                .where(model.id == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if isinstance(child, (AgentRun, ExecutionRun)) and child.status in {
                "queued",
                "running",
            }:
                child.status, child.finished_at = "cancelled", datetime.now(UTC)
                child.lease_token, child.lease_expires_at = None, None
                if isinstance(child, ExecutionRun):
                    child.stage = "cancelled"
        run.status, run.reason, run.finished_at = status, reason, datetime.now(UTC)

    async def cancel(self, user: UUID, run_id: UUID) -> RepairResponse:
        run = await self.get(user, run_id, lock=True)
        if run.status == "running":
            await self.stop(run, "cancelled", "user_cancelled")
        response = RepairResponse.model_validate(run)
        await self.db.commit()
        return response

    async def advance(self, run: RepairRun) -> None:
        if run.status != "running":
            return
        if not self.enabled or run.deadline.replace(tzinfo=UTC) <= datetime.now(UTC):
            await self.stop(run, "stopped", "disabled" if not self.enabled else "deadline")
            return
        root = await self.db.get(AgentRun, run.root_agent_id)
        conv = await self.db.get(Conversation, run.conversation_id)
        agent = await self.db.get(AgentRun, run.current_agent_id)
        if root is None or conv is None or agent is None:
            await self.stop(run, "stopped", "source_missing")
            return
        if root.config_hash != configuration(self.settings, "propose"):
            await self.stop(run, "stopped", "model_configuration_changed")
            return
        if agent.status in {"queued", "running"}:
            return
        if (
            agent.status != "completed"
            or not agent.result
            or not InvestigationResult.model_validate(agent.result).proposal
        ):
            await self.stop(run, "stopped", "proposal_unavailable")
            return
        profile: Literal["auto", "python", "javascript"] = (
            "python"
            if run.profile == "python"
            else "javascript"
            if run.profile == "javascript"
            else "auto"
        )
        if run.current_execution_id is None:
            # Avoid testing the exact same unsuccessful patch repeatedly.
            proposal = InvestigationResult.model_validate(agent.result).proposal
            assert proposal is not None
            prior = await self.detail(conv.user_id, run.id)
            if any(
                a.execution and a.execution.patch_sha256 == proposal.diff_sha256
                for a in prior.attempts
            ):
                await self.stop(run, "stopped", "repeated_patch")
                return
            execution, _ = await ExecutionService(self.db, self.settings, self.limiter).submit(
                conv.user_id,
                agent.id,
                ExecutionRequest(
                    request_key=child_key(run, "execution", run.revision),
                    profile=profile,
                    confirm_execution=True,
                ),
                commit=False,
            )
            run.current_execution_id = execution.id
            return
        execution_row = await self.db.get(ExecutionRun, run.current_execution_id)
        if execution_row and execution_row.status in {"queued", "running"}:
            return
        result = (
            SandboxResult.model_validate(execution_row.result)
            if execution_row and execution_row.result
            else None
        )
        history = await self.detail(conv.user_id, run.id)
        first = history.attempts[0].execution if history.attempts else None
        if (
            result
            and first
            and first.result
            and (
                result.image_id != first.result.image_id
                or result.archive_sha256 != first.result.archive_sha256
                or result.profile != first.result.profile
                or result.command != first.result.command
            )
        ):
            await self.stop(run, "stopped", "execution_environment_changed")
            return
        decision = (
            outcome(result) if execution_row and execution_row.status == "completed" else "stopped"
        )
        if decision == "passed":
            await self.stop(run, "passed", "test_command_passed")
        elif decision == "stopped":
            await self.stop(run, "stopped", "execution_unavailable_or_resource_limit")
        elif run.revision >= run.max_revisions:
            await self.stop(run, "exhausted", "revision_limit")
        else:
            revision = run.revision + 1
            revised, _ = await AgentService(self.db, self.settings, self.limiter).submit(
                conv.user_id,
                run.conversation_id,
                AgentRequest(
                    request_key=child_key(run, "agent", revision),
                    mode="propose",
                    question=root.question,
                    feedback_execution_id=run.current_execution_id,
                ),
                commit=False,
            )
            if (
                revised.source_index_id != root.source_index_id
                or revised.commit_sha != root.commit_sha
            ):
                # Savepoint rollback removes the new child before stopping this run.
                raise AppError("source_changed", "Pinned source changed.", 409)
            run.revision, run.current_agent_id, run.current_execution_id = (
                revision,
                revised.id,
                None,
            )
