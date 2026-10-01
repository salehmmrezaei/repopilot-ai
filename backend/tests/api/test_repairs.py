import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from test_agents import worker
from test_executions import Github, Runner, execute, setup
from test_proposals import ProposalProvider
from test_repositories import sign_in

from app.jobs.repair_dispatcher import advance_repairs
from app.models import AgentRun, Conversation, RepairRun, RepositoryFile


def submit(client, headers, agent, key=None, **extra):
    client.app.state.settings.repairs_enabled = True
    return client.post(
        "/agent-runs/" + agent["id"] + "/repairs",
        headers=headers,
        json={"request_key": str(key or uuid4()), "confirm_automatic_repair": True, **extra},
    )


def tick(client):
    asyncio.run(
        advance_repairs(
            client.app.state.test_factory, client.app.state.settings, client.app.state.rate_limiter
        )
    )


def detail(client, run):
    response = client.get("/repairs/" + run["id"])
    assert response.status_code == 200, response.text
    return response.json()


class FailedRunner(Runner):
    async def request(self, method, run_id, body=None):
        result = await super().request(method, run_id, body)
        if result.patched:
            result.patched.status, result.patched.exit_code = "failed", 1
        return result


class RevisionProvider(ProposalProvider):
    def __init__(self, path, number):
        super().__init__(path)
        self.number = number

    async def decide(self, payload):
        result = await super().decide(payload)
        if result.decision.proposal:
            result.decision.proposal.edits[0].new_text = f"print('revision {self.number}')"
        return result


def test_repair_success_idempotency_and_original_unchanged(auth_client):
    headers, _, agent = setup(auth_client)
    key = uuid4()
    response = submit(auth_client, headers, agent, key)
    assert response.status_code == 202, response.text
    run = response.json()
    assert submit(auth_client, headers, agent, key).json()["id"] == run["id"]
    assert submit(auth_client, headers, agent, key, max_revisions=1).status_code == 409
    assert submit(auth_client, headers, agent).status_code == 409
    tick(auth_client)
    tick(auth_client)
    before = detail(auth_client, run)
    execution = before["attempts"][0]["execution"]
    assert execution and len(before["attempts"]) == 1
    execute(auth_client, execution, Github(), Runner())
    tick(auth_client)
    after = detail(auth_client, run)
    assert after["run"]["status"] == "passed" and after["run"]["revision"] == 0
    assert len(after["attempts"][0]["calls"]) == 2
    tick(auth_client)
    assert detail(auth_client, run) == after

    async def check():
        async with auth_client.app.state.test_factory() as db:
            assert await db.scalar(select(RepositoryFile.content)) == "print('hello')"

    asyncio.run(check())


