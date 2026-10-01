# Milestone 10 — isolated patch application and test feedback

## Outcome

A completed patch proposal now offers **Run isolated validation** after a review checkbox.
The application downloads the proposal's exact GitHub commit archive, sends a bounded
request to a dedicated execution controller, and saves preparation/baseline/patched
receipts. The runner validates all original/proposed file hashes against the complete
accepted archive, checks and applies the diff only in disposable containers, and runs
Python or JavaScript checks. The repository and original checkout remain unchanged.

Run progress/results survive reloads. Runs are idempotent by proposal/request key, have
owner/CSRF checks, cancellation, quotas and deadlines. Known test output is bounded and
escaped in React. A terminal run can populate a new proposal request with frozen test
feedback; the user submits that new AI request and separately authorizes any subsequent
execution. Revision chains are capped at two. No automatic model repair or command retry.

This release implements the workflow. Real Docker isolation, PostgreSQL locking and
browser acceptance must still be verified on your host; see validation.md. Execution
stays disabled by default. No repository code or real sandbox containers were run here.

## Architecture and security boundary

The API/ordinary Celery worker never receives a Docker socket and never executes repository
code. It uses an authenticated HTTPS control API on a **separate dedicated Linux host**.
Do not run that controller beside production application databases, credentials or other
users' workloads. Containers share a kernel; this is bounded container isolation, not a
claim of VM-grade isolation or proof against every kernel/runtime exploit. For hostile
public multi-tenant service, add a stronger runtime/VM boundary and operational review.

The controller owns only its rootless Docker daemon, runner token, immutable trusted image
ID and local receipt store. A single process is enforced with an exclusive file lock.
Only one execution is active on each controller. Capacity/busy errors fail visibly.
No image name, host path, resource override or arbitrary host command comes from the user
or model. Operator-prebuilt images are selected by immutable local sha256 ID, never pulled
at run time. Docker must report rootless mode, default seccomp, cgroup v2 and systemd.

| Boundary | Enforced behavior |
| --- | --- |
| Runtime | Non-root UID/GID 65532, cap-drop ALL, no-new-privileges, default seccomp |
| Network | none; no published ports, host namespace, Docker socket or application environment |
| Resources | 1 CPU, 1 GiB memory/swap total, 64 PIDs, 128 open files, 10 MiB per-file limit |
| Filesystem | Read-only base; /work 128 MiB tmpfs; /tmp 64 MiB tmpfs; read-only per-run input mount |
| Time | 20 seconds preparation, 60 seconds each baseline/patched command, 225-second controller deadline |
| Recovery | Container expiry labels + five-second orphan reaper; startup removes abandoned owned containers |
| Output | First 32 KiB retained per phase; more than 512 KiB output terminates the command; Docker disk logging disabled |
| Input | 10 MiB compressed archive, 40 MiB expanded, at most 5,000 entries, 10 MiB per archive file |

The controller inspects actual Docker memory/CPU/PID/network/read-only settings before
starting code. Every phase gets a new container/workspace. Baseline test writes cannot
influence patched tests. Image defaults must not contain secrets or declared volumes.

Full-archive validation rejects traversal, duplicate paths, symlinks, hardlinks, sparse
files, device entries and submodule repositories; it does not silently omit them. Ordinary
binary files are preserved. Git LFS content is not hydrated. A repository that needs
unsupported archive features must fail or use a future complete-checkout profile.

Preparation verifies patch SHA-256, every before hash and create-path absence, applies with
Git's normal safe-path checks, then compares the entire resulting file/hash/mode manifest
to the proposal. Extra file changes or mode changes fail. This closes Milestone 9's omitted
file collision gap for accepted archives. No application source or test code is imported
by the controller itself: extraction, Git and tests run inside containers.

## Profiles and result semantics

Root detection supports Python project markers/Python tests and package.json with a test
script. Mixed/unknown roots require an explicit profile or fail preparation. Monorepo
subdirectory selection and Go are deferred.

