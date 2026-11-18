# Sandbox setup

Test execution is optional and disabled by default. The API and workers send
approved jobs to an authenticated controller on a **separate dedicated Linux host**.
Do not mount a Docker socket into application services or colocate the controller
with application credentials or databases.

## Install the controller

Use a dedicated user with rootless Docker, cgroup v2, and systemd delegation.
Copy the project to `~/repopilot-ai` on that host, then:

```bash
cd ~/repopilot-ai/backend
uv sync --frozen
docker build -f app/sandbox_runner/Dockerfile -t repopilot-sandbox:local .
docker image inspect --format '{{.Id}}' repopilot-sandbox:local
docker info
```

Record the immutable `sha256:...` image ID. Create
`~/.config/repopilot-sandbox.env` with mode 0600:

```dotenv
SANDBOX_TOKEN=<random-token-of-at-least-32-characters>
SANDBOX_IMAGE_ID=sha256:<actual-local-image-id>
SANDBOX_STATE_DIR=/home/<runner-user>/.local/state/repopilot-sandbox
```

Install [the user service](../deploy/repopilot-sandbox.service) as
`~/.config/systemd/user/repopilot-sandbox.service`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now repopilot-sandbox.service
systemctl --user status repopilot-sandbox.service
```

The controller binds to `127.0.0.1:8090`. Put an HTTPS proxy in front of it using
[the nginx example](../deploy/sandbox-nginx.conf.example), with valid certificates
and ingress restricted to application workers. Keep one controller process.
The controller requires rootless Docker with default seccomp, cgroup v2, and systemd.

## Verify the runner

On a dedicated test daemon, export the accepted image ID and run from `backend/`:

```bash
export SANDBOX_IMAGE_ID='sha256:replace-with-the-actual-local-image-id'
RUN_SANDBOX_TESTS=1 uv run pytest -q tests/integration/test_sandbox_docker.py tests/integration/test_coding_sandbox.py
```

Replace the image placeholder before running. Verify baseline/reference behavior,
runtime restrictions, and container cleanup. Exercise timeout, excessive output,
cancellation, controller restart, and runner unavailability before enabling it.
The [coding evaluation](evaluation.md#coding-and-sandbox-evaluation) checks reference
patches separately from live model output.

## Connect the application

After runner acceptance, set these in the application's `.env`:

```dotenv
APP_EXECUTIONS_ENABLED=true
APP_SANDBOX_URL=https://sandbox.example.com
APP_SANDBOX_TOKEN=<same-private-runner-token>
```

Recreate backend, worker, and dispatcher. In a completed proposal, review the patch,
confirm execution, and choose **Run isolated validation**. Check preparation,
baseline, and patched receipts; reload to verify the same execution is recovered.
Automatic repair additionally needs agents and `APP_REPAIRS_ENABLED=true`, plus
explicit authorization in the UI.

## Runtime limits and interpretation

| Boundary | Behavior |
| --- | --- |
| Identity | Non-root UID/GID 65532, all capabilities dropped, no-new-privileges |
| Network and storage | No network; read-only root; fresh bounded tmpfs per phase; no application credentials |
| Resources | 1 CPU, 1 GiB memory/swap total, 64 PIDs |
| Time | 20 seconds preparation, 60 seconds per test phase, 225-second controller deadline |
| Output | First 32 KiB retained per phase; output above 512 KiB terminates the command |
| Input | Pinned archive, at most 10 MiB compressed/40 MiB expanded and 5,000 entries |
| Recovery | Expiry labels, orphan reaping, startup cleanup, idempotent receipts |

The runner accepts complete archives and rejects traversal, links, special entries,
and submodules. Baseline and patched phases use separate workspaces with verified
file hashes. Python and JavaScript root-project profiles use preinstalled trusted
dependencies; no network installs, repository Dockerfiles, or model-supplied commands.
Unknown or mixed roots need an explicit supported profile. Monorepo subdirectories
and Go are outside the current scope.

A zero exit status means the command passed; inspect coverage and behavior before
accepting a patch. Missing dependencies, timeouts, resource failures, and unknown
outcomes remain explicit. Cancellation may take time to reach remote work.

Receipts are limited to 1,000 records and retained for up to seven days.
Application deletion does not immediately erase runner receipts. Monitor disk,
certificates, daemon health, and cleanup. Containers share a kernel; stronger
isolation is needed for unrestricted hostile multi-tenant execution.
