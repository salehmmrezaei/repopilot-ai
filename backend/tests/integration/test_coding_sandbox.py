"""Verify reference solutions only on the explicitly enabled dedicated rootless daemon."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from app.evaluation.coding import DEFAULT, archive, load, reference
from app.sandbox_runner import docker


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_SANDBOX_TESTS") != "1",
    reason="requires dedicated rootless Docker and trusted sandbox image",
)
@pytest.mark.parametrize("case", load(DEFAULT)[1], ids=lambda case: case.id)
def test_coding_reference_baseline_fails_patch_passes(tmp_path, case):
    folder = tmp_path / "input"
    folder.mkdir(mode=0o755)
    proposal = asyncio.run(reference(case))
    (folder / "source.tar.gz").write_bytes(archive(case))
    (folder / "request.json").write_text(
        json.dumps({"profile": case.profile, "proposal": proposal.model_dump()})
    )
    (folder / "change.patch").write_text(proposal.diff)
    shutil.copyfile(Path(docker.__file__).with_name("harness.py"), folder / "harness.py")
    for item in folder.iterdir():
        item.chmod(0o444)

    async def scenario():
        image = os.environ["SANDBOX_IMAGE_ID"]
        await docker.preflight(image)
        prefix = "rp-coding-" + str(uuid4())
        prep = await docker.run(folder, image, prefix + "-prepare", "prepare")
        assert prep.status == "passed", prep.log
        baseline = await docker.run(folder, image, prefix + "-baseline", "baseline")
        patched = await docker.run(folder, image, prefix + "-patched", "patched")
        assert baseline.status == "failed", baseline.log
        assert patched.status == "passed", patched.log
        assert (await docker.cli("ps", "-aq", "--filter", "name=" + prefix)).strip() == ""

    asyncio.run(scenario())