- Python: `/usr/bin/python3 -I -m pytest -q -p no:cacheprovider`.
- JavaScript: `/usr/local/bin/npm test --ignore-scripts` (the test script itself executes;
  npm pre/post lifecycle hooks are disabled).

The trusted image includes Python/pytest, Node/npm and Git. Network access and dependency
installation are disabled during execution. For additional dependencies the operator builds
and reviews a trusted image ahead of time, then configures its new immutable image ID.
Never build a repository-provided Dockerfile or install arbitrary packages on the controller.
Python plugin autoload is disabled; projects that require plugins need an explicit trusted
profile extension. Arbitrary model-provided commands are not supported.

A result status `passed` means the command exited zero, not that coverage, correctness,
security or test honesty is established. Untrusted tests may print misleading output or
exit zero. `completed` means both command receipts exist, even if one failed. Compare:

| Baseline / patched | Interpretation |
| --- | --- |
| failed / passed | Candidate improvement; inspect logs and coverage before accepting |
| passed / failed | Candidate regression or environment-sensitive behavior |
| failed / failed | Existing defects, dependency/environment problems or an incomplete fix |
| passed / passed | Both commands exited zero; this alone proves no behavioral change |

Timeout, output_limit, resource_limit, launch error and ordinary nonzero exit are retained.
Missing dependencies normally appear as a failed command plus its log, not as proof the
patch caused a defect. There is no fabricated test-count, correctness score or automatic
approval. Original proposal labels remain source-only; execution receipts are separate.

## Upgrade application from the exact Milestone 9 archive

Let active jobs finish. Extract separately and copy UPGRADE_FROM_MILESTONE_9.patch into
your existing project root. Preserve .env, Git history and database volumes. From there:

```bash
git apply --check UPGRADE_FROM_MILESTONE_9.patch
git apply UPGRADE_FROM_MILESTONE_9.patch

docker compose stop frontend backend worker dispatcher
docker compose build backend migrate worker dispatcher frontend
docker compose up -d postgres redis
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic check
docker compose up -d backend worker dispatcher frontend
```

Expect **0014_execution_runs (head)** and no schema drift. The additive migration creates
execution_runs and adds agent_runs.execution_feedback. Old runs remain readable. Downgrade
drops execution records and feedback; it is destructive, not a normal upgrade step.

Rebuild API, worker and dispatcher together. The proposal prompt fingerprint changed to
handle untrusted feedback; queued older proposal runs may fail with agent_config_changed.
Inspect their status before deliberately resubmitting. No previous run is auto-executed.

Initially keep APP_EXECUTIONS_ENABLED=false. The ordinary application works without a
sandbox host. Enable execution only after the following separate runner acceptance.

## Dedicated runner installation

Use a dedicated Linux host/user with rootless Docker, cgroup v2 and systemd delegation.
Follow current Docker rootless installation guidance linked below. Do not add Docker
socket mounts to the application Compose services. Do not expose Docker's daemon API.

Copy this release to the dedicated user's ~/repopilot-ai. On the runner host only:

```bash
cd ~/repopilot-ai/backend
uv sync --frozen
# This builds only our trusted image, not an imported repository Dockerfile.
docker build -f app/sandbox_runner/Dockerfile -t repopilot-sandbox:milestone-10 .
docker image inspect --format '{{.Id}}' repopilot-sandbox:milestone-10
docker info
```

The image ID should be sha256 followed by 64 hex characters. Keep it and set a random
shared token of at least 32 characters (for example generated with Python secrets).
Create ~/.config/repopilot-sandbox.env, mode 0600, with your actual values:

```dotenv
SANDBOX_TOKEN=<private-random-token>
SANDBOX_IMAGE_ID=sha256:<actual-local-image-id>
SANDBOX_STATE_DIR=/home/<runner-user>/.local/state/repopilot-sandbox
```

