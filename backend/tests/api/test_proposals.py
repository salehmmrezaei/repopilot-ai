import asyncio
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from test_agents import prepare, worker
from test_repositories import sign_in

from app.agents.contracts import ProposalDecision, StepResult
from app.models import AgentRun, RepositoryFile, RepositoryIndex


class ProposalProvider:
    def __init__(self, path, invalid=False):
        self.path, self.invalid, self.calls = path, invalid, 0

    async def decide(self, payload):
        self.calls += 1
        if self.calls == 1:
            value = dict(
                plan=["Read source"],
                action=dict(tool="read_file", path=self.path, start_line=1, query=None),
                answer=None,
                proposal=None,
            )
        else:
            source = json.loads(payload)["evidence"][0]
            value = dict(
                plan=[],
                action=None,
                answer=dict(
                    status="answered",
                    claims=[
                        dict(
                            text="Change the greeting in the inspected entry point.",
                            citation_ids=[source["citation_id"]],
                        )
                    ],
                    limitation="Tests have not run.",
                ),
                proposal=dict(
                    implementation_plan=["Change the greeting"],
                    risks=["Output changes"],
                    test_plan=["Check stdout"],
                    edits=[
                        dict(
                            path=self.path,
                            operation="replace",
                            start_line=1,
                            end_line=1,
                            old_text="invented" if self.invalid else source["content"],
                            new_text="print('updated')",
                        )
                    ],
                ),
            )
        return StepResult(
            decision=ProposalDecision.model_validate(value), input_tokens=100, output_tokens=70
        )


def submit(client, headers, url, key=None, mode="propose"):
    return client.post(
        url + "/investigations",
        headers=headers,
        json=dict(request_key=str(key or uuid4()), question="Update the greeting", mode=mode),
    )


def test_proposal_download_replay_provenance_and_no_source_mutation(auth_client):
    headers, _, url, previous = prepare(auth_client)
    key = uuid4()
    run = submit(auth_client, headers, url, key).json()
    path = "/agent-runs/" + run["id"]
    assert auth_client.get(path + "/patch").status_code == 409
    assert submit(auth_client, headers, url, key, "investigate").status_code == 409
    provider = ProposalProvider(previous.path)
    worker(auth_client, run["id"], provider)
    worker(auth_client, run["id"], provider)
    detail = auth_client.get(path).json()
    assert detail["run"]["status"] == "completed", detail
    proposal = detail["run"]["result"]["proposal"]
    assert proposal["validation"] == "source_checked_tests_not_run"
    assert provider.calls == 2 and len(detail["calls"]) == 2
    assert submit(auth_client, headers, url, key).json()["id"] == run["id"]
    download = auth_client.get(path + "/patch")
    assert download.status_code == 200 and download.text == proposal["diff"]
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "no-store"
    assert any(e["kind"] == "proposal_validated" for e in detail["events"])

    async def source_unchanged():
        async with auth_client.app.state.test_factory() as db:
            assert await db.scalar(select(RepositoryFile.content)) == "print('hello')"
            await db.execute(delete(RepositoryIndex))
            await db.commit()

    asyncio.run(source_unchanged())
    # Review and download use saved immutable results, even after index deletion.
    assert auth_client.get(path + "/patch").text == proposal["diff"]
    assert auth_client.get(url + "/messages").json()["items"] == []
    assert auth_client.delete(url, headers=headers).status_code == 204
    assert auth_client.get(path + "/patch").status_code == 404


def test_invalid_proposal_preserves_usage_but_publishes_nothing(auth_client):
    headers, _, url, previous = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    provider = ProposalProvider(previous.path, invalid=True)
    worker(auth_client, run["id"], provider)
    path = "/agent-runs/" + run["id"]
    detail = auth_client.get(path).json()
    assert detail["run"]["status"] == "failed"
    assert detail["run"]["error_code"] == "proposal_source_invalid"
    assert detail["run"]["result"] is None
    assert len(detail["calls"]) == 2 and detail["calls"][1]["input_tokens"] == 100
    assert auth_client.get(path + "/patch").status_code == 409


def test_proposal_cancel_during_final_model_call_never_publishes(auth_client):
    headers, _, url, previous = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    provider = ProposalProvider(previous.path)
    original = provider.decide

    async def decide(payload):
        result = await original(payload)
        if provider.calls == 2:
            async with auth_client.app.state.test_factory() as db:
                await db.execute(
                    update(AgentRun)
                    .where(AgentRun.id == UUID(run["id"]))
                    .values(status="cancelled", lease_token=None)
                )
                await db.commit()
        return result

    provider.decide = decide
    worker(auth_client, run["id"], provider)
    detail = auth_client.get("/agent-runs/" + run["id"]).json()
    assert detail["run"]["status"] == "cancelled" and detail["run"]["result"] is None
    assert detail["calls"][1]["input_tokens"] == 100


def test_proposal_download_owner_and_writer_protection(auth_client):
    headers, _, url, previous = prepare(auth_client)
    assert submit(auth_client, {}, url).status_code == 403
    run = submit(auth_client, headers, url).json()
    worker(auth_client, run["id"], ProposalProvider(previous.path))
    sign_in(auth_client, "foreign-proposal@example.com")
    assert auth_client.get("/agent-runs/" + run["id"] + "/patch").status_code == 404


@pytest.mark.parametrize("mode", ["execute", "apply", "shell"])
def test_unsupported_modes_are_not_tools(auth_client, mode):
    headers, _, url, _ = prepare(auth_client)
    assert submit(auth_client, headers, url, mode=mode).status_code == 422


def test_source_deleted_before_finalization_fails_with_usage(auth_client):
    headers, _, url, previous = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    provider = ProposalProvider(previous.path)
    original = provider.decide

    async def decide(payload):
        result = await original(payload)
        if provider.calls == 2:
            async with auth_client.app.state.test_factory() as db:
                await db.execute(delete(RepositoryIndex))
                await db.commit()
        return result

    provider.decide = decide
    worker(auth_client, run["id"], provider)
    detail = auth_client.get("/agent-runs/" + run["id"]).json()
    assert detail["run"]["status"] == "failed"
    assert detail["run"]["error_code"] == "agent_source_missing"
    assert detail["run"]["result"] is None
    assert detail["calls"][1]["input_tokens"] == 100


def test_proposal_completion_event_failure_rolls_back_publication(auth_client, monkeypatch):
    from app.jobs import agent_run

    headers, _, url, previous = prepare(auth_client)
    run = submit(auth_client, headers, url).json()
    original = agent_run.event

    async def event(db, run_id, kind, *args, **kwargs):
        if kind == "completed":
            raise RuntimeError("simulated storage failure")
        return await original(db, run_id, kind, *args, **kwargs)

    monkeypatch.setattr(agent_run, "event", event)
    worker(auth_client, run["id"], ProposalProvider(previous.path))
    detail = auth_client.get("/agent-runs/" + run["id"]).json()
    assert detail["run"]["status"] == "failed" and detail["run"]["result"] is None
    assert detail["calls"][1]["input_tokens"] == 100
    assert not any(e["kind"] == "completed" for e in detail["events"])
