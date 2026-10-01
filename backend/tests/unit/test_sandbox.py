import asyncio
import base64
import io
import json
import tarfile
import threading
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.proposals import ProposalDraft, render_proposal
from app.execution.contracts import CommandResult
from app.sandbox_runner import docker, harness, server
from app.schemas.search import Evidence


def archive(files=None, special=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for path, content in (
            files or {"main.py": "print('old')\n", "pytest.ini": "[pytest]\n"}
        ).items():
            info = tarfile.TarInfo("repo/" + path)
            encoded = content.encode()
            info.size = len(encoded)
            tar.addfile(info, io.BytesIO(encoded))
        if special:
            tar.addfile(special)
    return output.getvalue()


def proposal():
    draft = ProposalDraft(
        implementation_plan=["Change greeting"],
        risks=["Changes output"],
        test_plan=["pytest"],
        edits=[
            dict(
                path="main.py",
                operation="replace",
                start_line=1,
                end_line=1,
                old_text="print('old')\n",
                new_text="print('new')\n",
            )
        ],
    )
    seen = Evidence(
        citation_id="C1",
        chunk_id=uuid4(),
        path="main.py",
        commit_sha="a" * 40,
        start_line=1,
        end_line=1,
        content="print('old')\n",
    )
    return render_proposal(draft, {"main.py": seen.content}, [seen], "a" * 40)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("repo/../escape", tarfile.REGTYPE),
        ("repo/.git/config", tarfile.REGTYPE),
        ("repo/link", tarfile.SYMTYPE),
        ("repo/hard", tarfile.LNKTYPE),
        ("repo/dev", tarfile.CHRTYPE),
    ],
)
def test_full_archive_rejects_unsafe_entries(tmp_path, name, kind):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = "/etc/passwd"
    with pytest.raises(ValueError):
        harness.unpack(archive(special=info), tmp_path)
    assert not (tmp_path.parent / "escape").exists()


def test_complete_archive_keeps_binary_and_rejects_submodules(tmp_path):
    harness.unpack(
        archive({"asset.bin": "\x00binary", "package.json": '{"scripts":{"test":"node --test"}}'}),
        tmp_path,
    )
    assert (tmp_path / "asset.bin").read_bytes() == b"\x00binary"
    assert harness.detect(tmp_path, "auto")[0] == "javascript"
    with pytest.raises(ValueError):
        harness.unpack(archive({".gitmodules": "submodule"}), tmp_path)


def test_profile_detection_is_bounded_and_requires_choice_for_mixed_roots(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]")
    assert harness.detect(tmp_path, "auto")[0] == "python"
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node --test"}}')
    with pytest.raises(ValueError):
        harness.detect(tmp_path, "auto")
    assert harness.detect(tmp_path, "javascript")[1] == [
        "/usr/local/bin/npm",
        "test",
        "--ignore-scripts",
    ]


