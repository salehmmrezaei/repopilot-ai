import asyncio
import os
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.contracts import InvestigationResult
from app.agents.proposals import VerifiedProposal
from app.agents.provider import configuration
from app.core.config import Settings
from app.core.rate_limits import MemoryRateLimiter
from app.database.session import create_engine
from app.generation.contracts import AnswerDraft
from app.jobs.repair_dispatcher import advance_repairs
from app.models import AgentRun, Conversation, ExecutionRun, Repository, User
from app.models.repair import RepairRun
from app.schemas.repair import RepairRequest
from app.services.repairs import RepairService


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1", reason="requires migrated isolated PostgreSQL"
)
def test_concurrent_repair_transition_and_cancellation_are_atomic():
    async def scenario():
        settings = Settings().model_copy(
            update={"executions_enabled": True, "agents_enabled": True, "repairs_enabled": True}
        )
        assert settings.database_url.get_secret_value().split("?")[0].endswith("_test")
        engine = create_engine(settings)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user, repo, conversation, agent, execution = (uuid4() for _ in range(5))
        proposal = VerifiedProposal(
            implementation_plan=["fixture"],
            risks=["fixture"],
            test_plan=["fixture"],
            files=[],
            diff="fixture",
            diff_sha256=sha256(b"fixture").hexdigest(),
        )
        result = InvestigationResult(
            answer=AnswerDraft(status="insufficient_evidence", claims=[], limitation="fixture"),
            evidence=[],
            proposal=proposal,
        )

        try:
            async with factory() as db:
                db.add(
                    User(id=user, email=f"{user}@example.com", name="test", password_hash="unused")
                )
                await db.flush()
                db.add(
                    Repository(
                        id=repo, user_id=user, source_key=str(repo), owner="fixture", name="fixture"
                    )
                )
                await db.flush()
                db.add(
                    Conversation(
                        id=conversation, user_id=user, repository_id=repo, title="execution"
                    )
                )
                await db.flush()
                db.add(
                    AgentRun(
                        id=agent,
                        conversation_id=conversation,
                        request_key=uuid4(),
                        question="fixture",
                        source_index_id=uuid4(),
                        commit_sha="a" * 40,
                        config_hash=configuration(settings, "propose"),
                        model="fixture",
                        mode="propose",
                        status="completed",
                        result=result.model_dump(mode="json"),
                    )
                )
                await db.flush()
                await db.commit()
            limiter = MemoryRateLimiter()
            key = uuid4()

            async def submit():
                async with factory() as db:
                    return await RepairService(db, settings, limiter).submit(
                        user, agent, RepairRequest(request_key=key, confirm_automatic_repair=True)
                    )

            submissions = await asyncio.gather(submit(), submit())
            run_id = submissions[0][0].id
            assert submissions[1][0].id == run_id
            assert sum(created for _, created in submissions) == 1
            await asyncio.gather(
                advance_repairs(factory, settings, limiter),
                advance_repairs(factory, settings, limiter),
            )
            async with factory() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(ExecutionRun)
                        .where(ExecutionRun.agent_run_id == agent)
                    )
                    == 1
                )

            async def cancel():
                async with factory() as db:
                    await RepairService(db, settings, limiter).cancel(user, run_id)

            await asyncio.gather(advance_repairs(factory, settings, limiter), cancel())
            await advance_repairs(factory, settings, limiter)
            async with factory() as db:
                assert (await db.get(RepairRun, run_id)).status == "cancelled"
                execution_row = await db.scalar(
                    select(ExecutionRun).where(ExecutionRun.agent_run_id == agent)
                )
                assert execution_row.status == "cancelled"
        finally:
            async with engine.begin() as conn:
                await conn.execute(delete(User).where(User.id == user))
            await engine.dispose()

    asyncio.run(scenario())
