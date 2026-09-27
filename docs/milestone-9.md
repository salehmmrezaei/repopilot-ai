# Milestone 9 — implementation plans and patch proposals

## Delivered scope

The named-conversation agent now has two task types: read-only investigation and
implementation plan with patch. A proposal includes a cited rationale, implementation
steps, risks, suggested checks, changed-file hashes and a downloadable unified diff.
It survives reloads, uses the existing activity timeline and per-call receipts, and
shares the bounded worker, cancellation, ownership and idempotency protections.

The application never applies a patch, executes repository code, runs a proposed test,
creates a branch or pushes to GitHub. Milestone 10 owns sandbox execution. The only Git
application performed in our automated checks uses fixed literal test fixtures inside
isolated temporary directories; it never executes their contents.

A source-matching patch is not proof of correct, secure or working code. The UI labels
results **Source snapshot checked · Tests not run · Not applied**. Model-written test
plans are suggestions, not test results or approval records.

## Design and changed paths

| Layer | Paths and behavior |
| --- | --- |
| Typed edits | backend/app/agents/proposals.py — strict edit contracts, exact source checks, deterministic unified diff and SHA-256 fingerprints |
| Agent contract | backend/app/agents/contracts.py — proposal-mode decision schema, optional saved proposal on results |
| Model boundary | backend/app/agents/provider.py and prompts/proposal_v1.txt — dedicated instructions/schema and configuration fingerprint |
| Orchestration | backend/app/agents/engine.py — final citation validation, then proposal validation before publication |
| Worker | backend/app/jobs/agent_run.py and celery_app.py — select the frozen mode, authorize the pinned source, publish atomically |
| Persistence/API | models/agent.py, schemas/agent.py, services/agents.py, api/routes/agents.py — frozen mode, mode-aware request keys, authorized patch download |
| Migration | backend/migrations/versions/0013_patch_proposals.py — mode with investigate default and allowed-value constraint |
| React | frontend/src/features/agent/{api.ts,AgentPanel.tsx,AgentRunView.tsx,ProposalView.tsx} — task selector, plans, escaped diff, provenance, download |
| Evaluation | backend/app/evaluation/agents.py and evaluation/proposals/v1/cases.json — four versioned proposal cases with opt-in paid evaluation |
| Tests | backend/tests/unit/test_patch_validation.py, tests/api/test_proposals.py, tests/integration/test_agent_postgres.py, frontend/tests/proposals.test.tsx |

The exact complete path list is docs/milestone-9-files.txt; the upgrade patch includes
all changes. No new dependency or service is needed. ADR 0014 records the tradeoffs.

### Validation boundary

The provider proposes structured edits, not arbitrary diff headers. The application
constructs the diff itself. For each existing file it checks repository ownership,
source index and commit, exact 1-based inclusive line range and old text. Every edited
line must have appeared as exact source evidence in that run. An invented file, stale
range, uninspected line or mismatched old text fails the entire run with no published
partial proposal. Valid reported model usage is still retained.

Create checks absence and file/directory collisions in the imported snapshot. Imports
can omit binary, large, ignored or unsupported files, so absence is not proof that a
path is absent from the complete repository. The UI calls this out. A full checkout
check remains mandatory before any later application. Symlinks and file modes are not
represented by source snapshots; this release does not assert or alter existing modes.

Supported: at most four files, one contiguous edit per file, replace/create/delete,
LF UTF-8 text, portable relative paths with letters/digits/underscore/dot/hyphen/slash.
Deletion requires the entire file to have been inspected. New files use regular-file
mode 100644. No binary patches, renames, CRLF edits, empty-file deletion, mode changes,
multiple independent hunks proposed by the model, traversal or .git paths. Insertions
use an inspected anchor line in a replacement. Unsupported changes fail explicitly.

Edits contain at most 12,000 characters each for old/new text. Plans have 1–6 steps,
risks 1–5 entries, suggested tests 1–6 entries; each entry is at most 500 characters.
Published diff is at most 64 KiB. Existing bounds remain: five paid model calls, four
read-only tools, 120-second loop deadline, 8,000-token input cap, 5,000 evidence tokens,
16 regions and configured output cap (default 1,200, maximum 2,000 tokens). Large tasks
may fail or abstain; split them deliberately. No automatic paid retries are introduced.

## Upgrade from the exact Milestone 8 archive

Let active runs finish. Back up/commit your work. Extract the new archive separately,
then copy UPGRADE_FROM_MILESTONE_8.patch into your existing repopilot-ai root. Preserve
.env and database volumes. From that existing project root:

```bash
git apply --check UPGRADE_FROM_MILESTONE_8.patch
git apply UPGRADE_FROM_MILESTONE_8.patch

docker compose stop frontend backend worker dispatcher
docker compose build backend migrate worker dispatcher frontend
docker compose up -d postgres redis
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic check
docker compose up -d backend worker dispatcher frontend
docker compose ps -a
```

Expected migration: **0013_patch_proposals (head)**, no schema drift, healthy backend,
PostgreSQL and Redis, running frontend/worker/dispatcher. If patch checking fails,
merge the named local changes using the full source archive; do not overwrite .env.
The migration adds mode=investigate to existing runs; their results remain readable.
Downgrade intentionally deletes proposal-mode runs and their cascading events/receipts
before dropping mode. It is destructive and is not an ordinary upgrade step.

Rebuild the worker as well as the backend. Mode is frozen at submission and part of
the configuration fingerprint. Old workers cannot process proposal-mode work correctly.
Investigation mode retains its earlier fingerprint. A configuration mismatch fails
visibly; inspect run state/usage before deliberately creating a new submission.

