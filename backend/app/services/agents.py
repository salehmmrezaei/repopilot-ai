from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import InvestigationResult
from app.agents.provider import configuration
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limits import Limit, RateLimiter
from app.execution.contracts import SandboxResult
from app.models import AgentCall, AgentEvent, AgentRun, Conversation, ExecutionRun
from app.repair.policy import feedback as execution_feedback
from app.repositories.agent import event, owned
from app.repositories.conversation import ConversationStore
from app.schemas.agent import (
    AgentCallResponse,
    AgentDetail,
    AgentEventResponse,
    AgentList,
    AgentRequest,
    AgentResponse,
)
from app.services.search_preparation import PreparationService


class AgentService:
    def __init__(self, db: AsyncSession, settings: Settings, limiter: RateLimiter) -> None:
        self.db, self.settings, self.limiter = db, settings, limiter

    async def list(self, user_id: UUID, conversation_id: UUID) -> AgentList:
        await ConversationStore(self.db).owned(user_id, conversation_id)
        rows = await self.db.scalars(
            select(AgentRun)
            .where(AgentRun.conversation_id == conversation_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(20)
        )
        return AgentList(
            enabled=self.settings.agents_enabled,
            items=[AgentResponse.model_validate(row) for row in rows],
        )

    async def get(self, user_id: UUID, run_id: UUID) -> AgentDetail:
        run = await owned(self.db, user_id, run_id)
        events = await self.db.scalars(
            select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.sequence)
        )
        calls = await self.db.scalars(
            select(AgentCall).where(AgentCall.run_id == run_id).order_by(AgentCall.step)
        )
        return AgentDetail(
            run=AgentResponse.model_validate(run),
            events=[AgentEventResponse.model_validate(row) for row in events],
            calls=[AgentCallResponse.model_validate(row) for row in calls],
        )

    async def submit(
        self, user_id: UUID, conversation_id: UUID, data: AgentRequest, *, commit: bool = True
    ) -> tuple[AgentResponse, bool]:
        conv = await ConversationStore(self.db).owned(user_id, conversation_id)
        await self.db.execute(
            select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
        )
        previous = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.request_key == data.request_key,
            )
        )
        if previous:
            previous_feedback = (previous.execution_feedback or {}).get("execution_id")
            if (
                previous.question != data.question.strip()
                or previous.mode != data.mode
                or previous_feedback
                != (str(data.feedback_execution_id) if data.feedback_execution_id else None)
            ):
                raise AppError(
                    "idempotency_conflict", "Request key already used for another task.", 409
                )
            return AgentResponse.model_validate(previous), False
        if not self.settings.agents_enabled:
            raise AppError(
                "agent_disabled",
                "Read-only investigations are disabled in server configuration.",
                409,
            )
        active = await self.db.scalar(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.status.in_(["queued", "running"]),
            )
        )
        count = await self.db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.conversation_id == conversation_id)
        )
        if active or (count or 0) >= 200:
            raise AppError(
                "agent_limit",
                "An investigation is active or this conversation reached its run limit.",
                409,
            )
        preparation = PreparationService(self.db, self.limiter, self.settings)
        source = await preparation.source(user_id, conv.repository_id)
        if not (await preparation.state(user_id, conv.repository_id)).keyword:
            raise AppError("search_required", "Prepare keyword search before investigating.", 409)
        await self.limiter.check(
            [
                Limit("agent:user:minute:" + str(user_id), 3, 60),
                Limit("agent:user:day:" + str(user_id), 10, 86400),
                Limit("agent:deployment:day", self.settings.agent_daily_request_limit, 86400),
            ]
        )
        feedback = None
        if data.feedback_execution_id is not None:
            execution = await self.db.get(ExecutionRun, data.feedback_execution_id)
            if execution is None:
                raise AppError("not_found", "Execution feedback not found.", 404)
            parent = await owned(self.db, user_id, execution.agent_run_id)
            if (
                data.mode != "propose"
                or parent.conversation_id != conversation_id
                or parent.source_index_id != source.id
                or execution.status not in {"completed", "failed"}
                or not execution.result
            ):
                raise AppError(
                    "feedback_invalid",
                    "Feedback requires terminal execution on this conversation and source.",
                    409,
                )
            depth = int(str((parent.execution_feedback or {}).get("depth", 0))) + 1
            if depth > 2:
                raise AppError(
                    "feedback_limit",
                    "Two feedback revisions reached. Start a new reviewed task.",
                    409,
                )
            sandbox_result = SandboxResult.model_validate(execution.result)
            feedback = {
                **execution_feedback(
                    sandbox_result,
                    InvestigationResult.model_validate(parent.result).proposal
                    if parent.result
                    else None,
                ),
                "execution_id": str(execution.id),
                "depth": depth,
            }
        run = AgentRun(
            conversation_id=conversation_id,
            request_key=data.request_key,
            question=data.question.strip(),
            mode=data.mode,
            execution_feedback=feedback,
            source_index_id=source.id,
            commit_sha=source.commit_sha,
            config_hash=configuration(self.settings, data.mode),
            model=self.settings.answer_model,
        )
        self.db.add(run)
        try:
            await self.db.flush()
            await event(self.db, run.id, "queued", "Read-only investigation queued.")
            result = AgentResponse.model_validate(run)
            if commit:
                await self.db.commit()
            return result, True
        except IntegrityError:
            if commit:
                await self.db.rollback()
            raise AppError(
                "agent_conflict",
                "Concurrent submission changed this conversation. "
                "Refresh and retry the same request key.",
                409,
            ) from None

    async def cancel(self, user_id: UUID, run_id: UUID) -> AgentResponse:
        await owned(self.db, user_id, run_id)
        run = await self.db.scalar(
            select(AgentRun)
            .where(AgentRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None or run.status not in {"queued", "running"}:
            raise AppError(
                "agent_terminal", "Investigation already finished. Refresh its status.", 409
            )
        run.status, run.finished_at, run.lease_token, run.lease_expires_at = (
            "cancelled",
            datetime.now(UTC),
            None,
            None,
        )
        await event(
            self.db, run_id, "cancelled", "Cancelled. In-flight model charges may still occur."
        )
        result = AgentResponse.model_validate(run)
        await self.db.commit()
        return result
