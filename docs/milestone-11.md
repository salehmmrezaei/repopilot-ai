# Milestone 11 — bounded repair and coding benchmark

Implemented: a durable, opt-in automatic test → revise → test workflow and a
synthetic coding benchmark using the actual agent, patch validator and isolated runner.
This release does not automatically apply a patch to a repository, push code or create a PR.
Infrastructure and live-model acceptance gates below remain required.

## Upgrade from Milestone 10

Back up your database and working tree. Use the full archive in a new folder, or apply
`UPGRADE_FROM_MILESTONE_10.patch` from your Milestone 10 project root:

```bash
git apply --check UPGRADE_FROM_MILESTONE_10.patch
git apply UPGRADE_FROM_MILESTONE_10.patch
```

The patch is generated against the delivered Milestone 10 archive; if your local files
differ, review conflicts rather than overwriting your changes. The `.env`, database
volume, users, sessions and existing source snapshots are preserved. Do not use
`docker compose down -v` as an upgrade step.

Keep repair disabled until Milestone 10's dedicated rootless runner is accepted.
Add `APP_REPAIRS_ENABLED=false` to your existing `.env`. Rebuild the application:

```bash
docker compose build
docker compose run --rm migrate
docker compose up -d
```

Migration head is `0015_repair_runs`. For host development, from `backend`:

