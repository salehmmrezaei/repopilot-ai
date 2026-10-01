"""Run on a dedicated rootless-Docker host, ONE uvicorn process; no application credentials."""

import asyncio
import base64
import fcntl
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint

from app.execution.contracts import SandboxRequest, SandboxResult
from app.sandbox_runner import docker


def create_app() -> FastAPI:
    token = os.environ["SANDBOX_TOKEN"]
    image = os.environ["SANDBOX_IMAGE_ID"]
    state_dir = Path(os.environ["SANDBOX_STATE_DIR"]).resolve()
    if len(token) < 32:
        raise RuntimeError("SANDBOX_TOKEN must contain at least 32 characters")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = state_dir / "runs.sqlite3"
    tasks: dict[str, asyncio.Task[None]] = {}

    @contextmanager
    def db() -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(store)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS runs "
            "(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
            "result TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(run_id: UUID | str) -> SandboxResult | None:
        with db() as conn:
            row = conn.execute("SELECT result FROM runs WHERE id=?", (str(run_id),)).fetchone()
        return SandboxResult.model_validate_json(row[0]) if row else None

    def save(run_id: UUID | str, result: SandboxResult) -> None:
        with db() as conn:
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, result=excluded.result",
                (str(run_id), result.request_hash, result.model_dump_json(), time.time()),
            )

    def write_input(folder: Path, archive: bytes, request: SandboxRequest) -> None:
        folder.chmod(0o755)
        (folder / "source.tar.gz").write_bytes(archive)
        (folder / "request.json").write_text(
            json.dumps({"profile": request.profile, "proposal": request.proposal.model_dump()})
        )
        (folder / "change.patch").write_text(request.proposal.diff)
        shutil.copyfile(Path(__file__).with_name("harness.py"), folder / "harness.py")
        for path in folder.iterdir():
            path.chmod(0o444)

    async def execute(run_id: UUID, request: SandboxRequest, result: SandboxResult) -> None:
        try:
            async with asyncio.timeout(225):
                archive = base64.b64decode(request.archive_b64, validate=True)
                if len(archive) > 10 * 1024 * 1024:
                    raise ValueError("Archive too large")
                result.archive_sha256 = sha256(archive).hexdigest()
                with tempfile.TemporaryDirectory(prefix="input-", dir=state_dir) as temp:
                    folder = Path(temp)
                    write_task = asyncio.create_task(
                        asyncio.to_thread(write_input, folder, archive, request)
                    )
                    try:
                        await asyncio.shield(write_task)
                    except asyncio.CancelledError:
                        await write_task
                        raise
                    result.preparation = await docker.run(
                        folder, image, f"rp-{run_id}-prepare", "prepare"
                    )
                    if result.preparation.status != "passed":
                        result.status, result.error = (
                            "failed",
                            "Archive, profile or patch validation failed.",
                        )
                    else:
                        info = json.loads(result.preparation.log)
                        result.profile, result.command = info["profile"], info["command"]
                        save(run_id, result)
                        result.baseline = await docker.run(
                            folder, image, f"rp-{run_id}-baseline", "baseline"
                        )
                        save(run_id, result)
                        result.patched = await docker.run(
                            folder, image, f"rp-{run_id}-patched", "patched"
                        )
                        result.status = "completed"
        except asyncio.CancelledError:
            result.status, result.error = "cancelled", "Cancelled; containers are being removed."
        except Exception:
            result.status, result.error = (
                "failed",
                "Sandbox failed or exceeded its deadline. No automatic retry.",
            )
        finally:
            # Cancellation is terminal, even when requested after a phase finished.
            previous = get(run_id)
            if previous and previous.status == "cancelled":
                result.status = "cancelled"
            save(run_id, result)
            tasks.pop(str(run_id), None)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        lock = (state_dir / "controller.lock").open("a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        await docker.preflight(image)
        await docker.reap(force=True)
        for path in state_dir.glob("input-*"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        # Abandoned runs never restart execution after a controller crash.
        with db() as conn:
            rows = conn.execute("SELECT id, result FROM runs").fetchall()
        for run_id, raw in rows:
            result = SandboxResult.model_validate_json(raw)
            if result.status == "running":
                result.status, result.error = (
                    "failed",
                    "Controller restarted. No automatic execution retry.",
                )
                save(run_id, result)

        async def janitor() -> None:
            while True:
                try:
                    await docker.reap()
                    with db() as conn:
                        conn.execute(
                            "DELETE FROM runs WHERE created_at < ? AND "
                            "json_extract(result, '$.status') != 'running'",
                            (time.time() - 7 * 86400,),
                        )
                except Exception:
                    pass  # Supervise Docker health externally; later requests still fail closed.
                await asyncio.sleep(5)

        cleaner = asyncio.create_task(janitor())
        try:
            yield
        finally:
            cleaner.cancel()
            await asyncio.gather(cleaner, return_exceptions=True)
            pending = list(tasks.values())
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            lock.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next: RequestResponseEndpoint) -> Response:
        from fastapi.responses import JSONResponse

        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/runs/{run_id}")
    async def status(run_id: UUID) -> SandboxResult:
        result = get(run_id)
        if result is None:
            raise HTTPException(404)
        return result

    @app.post("/runs/{run_id}")
    async def submit(run_id: UUID, request: Request) -> SandboxResult:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 15 * 1024 * 1024:
                raise HTTPException(413)
        try:
            value = SandboxRequest.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "Invalid bounded sandbox input") from None
        fingerprint = sha256(value.model_dump_json().encode()).hexdigest()
        previous = get(run_id)
        if previous:
            if previous.status != "cancelled" and previous.request_hash != fingerprint:
                raise HTTPException(409, "Request ID conflicts with prior input")
            return previous
        if tasks:
            raise HTTPException(429, "Runner busy; no run started")
        with db() as conn:
            if conn.execute("SELECT count(*) FROM runs").fetchone()[0] >= 1000:
                raise HTTPException(503, "Runner retention capacity reached")
        result = SandboxResult(status="running", request_hash=fingerprint, image_id=image)
        save(run_id, result)  # Persist receipt before starting a container.
        tasks[str(run_id)] = asyncio.create_task(execute(run_id, value, result))
        tasks[str(run_id)].add_done_callback(lambda _: tasks.pop(str(run_id), None))
        return result

    @app.delete("/runs/{run_id}")
    async def cancel(run_id: UUID) -> SandboxResult:
        result = get(run_id) or SandboxResult(status="cancelled", request_hash="", image_id=image)
        if result.status == "running":
            result.status = "cancelled"
        save(run_id, result)  # A tombstone prevents a delayed POST from launching code.
        task = tasks.get(str(run_id))
        if task:
            task.cancel()
        return result

    return app