Do not put application database, Redis or AI-provider credentials on this host. Install
deploy/repopilot-sandbox.service as ~/.config/systemd/user/repopilot-sandbox.service,
then run on that host:

```bash
systemctl --user daemon-reload
systemctl --user enable --now repopilot-sandbox.service
systemctl --user status repopilot-sandbox.service
journalctl --user -u repopilot-sandbox.service --no-pager -n 100
```

The service binds 127.0.0.1:8090. Configure a trusted HTTPS reverse proxy with a valid
certificate; deploy/sandbox-nginx.conf.example shows the required body/time limits and
Authorization forwarding. Restrict ingress to application workers through firewall/VPN.
The app requires HTTPS, verifies certificates and follows no redirects. Never disable
certificate verification or expose the token over public plain HTTP. Keep exactly one
controller process; its file lock rejects accidental concurrent workers sharing state.

After passing the real-runner tests below, edit the application's existing .env:

```dotenv
APP_EXECUTIONS_ENABLED=true
APP_SANDBOX_URL=https://sandbox.example.com
APP_SANDBOX_TOKEN=<same-private-runner-token>
```

Recreate backend, worker and dispatcher to load settings. These credentials are server-only.
The runner need not access GitHub: the ordinary worker fetches the fixed public commit and
uploads its bounded archive. The sandboxed code has no network even while the controller
can communicate with application workers.

## Browser acceptance

Expect **10 / Sandbox validation** at http://localhost:3000.

1. Produce a small completed proposal in a named conversation.
2. In its sandbox panel select Auto/Python/JavaScript. Review the patch and tick the
   explicit execution checkbox, then click **Run isolated validation**.
3. Watch fetching/preparation/baseline/patched status. Reload: same execution ID and
   patch hash, no duplicate launch. The UI polls durable records every two seconds.
4. Review the immutable image ID, archive SHA-256, command, exit status, bounded log,
   duration and truncation indicator for each phase. Verify source remains unchanged.
5. Cancel queued and active work. Local status becomes cancelled immediately; the worker
   requests remote cancellation at its next observation. During a disconnected/lost worker,
   controller deadlines/reaper bound remaining work; cancellation is not instantaneous.
6. Try missing dependencies, unsupported/mixed roots, a hanging test and excessive output;
   expect bounded, explicit results rather than a claim of successful validation.
7. From a terminal result choose **Use results in a revision proposal**. Review/edit the
   prefilled task, then submit it. This sends bounded logs to the AI provider and may
   incur charges. A new proposal is made against the original source, not old test writes.
   Review it and authorize a new execution separately. At most two linked revisions.
8. Verify foreign-account read/list/cancel/feedback IDs return 404 and deletion of a
   conversation removes its application-side execution records. Running worker work
   cannot resurrect deleted records. Runner-side bounded receipts have separate retention.

## Reliability and retention

Admission limits are two executions/minute, ten/day per user, fifty/day per deployment,
one active run per proposal and twenty saved execution attempts per proposal. Idempotency
keys bind the proposal/profile. The runner persists a receipt before launching and a cancel
tombstone blocks a delayed POST. Lost/uncertain executions never restart automatically.
Queued delivery can retry; the database claim prevents duplicate execution. A worker loss
becomes a visible failure after the 300-second lease expires. Partial saved receipts remain.

The dedicated runner stores at most 1,000 receipts, removes terminal records after seven
days, and fails closed at capacity. Active input directories are deleted at completion;
startup removes abandoned input directories after stopping its orphan containers. Temporary
container filesystems are removed on exit/cancel/timeout. Application conversation deletion
does not immediately erase the runner's independent seven-day receipt/log cache. To erase
it sooner, stop the runner, confirm its containers are gone, and remove the selected local
receipt data under operator control. Do not delete receipt state while work is active.
Monitor runner disk space, failed service starts, orphan cleanup and certificate expiry.
Cleanup depends on a functioning Docker daemon/controller. If either becomes unresponsive,
operator intervention may be necessary; resource limits alone are not a wall-clock watchdog.

