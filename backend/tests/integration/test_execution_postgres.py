import asyncio
import os
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.contracts import InvestigationResult
from app.agents.proposals import VerifiedProposal
from app.core.config import Settings
from app.database.session import create_engine
from app.execution.contracts import CommandResult, SandboxResult
from app.generation.contracts import AnswerDraft
from app.jobs.execution_run import run_execution
from app.models import AgentRun, Conversation, ExecutionRun, Repository, User


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1", reason="requires migrated isolated PostgreSQL"
)
def test_concurrent_execution_delivery_claims_once():
    async def scenario():
        settings = Settings().model_copy(update={"executions_enabled": True})
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

        class Github:
            async def download(self, source):
                return b"archive"

        class Runner:
            calls = 0

            async def request(self, method, run_id, body=None):
                self.calls += 1
                phase = CommandResult(
                    status="passed", exit_code=0, log="fixture", truncated=False, duration_ms=1
                )
                return SandboxResult(
                    status="completed",
                    request_hash=sha256(body.model_dump_json().encode()).hexdigest(),
                    image_id="sha256:" + "a" * 64,
                    archive_sha256=sha256(b"archive").hexdigest(),
                    profile="python",
                    command=["pytest"],
                    preparation=phase,
                    baseline=phase,
                    patched=phase,
                )

        runner = Runner()
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
                        config_hash="a" * 64,
                        model="fixture",
                        mode="propose",
                        status="completed",
                        result=result.model_dump(mode="json"),
                    )
                )
                await db.flush()
                db.add(
                    ExecutionRun(
                        id=execution,
                        agent_run_id=agent,
                        request_key=uuid4(),
                        profile="python",
                        patch_sha256=proposal.diff_sha256,
                    )
                )
                await db.commit()
            await asyncio.gather(
                run_execution(factory, execution, settings, Github(), runner, 0),
                run_execution(factory, execution, settings, Github(), runner, 0),
            )
            assert runner.calls == 1
            async with factory() as db:
                assert (await db.get(ExecutionRun, execution)).status == "completed"
        finally:
            async with engine.begin() as conn:
                await conn.execute(delete(User).where(User.id == user))
            await engine.dispose()

    asyncio.run(scenario())