def test_source_mismatch_never_invokes_git(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("stale")

    def forbidden(*args, **kwargs):
        raise AssertionError("must fail before application")

    monkeypatch.setattr(harness.subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        harness.prepare(tmp_path, proposal().model_dump(), True)


def test_docker_policy_and_immutable_image(tmp_path):
    args = docker.command("run", tmp_path, "sha256:" + "a" * 64, "patched")
    for flag in [
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1",
        "--memory=1g",
        "--memory-swap=1g",
        "--pids-limit=64",
        "--pull=never",
        "--log-driver=none",
    ]:
        assert flag in args
    assert not any("docker.sock" in arg or arg == "--privileged" for arg in args)
    assert args[-3:] == ["-I", "/input/harness.py", "patched"]
    with pytest.raises(ValueError):
        docker.command("run", tmp_path, "latest", "patched")
    with pytest.raises(ValueError):
        docker.command("run", tmp_path, "sha256:" + "a" * 64, "sh")


def test_runner_idempotency_auth_and_cancel_tombstone(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "secret" * 8)
    monkeypatch.setenv("SANDBOX_IMAGE_ID", "sha256:" + "a" * 64)
    monkeypatch.setenv("SANDBOX_STATE_DIR", str(tmp_path))

    async def no_op(*args, **kwargs):
        pass

    monkeypatch.setattr(docker, "preflight", no_op)
    monkeypatch.setattr(docker, "reap", no_op)
    calls = []

    async def run(directory, image, name, phase):
        calls.append(phase)
        return CommandResult(
            status="passed",
            exit_code=0,
            log=json.dumps({"profile": "python", "command": ["pytest"]})
            if phase == "prepare"
            else "all done",
            truncated=False,
            duration_ms=1,
        )

    monkeypatch.setattr(docker, "run", run)
    body = dict(
        commit_sha="a" * 40,
        archive_b64=base64.b64encode(archive()).decode(),
        proposal=proposal().model_dump(),
        profile="python",
    )
    with TestClient(server.create_app()) as client:
        key = str(uuid4())
        url = "/runs/" + key
        assert client.post(url, json=body).status_code == 401
        headers = {"Authorization": "Bearer " + "secret" * 8}
        first = client.post(url, json=body, headers=headers)
        assert first.status_code == 200
        for _ in range(20):
            result = client.get(url, headers=headers).json()
            if result["status"] != "running":
                break
        assert result["status"] == "completed"
        assert result["archive_sha256"] == sha256(base64.b64decode(body["archive_b64"])).hexdigest()
        assert calls == ["prepare", "baseline", "patched"]
        assert client.post(url, json=body, headers=headers).status_code == 200
        assert len(calls) == 3
        assert (
            client.post(url, json={**body, "profile": "javascript"}, headers=headers).status_code
            == 409
        )
        tombstone = "/runs/" + str(uuid4())
        assert client.delete(tombstone, headers=headers).json()["status"] == "cancelled"
        assert client.post(tombstone, json=body, headers=headers).json()["status"] == "cancelled"
        assert len(calls) == 3


def test_rootless_cgroup_preflight_fails_closed(monkeypatch):
    async def cli(*args):
        return json.dumps(
            {"SecurityOptions": ["name=seccomp"], "CgroupVersion": "1", "CgroupDriver": "none"}
        )

    monkeypatch.setattr(docker, "cli", cli)
    with pytest.raises(RuntimeError):
        asyncio.run(docker.preflight("sha256:" + "a" * 64))


@pytest.mark.parametrize("failure", ["output_limit", "oom", "success", "restriction"])
def test_container_output_bounds_resources_and_cleanup(tmp_path, monkeypatch, failure):
    calls = []

    async def cli(*args):
        calls.append(args)
        if args[0] == "inspect" and "--format" not in args:
            return json.dumps(
                [
                    {
                        "HostConfig": {
                            "Memory": 0 if failure == "restriction" else 1073741824,
                            "NanoCpus": 1000000000,
                            "PidsLimit": 64,
                            "NetworkMode": "none",
                            "ReadonlyRootfs": True,
                        }
                    }
                ]
            )
        if args[0] == "inspect":
            return json.dumps(
                {
                    "Status": "exited",
                    "ExitCode": 137 if failure == "oom" else 0,
                    "OOMKilled": failure == "oom",
                }
            )
        return ""

    class Process:
        def __init__(self):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b"x" * (600000 if failure == "output_limit" else 3))
            self.stdout.feed_eof()

        async def wait(self):
            self.returncode = self.returncode or 0
            return self.returncode

        def kill(self):
            self.returncode = 137

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(docker, "cli", cli)
    monkeypatch.setattr(docker.asyncio, "create_subprocess_exec", spawn)

    async def scenario():
        return await docker.run(tmp_path, "sha256:" + "a" * 64, "fixture", "patched")

    if failure == "restriction":
        with pytest.raises(RuntimeError):
            asyncio.run(scenario())
    else:
        result = asyncio.run(scenario())
        assert (
            result.status
            == {"output_limit": "output_limit", "oom": "resource_limit", "success": "passed"}[
                failure
            ]
        )
        assert len(result.log) <= 32768
    assert calls[-1] == ("rm", "-f", "fixture")


def test_runner_cancellation_stops_current_phase_and_never_starts_next(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "x" * 40)
    monkeypatch.setenv("SANDBOX_IMAGE_ID", "sha256:" + "a" * 64)
    monkeypatch.setenv("SANDBOX_STATE_DIR", str(tmp_path))

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(docker, "preflight", noop)
    monkeypatch.setattr(docker, "reap", noop)
    calls = []

    entered = threading.Event()

    async def block(*args):
        calls.append(args[-1])
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("cleaned")

    monkeypatch.setattr(docker, "run", block)
    headers = {"Authorization": "Bearer " + "x" * 40}
    url = "/runs/" + str(uuid4())
    with TestClient(server.create_app()) as client:
        assert (
            client.post(
                url,
                headers=headers,
                json=dict(
                    commit_sha="a" * 40,
                    archive_b64=base64.b64encode(archive()).decode(),
                    proposal=proposal().model_dump(),
                ),
            ).status_code
            == 200
        )
        assert entered.wait(timeout=2)
        assert client.delete(url, headers=headers).json()["status"] == "cancelled"
        for _ in range(10):
            client.get(url, headers=headers)
        assert "baseline" not in calls and "patched" not in calls
    assert calls == ["prepare", "cleaned"]
