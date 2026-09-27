import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.contracts import Decision, ProposalDecision, StepResult
from app.agents.provider import configuration
from app.core.config import Settings
from app.database.session import create_engine
from app.jobs.agent_run import run_agent
from app.models import AgentCall, AgentRun, Conversation, Repository, User


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1", reason="requires migrated isolated PostgreSQL"
)
@pytest.mark.parametrize("mode", ["investigate", "propose"])
def test_duplicate_agent_delivery_claims_once_on_postgres(mode):
    async def scenario():
        settings = Settings().model_copy(update={"agents_enabled": True})
        assert settings.database_url.get_secret_value().split("?")[0].endswith("_test")
        engine = create_engine(settings)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id, repo_id, conversation_id, run_id = (uuid4() for _ in range(4))

        class Provider:
            calls = 0

            async def decide(self, payload):
                self.calls += 1
                return StepResult(
                    decision=(ProposalDecision if mode == "propose" else Decision).model_validate(
                        {
                            **({"proposal": None} if mode == "propose" else {}),
                            "plan": ["Assess available evidence"],
                            "action": None,
                            "answer": {
                                "status": "insufficient_evidence",
                                "claims": [],
                                "limitation": "No evidence retrieved.",
                            },
                        }
                    ),
                    input_tokens=100,
                    output_tokens=40,
                )

        provider = Provider()

        def build(*args):
            raise AssertionError("Abstention must not invoke a tool")

        try:
            async with factory() as db:
                db.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.com",
                        name="Test",
                        password_hash="unused",
                    )
                )
                await db.flush()
                db.add(
                    Repository(
                        id=repo_id,
                        user_id=user_id,
                        source_key=str(repo_id),
                        owner="fixture",
                        name="fixture",
                    )
                )
                await db.flush()
                db.add(
                    Conversation(
                        id=conversation_id, user_id=user_id, repository_id=repo_id, title="Agent"
                    )
                )
                await db.flush()
                db.add(
                    AgentRun(
                        id=run_id,
                        conversation_id=conversation_id,
                        request_key=uuid4(),
                        question="Investigate",
                        source_index_id=uuid4(),
                        commit_sha="a" * 40,
                        model=settings.answer_model,
                        mode=mode,
                        config_hash=configuration(settings, mode),
                    )
                )
                await db.commit()
            await asyncio.gather(
                run_agent(factory, run_id, settings, provider, build),
                run_agent(factory, run_id, settings, provider, build),
            )
            assert provider.calls == 1
            async with factory() as db:
                run = await db.get(AgentRun, run_id)
                assert run.status == "completed"
                assert (
                    len(
                        list(
                            (
                                await db.scalars(
                                    select(AgentCall).where(AgentCall.run_id == run_id)
                                )
                            ).all()
                        )
                    )
                    == 1
                )
        finally:
            async with engine.begin() as conn:
                await conn.execute(delete(User).where(User.id == user_id))
            await engine.dispose()

    asyncio.run(scenario())
