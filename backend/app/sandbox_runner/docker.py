"""Trusted controller: fixed Docker argv, never repository shell commands on the host."""

import asyncio
import json
import re
import time
from pathlib import Path

from app.execution.contracts import CommandResult


async def cli(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 15)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0 or len(stdout) > 1024 * 1024:
        raise RuntimeError("Docker control operation failed")
    return stdout.decode()


def command(name: str, directory: Path, image: str, phase: str) -> list[str]:
    if phase not in {"prepare", "baseline", "patched"} or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image
    ):
        raise ValueError("Invalid sandbox configuration")
    return [
        "create",
        "--name",
        name,
        "--label",
        "repopilot.sandbox=1",
        "--label",
        f"repopilot.expires={int(time.time()) + 90}",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1",
        "--memory=1g",
        "--memory-swap=1g",
        "--pids-limit=64",
        "--ulimit",
        "nofile=128:128",
        "--ulimit",
        "fsize=10485760:10485760",
        "--init",
        "--stop-timeout=1",
        "--log-driver=none",
        "--tmpfs",
        "/work:rw,nosuid,nodev,size=128m,uid=65532,gid=65532,mode=0700",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m,uid=65532,gid=65532,mode=1777",
        "--mount",
        f"type=bind,src={directory},dst=/input,readonly",
        "--workdir=/work",
        "--entrypoint=/usr/bin/python3",
        image,
        "-I",
        "/input/harness.py",
        phase,
    ]


async def preflight(image: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RuntimeError("Configure a locally installed immutable image ID")
    info = json.loads(await cli("info", "--format", "{{json .}}"))
    security = " ".join(info.get("SecurityOptions", []))
    if (
        "rootless" not in security
        or "seccomp" not in security
        or info.get("CgroupVersion") != "2"
        or info.get("CgroupDriver") != "systemd"
    ):
        raise RuntimeError("Require rootless Docker, default seccomp, cgroup v2 and systemd")
    image_info = json.loads(await cli("image", "inspect", image))[0]
    if image_info["Id"] != image or image_info["Config"].get("Volumes"):
        raise RuntimeError("Image must match its ID and declare no volumes")


async def reap(force: bool = False) -> None:
    ids = (await cli("ps", "-aq", "--filter", "label=repopilot.sandbox=1")).split()
    for container in ids:
        data = json.loads(await cli("inspect", container))[0]
        expires = int(data["Config"]["Labels"].get("repopilot.expires", "0"))
        if force or expires < time.time():
            await cli("rm", "-f", container)


async def run(directory: Path, image: str, name: str, phase: str) -> CommandResult:
    started = time.monotonic()
    output, size, status, code = bytearray(), 0, "error", None
    process = None
    try:
        await cli(*command(name, directory, image, phase))
        # Inspect enforcement, not merely requested flags, before repository code can run.
        config = json.loads(await cli("inspect", name))[0]["HostConfig"]
        if (
            config["Memory"] != 1073741824
            or config["NanoCpus"] != 1000000000
            or config["PidsLimit"] != 64
            or config["NetworkMode"] != "none"
            or not config["ReadonlyRootfs"]
        ):
            raise RuntimeError("Container resource restrictions were not applied")
        process = await asyncio.create_subprocess_exec(
            "docker",
            "start",
            "-a",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        async with asyncio.timeout(20 if phase == "prepare" else 60):
            while chunk := await process.stdout.read(4096):
                size += len(chunk)
                output.extend(chunk[: max(0, 32768 - len(output))])
                if size > 512 * 1024:
                    status = "output_limit"
                    break
            else:
                await process.wait()
                state = json.loads(await cli("inspect", "--format", "{{json .State}}", name))
                code = state["ExitCode"]
                status = (
                    "error"
                    if state.get("Status") != "exited" or (process.returncode != 0 and code == 0)
                    else "resource_limit"
                    if state["OOMKilled"]
                    else ("passed" if code == 0 else "failed")
                )
    except TimeoutError:
        status = "timeout"
    finally:
        try:
            await asyncio.shield(cli("rm", "-f", name))
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
    decoded = output.decode("utf-8", errors="replace")
    clean = "".join(c for c in decoded if c in "\n\t" or (c.isprintable() and c != "\x1b"))
    return CommandResult(
        status=status,
        exit_code=code,
        log=clean,
        truncated=size > 32768,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
