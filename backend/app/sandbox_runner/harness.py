"""Trusted entrypoint, run ONLY inside a constrained sandbox container; stdlib only."""

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

MAX_EXPANDED = 40 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unpack(blob: bytes, destination: Path) -> None:
    # Bound decompression before opening tar; reject unsupported entries rather than omit them.
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
        raw = stream.read(MAX_EXPANDED + 1)
    if len(raw) > MAX_EXPANDED:
        raise ValueError("Archive exceeds 40 MiB expanded limit")
    seen, root = set(), None
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for number, member in enumerate(archive):
            if number >= 5000:
                raise ValueError("Archive entry limit")
            parts = member.name.rstrip("/").split("/")
            if (
                any(p in {"", ".", ".."} or p.lower() == ".git" for p in parts)
                or "\\" in member.name
                or len(member.name) > 600
            ):
                raise ValueError("Unsafe archive path")
            root = root or parts[0]
            if parts[0] != root:
                raise ValueError("Inconsistent archive root")
            if len(parts) == 1 and member.isdir():
                continue
            if len(parts) < 2:
                raise ValueError("Invalid root entry")
            name = "/".join(parts[1:])
            if name in seen or not (member.isdir() or member.isfile()) or member.issparse():
                raise ValueError("Duplicate path or unsupported link/special file")
            seen.add(name)
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.size > 10 * 1024 * 1024:
                raise ValueError("Archive file exceeds 10 MiB")
            target.parent.mkdir(parents=True, exist_ok=True)
            file_stream = archive.extractfile(member)
            if file_stream is None:
                raise ValueError("Missing file bytes")
            target.write_bytes(file_stream.read(member.size + 1))
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    if (destination / ".gitmodules").exists():
        raise ValueError("Submodule repositories require a complete checkout profile")


def manifest(root: Path) -> dict[str, tuple[str, int]]:
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Unsupported file type")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = (
                digest(path.read_bytes()),
                path.stat().st_mode & 0o777,
            )
    return result


def prepare(root: Path, proposal: dict[str, Any], patched: bool) -> None:
    before = manifest(root)
    expected = dict(before)
    if digest(proposal["diff"].encode()) != proposal["diff_sha256"]:
        raise ValueError("Patch fingerprint mismatch")
    seen = set()
    for item in proposal["files"]:
        path = item["path"]
        if path in seen:
            raise ValueError("Duplicate patch path")
        seen.add(path)
        actual = before.get(path)
        if (actual[0] if actual else None) != item["before_sha256"]:
            raise ValueError("Complete archive differs from proposal base")
        if item["operation"] == "delete":
            expected.pop(path)
        else:
            expected[path] = (item["after_sha256"], actual[1] if actual else 0o644)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.autocrlf=false",
        "apply",
        "--whitespace=nowarn",
    ]
    # Check and apply even for preparation; baseline receives a fresh, separate container.
    subprocess.run(
        command + ["--check", "/input/change.patch"],
        cwd=root,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    if patched:
        subprocess.run(
            command + ["/input/change.patch"],
            cwd=root,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if manifest(root) != expected:
            raise ValueError("Patch changed unexpected bytes, paths, or modes")


def detect(root: Path, requested: str) -> tuple[str, list[str]]:
    python = (
        any(
            (root / p).is_file()
            for p in ("pyproject.toml", "pytest.ini", "requirements.txt", "setup.py")
        )
        or any(root.glob("test_*.py"))
        or any((root / "tests").rglob("test_*.py"))
    )
    node = (root / "package.json").is_file()
    if requested == "auto":
        if python == node:
            raise ValueError("Ambiguous or unsupported project; select Python or JavaScript")
        requested = "python" if python else "javascript"
    if requested == "python":
        if not python:
            raise ValueError("No Python project markers at archive root")
        return requested, ["/usr/bin/python3", "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if requested == "javascript" and node:
        config = json.loads((root / "package.json").read_text())
        if not isinstance(config.get("scripts", {}).get("test"), str):
            raise ValueError("package.json has no test script")
        return requested, ["/usr/local/bin/npm", "test", "--ignore-scripts"]
    raise ValueError("Unsupported test profile")


def main() -> None:
    root = Path("/work")
    request = json.loads(Path("/input/request.json").read_text())
    try:
        unpack(Path("/input/source.tar.gz").read_bytes(), root)
        profile, command = detect(root, request["profile"])
        phase = sys.argv[1]
        prepare(root, request["proposal"], patched=phase != "baseline")
        if phase == "prepare":
            print(json.dumps({"profile": profile, "command": command}))
            return
    except Exception:
        print(
            "Sandbox preparation failed: unsupported archive, source mismatch, patch or profile.",
            flush=True,
        )
        raise SystemExit(201) from None
    os.chdir(root)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "CI": "true",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "npm_config_cache": "/tmp/npm",
        "npm_config_offline": "true",
    }
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