def test_failure_revision_success_preserves_task_source_and_feedback(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    first = detail(auth_client, run)["attempts"][0]
    execute(auth_client, first["execution"], Github(), FailedRunner())
    tick(auth_client)
    second = detail(auth_client, run)["attempts"][1]["agent"]
    assert second["question"] == agent["question"] and second["commit_sha"] == agent["commit_sha"]
    path = first["agent"]["result"]["proposal"]["files"][0]["path"]
    worker(auth_client, second["id"], RevisionProvider(path, 1))
    tick(auth_client)
    execution = detail(auth_client, run)["attempts"][1]["execution"]
    execute(auth_client, execution, Github(), Runner())
    tick(auth_client)
    result = detail(auth_client, run)
    assert result["run"]["status"] == "passed" and result["run"]["revision"] == 1
    assert len(result["attempts"]) == 2

    async def check():
        async with auth_client.app.state.test_factory() as db:
            saved = await db.get(AgentRun, UUID(second["id"]))
            assert saved.execution_feedback["depth"] == 1
            assert saved.execution_feedback["execution_id"] == first["execution"]["id"]

    asyncio.run(check())


def test_two_revisions_exhaust_without_a_fourth_attempt(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    for revision in range(3):
        auth_client.app.state.rate_limiter.values.clear()
        if revision:
            attempt = detail(auth_client, run)["attempts"][-1]["agent"]
            worker(
                auth_client,
                attempt["id"],
                RevisionProvider(
                    detail(auth_client, run)["attempts"][0]["agent"]["result"]["proposal"]["files"][
                        0
                    ]["path"],
                    revision,
                ),
            )
        tick(auth_client)
        attempt = detail(auth_client, run)["attempts"][-1]
        assert attempt["execution"], attempt
        execute(auth_client, attempt["execution"], Github(), FailedRunner())
        tick(auth_client)
    result = detail(auth_client, run)
    assert result["run"]["status"] == "exhausted"
    assert len(result["attempts"]) == 3
    tick(auth_client)
    assert detail(auth_client, run) == result


def test_repeated_patch_stops_before_another_execution(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    attempt = detail(auth_client, run)["attempts"][0]
    execute(auth_client, attempt["execution"], Github(), FailedRunner())
    tick(auth_client)
    revision = detail(auth_client, run)["attempts"][-1]["agent"]
    worker(
        auth_client,
        revision["id"],
        ProposalProvider(attempt["agent"]["result"]["proposal"]["files"][0]["path"]),
    )
    tick(auth_client)
    final = detail(auth_client, run)
    assert final["run"]["reason"] == "repeated_patch"
    assert final["attempts"][-1]["execution"] is None


@pytest.mark.parametrize("phase", ["before_execution", "execution", "agent"])
def test_cancel_fences_children_and_no_further_work(auth_client, phase):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    if phase != "before_execution":
        tick(auth_client)
    if phase == "agent":
        execute(
            auth_client,
            detail(auth_client, run)["attempts"][0]["execution"],
            Github(),
            FailedRunner(),
        )
        tick(auth_client)
    cancelled = auth_client.post("/repairs/" + run["id"] + "/cancel", headers=headers, json={})
    assert cancelled.status_code == 200
    tick(auth_client)
    result = detail(auth_client, run)
    assert result["run"]["status"] == "cancelled"
    assert all(a["agent"]["status"] not in {"queued", "running"} for a in result["attempts"])
    assert all(
        not a["execution"] or a["execution"]["status"] not in {"queued", "running"}
        for a in result["attempts"]
    )
    assert (
        auth_client.post("/repairs/" + run["id"] + "/cancel", headers=headers, json={}).status_code
        == 200
    )


@pytest.mark.parametrize(
    "reason", ["deadline", "disabled", "model_configuration_changed", "resource_limit"]
)
def test_terminal_stops(auth_client, reason):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    if reason == "disabled":
        auth_client.app.state.settings.repairs_enabled = False
    elif reason == "model_configuration_changed":
        auth_client.app.state.settings.answer_model = "different-model"
    elif reason == "deadline":

        async def expire():
            async with auth_client.app.state.test_factory() as db:
                await db.execute(
                    update(RepairRun).values(deadline=datetime.now(UTC) - timedelta(seconds=1))
                )
                await db.commit()

        asyncio.run(expire())
    else:

        class Limited(Runner):
            async def request(self, method, run_id, body=None):
                result = await super().request(method, run_id, body)
                result.patched.status = "timeout"
                return result

        execute(
            auth_client, detail(auth_client, run)["attempts"][0]["execution"], Github(), Limited()
        )
    tick(auth_client)
    result = detail(auth_client, run)
    assert result["run"]["status"] == "stopped"
    assert result["run"]["reason"] == (
        reason if reason != "resource_limit" else "execution_unavailable_or_resource_limit"
    )


def test_confirmation_owner_and_cascade(auth_client):
    headers, _, agent = setup(auth_client)
    endpoint = "/agent-runs/" + agent["id"] + "/repairs"
    assert (
        auth_client.post(endpoint, headers=headers, json={"request_key": str(uuid4())}).status_code
        == 422
    )
    assert submit(auth_client, {}, agent).status_code == 403
    assert submit(auth_client, headers, agent, max_revisions=3).status_code == 422
    run = submit(auth_client, headers, agent).json()
    outsider = sign_in(auth_client, "repair-outsider@example.com")
    assert auth_client.get("/repairs/" + run["id"]).status_code == 404
    assert auth_client.get(endpoint).status_code == 404
    assert (
        auth_client.post("/repairs/" + run["id"] + "/cancel", headers=outsider, json={}).status_code
        == 404
    )

    async def remove():
        async with auth_client.app.state.test_factory() as db:
            await db.execute(
                delete(Conversation).where(Conversation.id == UUID(agent["conversation_id"]))
            )
            await db.commit()
            assert await db.get(RepairRun, UUID(run["id"])) is None

    asyncio.run(remove())
    tick(auth_client)


def test_execution_environment_change_stops_repair(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    first = detail(auth_client, run)["attempts"][0]
    execute(auth_client, first["execution"], Github(), FailedRunner())
    tick(auth_client)
    child = detail(auth_client, run)["attempts"][-1]["agent"]
    worker(
        auth_client,
        child["id"],
        RevisionProvider(first["agent"]["result"]["proposal"]["files"][0]["path"], 1),
    )
    tick(auth_client)

    class Changed(Runner):
        async def request(self, method, run_id, body=None):
            result = await super().request(method, run_id, body)
            result.image_id = "sha256:" + "c" * 64
            return result

    execute(auth_client, detail(auth_client, run)["attempts"][-1]["execution"], Github(), Changed())
    tick(auth_client)
    assert detail(auth_client, run)["run"]["reason"] == "execution_environment_changed"


def test_quota_failure_rolls_back_child_and_stops_without_retry(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    from app.core.errors import AppError

    class Denied:
        async def check(self, limits):
            raise AppError("rate_limited", "quota", 429)

    auth_client.app.state.rate_limiter = Denied()
    tick(auth_client)
    after = detail(auth_client, run)
    assert after["run"]["reason"] == "rate_limited"
    assert after["attempts"][0]["execution"] is None
    tick(auth_client)
    assert detail(auth_client, run) == after


def test_worker_checks_deadline_without_dispatcher(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    execution = detail(auth_client, run)["attempts"][0]["execution"]

    async def expire():
        async with auth_client.app.state.test_factory() as db:
            await db.execute(
                update(RepairRun).values(deadline=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.commit()

    asyncio.run(expire())
    runner, github = Runner(), Github()
    execute(auth_client, execution, github, runner)
    assert "POST" not in runner.methods and not github.sources
    tick(auth_client)
    assert detail(auth_client, run)["run"]["reason"] == "deadline"


def test_changed_source_rolls_back_new_agent(auth_client, monkeypatch):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    execute(
        auth_client, detail(auth_client, run)["attempts"][0]["execution"], Github(), FailedRunner()
    )
    from app.services.agents import AgentService

    original = AgentService.submit

    async def changed(self, *args, **kwargs):
        response, created = await original(self, *args, **kwargs)
        response.source_index_id = uuid4()
        return response, created

    monkeypatch.setattr(AgentService, "submit", changed)
    tick(auth_client)
    after = detail(auth_client, run)
    assert after["run"]["reason"] == "source_changed"
    assert len(after["attempts"]) == 1

    async def count():
        async with auth_client.app.state.test_factory() as db:
            assert len(list(await db.scalars(select(AgentRun)))) == 1

    asyncio.run(count())


def test_expired_revision_cannot_start_model_call(auth_client):
    headers, _, agent = setup(auth_client)
    run = submit(auth_client, headers, agent).json()
    tick(auth_client)
    first = detail(auth_client, run)["attempts"][0]
    execute(auth_client, first["execution"], Github(), FailedRunner())
    tick(auth_client)
    child = detail(auth_client, run)["attempts"][-1]["agent"]

    async def expire():
        async with auth_client.app.state.test_factory() as db:
            await db.execute(
                update(RepairRun).values(deadline=datetime.now(UTC) - timedelta(seconds=1))
            )
            saved = await db.get(AgentRun, UUID(child["id"]))
            assert (
                saved.execution_feedback["prior_patch"]["diff_sha256"]
                == first["execution"]["patch_sha256"]
            )
            await db.commit()

    asyncio.run(expire())
    provider = RevisionProvider(first["agent"]["result"]["proposal"]["files"][0]["path"], 1)
    worker(auth_client, child["id"], provider)
    assert provider.calls == 0
    tick(auth_client)
    assert detail(auth_client, run)["run"]["reason"] == "deadline"
