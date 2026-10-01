"""Explicit opt-in only: executes fixed test code on a dedicated rootless Docker daemon."""

import asyncio
import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.agents.proposals import ProposalDraft, render_proposal
from app.sandbox_runner import docker
from app.schemas.search import Evidence


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_SANDBOX_TESTS") != "1",
    reason="requires dedicated rootless Docker and trusted sandbox image",
)
@pytest.mark.parametrize("profile", ["python", "javascript"])
def test_real_isolated_baseline_and_patch(tmp_path, profile):
    old, new = "VALUE = 0\n", "VALUE = 1\n"
    path = "value.py"
    files = {
        path: old,
        "pytest.ini": "[pytest]\npythonpath = .\n",
        "test_value.py": """import os, socket
from pathlib import Path
from value import VALUE

def test_isolation_and_value():
    assert os.getuid() == 65532
    assert not any(key.startswith('APP_') or key == 'SANDBOX_TOKEN' for key in os.environ)
    assert Path('/sys/fs/cgroup/memory.max').read_text().strip() == '1073741824'
    assert Path('/sys/fs/cgroup/pids.max').read_text().strip() == '64'
    assert not Path('/var/run/docker.sock').exists()
    try:
        Path('/outside').write_text('bad')
        assert False, 'root must be read-only'
    except OSError:
        pass
    try:
        socket.create_connection(('1.1.1.1', 443), timeout=1)
        assert False, 'network must be disabled'
    except OSError:
        pass
    assert VALUE == 1
""",
    }
    if profile == "javascript":
        path, old, new = "value.js", "module.exports = 0;\n", "module.exports = 1;\n"
        files = {
            path: old,
            "package.json": '{"scripts":{"test":"node --test test.js"}}',
            "test.js": (
                "const test = require('node:test'); const assert = require('node:assert');\n"
                "test('value', () => {assert.equal(process.getuid(), 65532);\n"
                "assert.equal(require('./value'),1);});\n"
            ),
        }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, content in files.items():
            item = tarfile.TarInfo("repo/" + name)
            data = content.encode()
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    evidence = Evidence(
        citation_id="C1",
        chunk_id=uuid4(),
        path=path,
        commit_sha="a" * 40,
        start_line=1,
        end_line=1,
        content=old,
    )
    draft = ProposalDraft(
        implementation_plan=["Fix value"],
        risks=["Test fixture"],
        test_plan=["Check value"],
        edits=[
            dict(
                path=path, operation="replace", start_line=1, end_line=1, old_text=old, new_text=new
            )
        ],
    )
    proposal = render_proposal(draft, files, [evidence], "a" * 40)
    folder = tmp_path / "input"
    folder.mkdir()
    folder.chmod(0o755)
    (folder / "source.tar.gz").write_bytes(output.getvalue())
    (folder / "request.json").write_text(
        json.dumps({"profile": profile, "proposal": proposal.model_dump()})
    )
    (folder / "change.patch").write_text(proposal.diff)
    shutil.copyfile(Path(docker.__file__).with_name("harness.py"), folder / "harness.py")
    for item in folder.iterdir():
        item.chmod(0o444)

    async def scenario():
        image = os.environ["SANDBOX_IMAGE_ID"]
        await docker.preflight(image)
        prefix = "rp-fixture-" + str(uuid4())
        prep = await docker.run(folder, image, prefix + "-prepare", "prepare")
        assert prep.status == "passed", prep.log
        baseline = await docker.run(folder, image, prefix + "-baseline", "baseline")
        patched = await docker.run(folder, image, prefix + "-patched", "patched")
        assert baseline.status == "failed", baseline.log
        assert patched.status == "passed", patched.log
        assert (await docker.cli("ps", "-aq", "--filter", "name=" + prefix)).strip() == ""

    asyncio.run(scenario())