```bash
uv sync --frozen
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

`APP_REPAIRS_ENABLED=true` requires both existing `APP_AGENTS_ENABLED=true` and
`APP_EXECUTIONS_ENABLED=true` at runtime. Keep the existing provider key, HTTPS
sandbox origin and sandbox token configuration. Restart backend, worker and dispatcher
together after enabling. No Docker socket belongs in any application container.
The runner API and curated image from Milestone 10 remain compatible.

## User workflow

1. Import/index a public Python repository and prepare search. Request a patch proposal.
2. Inspect the proposal. In **Bounded automatic repair**, select the test profile and
   one or two revisions, then explicitly authorize automatic model revisions and
   sandbox execution. This is separate from approving one isolated validation run.
3. The initial patch is tested. An ordinary patched-test failure permits a fresh
   proposal against the same original commit using the original task and saved,
   untrusted feedback. Each new proposal is source-checked and tested separately.
4. Reload at any time. Repair history shows every attempt, diff/download, model,
   usage, execution result, image/archive/patch fingerprints and stop reason.
5. Review the final diff and test coverage before applying it yourself. A zero exit
   code is not proof of task correctness; the application does not protect repository
   tests from edits. The coding benchmark separately protects its tests/configuration.

Revision prompts include bounded baseline/patched output (1,500 characters each),
up to 6,000 characters of the prior diff, its hash and truncation flag. They carry an
explicit untrusted-data notice. The model must inspect original source again and
produce a complete replacement proposal against that original source, not a stack
of incremental patches. The existing 8,000-input-token/64-KB prompt budget still applies;
large feedback can cause a budget stop instead of an oversized model request.

## Bounds, recovery and accounting

| Bound | Behavior |
| --- | --- |
| Revisions | User selects 1 or 2; inherited manual feedback depth plus this budget cannot exceed 2 |
| New model calls | At most 5 per revision, 10 additional total; original proposal usage remains visible |
| Sandbox attempts | Initial proposal plus at most two revisions; fresh baseline/patched containers each time |
| Deadline | 20 minutes including queue waits; worker checks prevent new calls/launches after expiry |
| Concurrent repair | One running repair per conversation, enforced in PostgreSQL |
| Repair submissions | 5 per user/day; at most 20 saved repair runs per root proposal |
| Existing quotas | Agent and execution quotas apply to every child submission; quota exhaustion stops the loop |
| Unknown usage | Preserved separately; excluded from known estimated cost, never called free |

The dispatcher makes short row-locked transitions; it does not execute code or call
models. A new child and the parent pointer commit in one transaction. Deterministic
request keys and durable child records prevent duplicate paid work when a dispatcher
restarts or receives concurrent work. Failed transitions roll back their child before
recording a terminal stop. Existing workers and their leases continue to own execution.
Lost/uncertain model or sandbox work is not automatically retried.

Stop states: `passed` (patched command exited zero), `exhausted` (revision limit),
`cancelled` or `stopped` (including repeated diff, no proposal, source/model changes,
changed image/archive/profile/command, resource limit, provider/infrastructure error,
quota, feature disable or deadline). Only a completed execution with ordinary
baseline status and ordinary patched failure triggers a revision. Passing baseline
is allowed for an application repair; the benchmark requires a failing baseline.

Cancellation atomically fences the active child and prevents new children. Model calls
already in flight can still incur charges; receipts are retained. An execution worker
requests remote cancellation at its next check, while runner deadlines/reaping bound
orphan work. A dead dispatcher delays the visible parent stop; worker guards still
reject expired repair work. A call/container already running at the deadline may take
time to stop under the Milestone 10 limits. Controller/daemon failures still require
operator monitoring. Deleting the conversation cascades application records; independent
runner receipts retain the existing seven-day lifetime.

## API

All routes are owner-scoped. POST requires existing session, Origin and CSRF controls.

| Route | Purpose |
| --- | --- |
| `GET /agent-runs/{id}/repairs` | Feature availability and latest 20 repairs |
| `POST /agent-runs/{id}/repairs` | Start with UUID `request_key`, `profile`, `max_revisions` and `confirm_automatic_repair: true` |
| `GET /repairs/{id}` | Parent plus all child proposal/execution/usage records |
| `POST /repairs/{id}/cancel` | Idempotent cancellation with `{}` body |

Submission replay returns the original repair even after completion; reusing its key
with different limits/profile returns 409. A lost response must be retried with the
same key. React preserves that key and freezes fields until resolution; persisted
runs are recovered from the server on reload. An unresolved submission across a full
browser reload should be checked in history before starting another repair.

## Coding benchmark and dashboard

Four synthetic development tasks cover Python boundary validation, stable deduplication,
cross-file configuration and JavaScript pagination. Each has immutable source, protected
tests/configuration, editable-path labels and a reference patch. Source content hashes
pin this synthetic corpus; the all-zero commit marker denotes a fixture, not a Git commit.
These tasks are small development fixtures, not a held-out or production benchmark.

Free structural validation, with no provider or sandbox calls:

```bash
cd backend
uv run python -m app.evaluation.coding --check
```

First verify that each buggy baseline fails and its reference patch passes on the
accepted dedicated runner. Configure `APP_DATABASE_URL` (required by shared settings,
not used for a database connection here), `APP_SANDBOX_URL` and `APP_SANDBOX_TOKEN`.
Set `SANDBOX_IMAGE_ID` to the accepted immutable `sha256:...` image ID:

```bash
uv run python -m app.evaluation.coding --verify-fixtures --allow-execution --image-id "$SANDBOX_IMAGE_ID" --output evaluation/coding/reports/reference.json
```

This makes four sandbox runs and zero model calls. The command exits nonzero if a
reference does not pass or its baseline does not fail. Investigate image/dependency
issues before interpreting any model run. Run on a dedicated idle runner; a busy
runner is an infrastructure error, not an automatic retry or a model failure to hide.

After deliberately authorizing provider charges, configure `APP_OPENAI_API_KEY` and run:

```bash
uv run python -m app.evaluation.coding --allow-paid --allow-execution --image-id "$SANDBOX_IMAGE_ID" --output evaluation/coding/reports/live.json
```

This permits at most 60 model calls and 12 sandbox runs across four cases. It bypasses
application request quotas; use a provider account spending cap. Calls/runs are sequential,
with five model calls/120 seconds per proposal and 270 seconds per sandbox request.
Use `--max-revisions 0` for first-attempt-only measurement or `1` for one repair.
Never run model-generated fixture code on the CLI host; the CLI only builds archives
and sends them to the authenticated isolated runner. Reference solutions are never
included in model prompts. The benchmark refuses edits outside labeled source paths.

Each run writes JSON and an adjacent HTML dashboard. Open the HTML locally; it needs
no server, scripts, CDN or network. Reports include first-attempt/final pass rates,
per-task attempts and errors, known token/cost totals, unknown-call count, latency,
model/config/prompt/dataset/corpus hashes, image ID, diffs and bounded sandbox receipts.
All cases, including failed/invalid baselines, remain in the rate denominator. A task
passes only if its baseline fails and its patched command succeeds. Reference mode is
explicitly labeled and must never be presented as model performance.

Human graders should fill `human_patch_correctness` (0 incorrect, 1 partial, 2 fully
satisfies the task) and `human_notes`, checking hardcoding, scope, regression coverage
and misleading test changes. Compare only matching corpus, image, prompt and budgets;
report changed configurations explicitly. The benchmark reuses real agent/proposal/
execution logic, but fixture tool lookup is in-memory and does not measure production
PostgreSQL retrieval, browser behavior or GitHub import performance.

## Acceptance gates

Run backend lint/types/tests and frontend checks/tests/build. In a migrated dedicated
PostgreSQL `_test` database with Redis, run the existing CI suite plus:

```bash
RUN_DB_TESTS=1 uv run pytest tests/integration/test_repair_postgres.py -q
```

On the dedicated rootless Docker host with the trusted image:

```bash
RUN_SANDBOX_TESTS=1 uv run pytest tests/integration/test_sandbox_docker.py tests/integration/test_coding_sandbox.py -q
```

Then verify browser consent, failed test → new proposal → passing test, stop at chosen
budget, repeated-patch stop, reload/restart, cancellation during both model and sandbox
work, deadline, changed source/config/image, quota exhaustion, owner isolation and
conversation deletion. Confirm no changes to imported source and no late publication.
Run reference verification before a separately authorized live benchmark; review all
four diffs and retain both JSON/HTML reports with their hashes. No live score is claimed
by this release. See `validation.md` for checks performed in the build environment.
