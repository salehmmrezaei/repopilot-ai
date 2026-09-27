import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from test_conversations import setup
from test_repositories import sign_in

from app.agents.contracts import Decision, StepResult
from app.agents.tools import RepositoryTools
from app.core.errors import AppError
from app.jobs.agent_dispatcher import dispatch_agents
from app.jobs.agent_run import run_agent
from app.models import AgentCall, AgentRun, RepositoryFile
from app.services.search import SearchService


class Provider:
    def __init__(self, path, failure=None):
        self.path, self.failure, self.calls = path, failure, 0

    async def decide(self, payload):
        self.calls += 1
        if self.failure == "provider":
            raise AppError("unavailable", "Provider unavailable", 503)
        data = json.loads(payload)
        if self.calls == 1:
            decision = {
                "plan": ["Read repository file"],
                "action": {"tool": "read_file", "path": self.path, "start_line": 1, "query": None},
                "answer": None,
            }
        else:
            decision = {
                "plan": [],
                "action": None,
                "answer": {
                    "status": "answered",
                    "claims": [
                        {
                            "text": "Prints hello.",
                            "citation_ids": [
                                "C999"
                                if self.failure == "citation"
                                else data["evidence"][0]["citation_id"]
                            ],
                        }
                    ],
                    "limitation": "",
                },
            }
        return StepResult(
            decision=Decision.model_validate(decision), input_tokens=100, output_tokens=40
        )


def prepare(client):
    headers, repo, url, _ = setup(client)
    client.app.state.settings.agents_enabled = True

    async def path():
        async with client.app.state.test_factory() as db:
            return await db.scalar(select(RepositoryFile.path))

    return headers, repo, url, Provider(asyncio.run(path()))


def submit(client, headers, url, key=None, question="Explain the entry point"):
    return client.post(
        url + "/investigations",
        json={"request_key": str(key or uuid4()), "question": question},
        headers=headers,
    )


def worker(client, run_id, provider):
    def build(db, user_id, repository_id, index_id):
        return RepositoryTools(
            db,
            SearchService(db, None, client.app.state.rate_limiter, client.app.state.settings, None),
            user_id,
            repository_id,
            index_id,
        )

    asyncio.run(
        run_agent(
            client.app.state.test_factory, UUID(run_id), client.app.state.settings, provider, build
        )
    )


def test_agent_end_to_end_replay_citations_usage_and_events(auth_client):
    headers, _, url, provider = prepare(auth_client)
    key = uuid4()
    response = submit(auth_client, headers, url, key)
    assert response.status_code == 202, response.text
    run = response.json()
    path = "/agent-runs/" + run["id"]
    assert submit(auth_client, headers, url, key).json()["id"] == run["id"]
    assert submit(auth_client, headers, url, key, "different").status_code == 409
    worker(auth_client, run["id"], provider)
    worker(auth_client, run["id"], provider)
    detail = auth_client.get(path).json()
    assert detail["run"]["status"] == "completed", detail
    assert len(detail["calls"]) == 2 and provider.calls == 2
    assert all(call["input_tokens"] == 100 for call in detail["calls"])
    assert detail["run"]["result"]["evidence"][0]["content"] == "print('hello')"
    assert [e["sequence"] for e in detail["events"]] == list(range(1, len(detail["events"]) + 1))
    assert (
        auth_client.get(url + "/messages").json()["items"] == []
    )  # independent investigation history
    wire = auth_client.get(
        path + "/events", headers={"X-RepoPilot-Request": "1", "Last-Event-ID": "2"}
    )
    assert (
        wire.status_code == 200 and "id: 1\n" not in wire.text and "event: stream.end" in wire.text
    )
    assert (
        auth_client.get(
            path + "/events", headers={"X-RepoPilot-Request": "1", "Last-Event-ID": "32"}
        ).status_code
        == 409
    )
    assert submit(auth_client, headers, url, key).status_code == 200
    assert auth_client.delete(url, headers=headers).status_code == 204
    assert auth_client.get(path).status_code == 404

    async def gone():
        async with auth_client.app.state.test_factory() as db:
            assert await db.scalar(select(AgentCall)) is None

    asyncio.run(gone())


@pytest.mark.parametrize("failure", ["citation", "provider"])
def test_failure_preserves_receipts_and_never_retries(auth_client, failure):
    headers, _, url, provider = prepare(auth_client)
    provider.failure = failure
    run = submit(auth_client, headers, url).json()
    worker(auth_client, run["id"], provider)
    worker(auth_client, run["id"], provider)
    detail = auth_client.get("/agent-runs/" + run["id"]).json()
    assert detail["run"]["status"] == "failed" and detail["run"]["result"] is None
    assert detail["calls"][0]["input_tokens"] == (None if failure == "provider" else 100)
    assert provider.calls == (1 if failure == "provider" else 2)


def test_cancel_during_provider_preserves_usage_without_next_tool(auth_client):
    headers, _, url, provider = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    original = provider.decide

    async def decide(payload):
        async with auth_client.app.state.test_factory() as db:
            assert await db.get(AgentCall, (UUID(run["id"]), 1)) is not None
            await db.execute(update(AgentRun).values(status="cancelled", lease_token=None))
            await db.commit()
        return await original(payload)

    provider.decide = decide
    worker(auth_client, run["id"], provider)
    detail = auth_client.get("/agent-runs/" + run["id"]).json()
    assert detail["run"]["status"] == "cancelled" and detail["run"]["result"] is None
    assert provider.calls == 1 and detail["calls"][0]["input_tokens"] == 100
    assert not any(e["kind"] == "tool_started" for e in detail["events"])


def test_owner_and_writer_protections(auth_client):
    headers, _, url, provider = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    path = "/agent-runs/" + run["id"]
    assert auth_client.post(path + "/cancel", json={}).status_code == 403
    assert auth_client.get(path + "/events").status_code == 403
    other = sign_in(auth_client, "agent-outsider@example.com")
    assert auth_client.get(path).status_code == 404
    assert auth_client.get(url + "/investigations").status_code == 404
    assert submit(auth_client, other, url).status_code == 404
    assert auth_client.post(path + "/cancel", json={}, headers=other).status_code == 404
    assert (
        auth_client.get(path + "/events", headers={"X-RepoPilot-Request": "1"}).status_code == 404
    )
    assert provider.calls == 0


def test_queued_cancel_and_dispatcher_expiry_make_no_calls(auth_client):
    headers, _, url, provider = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    path = "/agent-runs/" + run["id"]
    assert auth_client.post(path + "/cancel", json={}, headers=headers).status_code == 200
    worker(auth_client, run["id"], provider)
    assert provider.calls == 0
    second = submit(auth_client, headers, url).json()

    async def expire():
        async with auth_client.app.state.test_factory() as db:
            await db.execute(
                update(AgentRun)
                .where(AgentRun.id == UUID(second["id"]))
                .values(status="running", lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.commit()

        async def publish(run_id):
            raise AssertionError("Never retry running paid work")

        await dispatch_agents(auth_client.app.state.test_factory, publish)

    asyncio.run(expire())
    worker(auth_client, second["id"], provider)
    assert auth_client.get("/agent-runs/" + second["id"]).json()["run"]["status"] == "failed"
    assert provider.calls == 0