## API and changed files

- POST /agent-runs/{id}/executions: request_key UUID, profile auto/python/javascript,
  confirm_execution=true. 202 created, 200 identical replay, 409 active/conflict/disabled.
- GET /agent-runs/{id}/executions: enabled flag and latest twenty owner-visible runs.
- GET /executions/{id}: durable status, current phase and receipts.
- POST /executions/{id}/cancel: existing writer/CSRF protection, empty JSON object.
- Agent submissions accept optional feedback_execution_id. It must belong to this owner,
  conversation and source and contain terminal results. Feedback is frozen and bounded.

Browser paths prepend /api. Control endpoints are authenticated /runs/{UUID} POST/GET/DELETE
on the dedicated runner; the browser never receives its token or contacts it directly.
No shell/command override endpoint, repository push or original-checkout application exists.

Implementation paths: backend/app/execution/{contracts,client}.py;
models/execution.py; services/executions.py; schemas/execution.py;
api/routes/executions.py; jobs/{execution_run,execution_dispatcher}.py;
sandbox_runner/{server,docker,harness}.py and Dockerfile;
migrations/versions/0014_execution_runs.py; frontend/src/features/execution/ExecutionPanel.tsx.
Agent contracts/service/worker integrate frozen feedback. Exact paths are in
milestone-10-files.txt. ADR 0015 records the isolation decision and remaining limits.

## Validation commands

From backend/:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
uv run python -m app.evaluation.agents --check
uv run python -m app.evaluation.agents --mode propose --check
```

Ordinary tests do not execute repository code. They verify archive rejection, command
policy, output/cleanup handling, control API authentication/idempotency/cancellation,
source pinning, receipts, ownership, feedback bounds and terminal failure behavior.
Use an isolated *_test database following milestone-7a.md for real PostgreSQL/Redis tests:

```bash
uv run alembic upgrade head
uv run alembic check
RUN_DB_TESTS=1 RUN_QUEUE_TESTS=1 uv run pytest -m integration
```

The new real PostgreSQL test races two execution deliveries and expects one controller
submission. Two additional opt-in tests execute literal Python/JavaScript fixtures on a
**dedicated test Docker daemon**, not on a busy production runner. On that host:

```bash
export SANDBOX_IMAGE_ID=sha256:<the-built-image-id>
RUN_SANDBOX_TESTS=1 uv run pytest -q tests/integration/test_sandbox_docker.py
```

Expected: both pass, baseline fails its intended assertion, patched command exits zero,
and no named containers remain. The Python fixture checks UID, absent credentials/socket,
read-only root, disabled network, memory/PID cgroup limits. This is acceptance evidence
for that host, not a complete escape-resistance audit. Also manually exercise timeout,
output flood, cancel, controller crash/restart and runner unavailability before enabling.

From frontend/: pnpm install --frozen-lockfile, pnpm check, pnpm test, pnpm build.
The standard Vite native minifier still crashes in this implementation runtime as in
Milestones 8/9; the unminified diagnostic command `pnpm exec vite build --minify=false`
bundles successfully. Keep the normal minified Docker/CI build as an acceptance gate.
Measured test results and all unexecuted checks are in validation.md.

Official runtime references checked for this design:
https://docs.docker.com/engine/security/rootless/
https://docs.docker.com/engine/security/rootless/tips/
https://docs.docker.com/engine/security/

Suggested commit: feat(execution): add isolated patch validation and bounded test feedback

Completed: Milestone 10 implementation, migration, runner, UI, tests and upgrade guide.
Pending acceptance: actual dedicated runner, service/database/browser checks, normal minification.
Deferred: arbitrary dependency installation, monorepos/Go, stronger VM runtime, automatic
repair loops, original-repository modification and remote branches/PRs.
