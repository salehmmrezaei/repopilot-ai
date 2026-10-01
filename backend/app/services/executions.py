from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import InvestigationResult
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.execution.contracts import ExecutionRequest
from app.models import AgentRun, ExecutionRun
from app.repositories.agent import owned
from app.schemas.execution import ExecutionList, ExecutionResponse


class ExecutionService:
    def __init__(self, db: AsyncSession, settings: Settings, limiter: RateLimiter):
        self.db, self.settings, self.limiter = db, settings, limiter

    async def get(self, user: UUID, run_id: UUID) -> ExecutionRun:
        run = await self.db.get(ExecutionRun, run_id)
        if run is None:
            raise AppError("not_found", "Execution not found.", 404)
        await owned(self.db, user, run.agent_run_id)
        return run

    async def list(self, user: UUID, agent_id: UUID) -> ExecutionList:
        await owned(self.db, user, agent_id)
        runs = await self.db.scalars(
            select(ExecutionRun)
            .where(ExecutionRun.agent_run_id == agent_id)
            .order_by(ExecutionRun.created_at.desc())
            .limit(20)
        )
        return ExecutionList(
            enabled=self.settings.executions_enabled,
            items=[ExecutionResponse.model_validate(row) for row in runs],
        )

    async def submit(
        self, user: UUID, agent_id: UUID, data: ExecutionRequest
    ) -> tuple[ExecutionResponse, bool]:
        agent = await owned(self.db, user, agent_id)
        await self.db.execute(select(AgentRun.id).where(AgentRun.id == agent_id).with_for_update())
        previous = await self.db.scalar(
            select(ExecutionRun).where(
                ExecutionRun.agent_run_id == agent_id, ExecutionRun.request_key == data.request_key
            )
        )
        if previous:
            if previous.profile != data.profile:
                raise AppError(
                    "idempotency_conflict", "Execution key already used with another profile.", 409
                )
            return ExecutionResponse.model_validate(previous), False
        if not self.settings.executions_enabled:
            raise AppError("execution_disabled", "Sandbox execution is disabled.", 409)
        if agent.mode != "propose" or agent.status != "completed" or agent.result is None:
            raise AppError("proposal_required", "A completed patch proposal is required.", 409)
        proposal = InvestigationResult.model_validate(agent.result).proposal
        if proposal is None:
            raise AppError("proposal_required", "This run has no patch.", 409)
        active = await self.db.scalar(
            select(ExecutionRun.id).where(
                ExecutionRun.agent_run_id == agent_id,
                ExecutionRun.status.in_(["queued", "running"]),
            )
        )
        count = await self.db.scalar(
            select(func.count())
            .select_from(ExecutionRun)
            .where(ExecutionRun.agent_run_id == agent_id)
        )
        if active or (count or 0) >= 20:
            raise AppError(
                "execution_limit",
                "An execution is active or this proposal reached its 20-run limit.",
                409,
            )
        await self.limiter.check(
            [
                Limit("execution:user:minute:" + str(user), 2, 60),
                Limit("execution:user:day:" + str(user), 10, 86400),
                Limit("execution:deployment:day", 50, 86400),
            ]
        )
        run = ExecutionRun(
            agent_run_id=agent_id,
            request_key=data.request_key,
            profile=data.profile,
            patch_sha256=proposal.diff_sha256,
        )
        self.db.add(run)
        await self.db.flush()
        result = ExecutionResponse.model_validate(run)
        await self.db.commit()
        return result, True

    async def cancel(self, user: UUID, run_id: UUID) -> ExecutionResponse:
        await self.get(user, run_id)
        row = await self.db.scalar(
            select(ExecutionRun)
            .where(ExecutionRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.status not in {"queued", "running"}:
            raise AppError("execution_terminal", "Execution already finished.", 409)
        row.status, row.stage, row.finished_at = "cancelled", "cancelled", datetime.now(UTC)
        row.lease_token, row.lease_expires_at = None, None
        result = ExecutionResponse.model_validate(row)
        await self.db.commit()
        return result