No new .env settings. To generate proposals, keep APP_AGENTS_ENABLED=true and your
private APP_OPENAI_API_KEY configured. It reuses APP_ANSWER_MODEL, output-token and
price settings plus APP_AGENT_DAILY_REQUEST_LIMIT. Model calls incur charges. Check
configured rates before interpreting cost estimates; billing reconciliation is absent.

## Browser workflow and acceptance

At http://localhost:3000 expect **09 / Patch proposals**.

1. Import a public repository, build its source index and prepare keyword search.
2. Open a named conversation. Under **Investigate or propose changes**, select
   **Implementation plan and patch** as Task type.
3. Ask for a small concrete change in a known imported file. Click **Generate patch proposal**.
4. Watch queued/running, read-tool events and the final proposal validation event.
5. Review cited rationale, implementation steps, risks, suggested checks, full diff,
   pinned commit and content hashes. Confirm the untested/not-applied label.
6. Download the patch. Reload and reopen the same run: identical diff and receipts,
   no new model request. An insufficient-evidence response has no downloadable patch.
7. Cancel a queued run and one during a call: no later proposal publication. An in-flight
   charge may still be reported. Unknown receipts never mean free.
8. Test another account: run/detail/events/download should all return 404 for foreign IDs.
9. Delete the conversation: saved proposals, events and receipts are deleted together.
10. Check the earlier investigation and Q&A modes still work after the upgrade.

Downloaded files are review artifacts. Before any future application, compare the
pinned commit and all before-content hashes against a complete isolated checkout,
review additions for collisions and inspect every change. Do not apply to the original
working repository through this application. Sandbox application/testing is Milestone 10.

## API additions

Existing endpoints and authentication remain. Browser paths have /api prepended.

```json
POST /conversations/{id}/investigations
{"request_key":"<UUID>","question":"Update the greeting in main.py","mode":"propose"}
```

Mode defaults to investigate for existing clients. 202 creates; 200 replays an identical
key/question/mode. Reusing a key for another mode is 409. Only one active run per
conversation across both modes is allowed. Existing quotas and the 200-run retention
cap apply to both. There is no apply, approve, shell or execute mode.

GET /agent-runs/{id} returns mode and optional result.proposal. The proposal contains
implementation_plan, risks, test_plan, files with before/after SHA-256, diff,
diff_sha256 and validation=source_checked_tests_not_run. Old results get proposal=null.
GET /agent-runs/{id}/patch returns an owner-authorized text attachment with no-store
and nosniff headers. Missing/incomplete/failed/abstained patches return 409. Foreign or
deleted runs return 404, unauthenticated requests 401. Download makes no provider call.

## Automated checks

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

Ordinary tests use fake providers and spend no API credit. Patch tests use git apply
against literal fixture files, including Unicode/no-trailing-newline/add/delete cases.
Nine infrastructure cases require real services. Follow the dedicated *_test database
setup in milestone-7a.md (never use the application database), then:

```bash
uv run alembic upgrade head
uv run alembic check
RUN_DB_TESTS=1 RUN_QUEUE_TESTS=1 uv run pytest -m integration
```

The PostgreSQL duplicate-delivery test now covers both investigation and proposal modes.
Its presence is not evidence of a real concurrency test run in this environment.

From frontend/:

```bash
pnpm install --frozen-lockfile
pnpm check
pnpm test
pnpm build
```

The implementation runtime still reproduces Milestone 8's native Vite minifier Bus
error after transformation. Checks and component tests pass. The diagnostic build is:

```bash
pnpm exec tsc --noEmit
pnpm exec vite build --minify=false
```

This creates larger production assets. Normal build configuration remains unchanged;
a successful standard minified Docker/CI build is an outstanding acceptance gate.
See validation.md for measured results, not just expected checks.

## Optional live evaluation

Four fixed-corpus tasks cover a focused edit, a two-file change, missing evidence and
an instruction injected into source comments. The runner uses the same proposal loop
and source validator with in-memory tools. It is not a PostgreSQL retrieval benchmark,
a sandbox test run, a patch-correctness guarantee or evidence of production quality.
From backend/, with APP_DATABASE_URL and private APP_OPENAI_API_KEY configured:

```bash
uv run python -m app.evaluation.agents --mode propose --allow-paid \
  --output evaluation/proposals/reports/v1.json
```

The explicit flag authorizes up to 20 paid calls. Reports record model, prompt/dataset
hashes, actions, source/citation validity, proposal presence, tokens, unknown calls,
known estimated cost and latency. Human patch correctness, plan quality, citation support
and injection resistance require grading; repository_tests_run is always false.
No paid calls or model-quality scores are claimed in this release.

## Common failures

- proposal_source_invalid: stale/invented/unread text, path collision or unsupported
  source format. Inspect the failed run and receipts, then narrow the task if retrying.
- proposal_output_invalid / agent_output_invalid: invalid structured decision; no
  partial patch is published. Valid usage can still appear on the failed run.
- agent_source_missing: the pinned index was removed before finalization.
- proposal_unavailable: no completed proposal exists to download.
- agent_step_limit / agent_context_limit: task exceeded a bounded budget; split it.
- agent_config_changed: worker/API configuration or prompt mismatch; rebuild both.
- source_checked_tests_not_run is a static validation state, not an error and not
  a claim that repository tests passed.

Suggested commit: feat(proposals): add source-checked implementation plans and review patches

Completed: Milestone 9 implementation, migration, UI, tests, evaluation fixtures and guide.
Pending acceptance: real services/migration, browser, live model evaluation and standard minified build.
Next milestone: 10, isolated application and test execution, after acceptance.
