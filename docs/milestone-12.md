# Milestone 12 — complete extension release

This release includes Milestone 12A and implements all remaining extensions in the
implementation roadmap: refresh/incremental indexing, GitHub OAuth/private access,
reranking, PR review, deployment assets, and operational metrics. It is a source
release, not a claim of live deployment or evaluated model quality.

## Delivered behavior

| Area | Behavior | Boundaries |
| --- | --- | --- |
| TypeScript/TSX | Existing static symbols, source chunks and browsing from 12A | Syntax analysis only; no compiler or repository code execution |
| Refresh | Refresh a completed import from GitHub; failed/cancelled refresh can be retried | Full bounded archive download; old successful source remains available during download/failure |
| Incremental indexing | Reuse unchanged path/language/content under the same parser pipeline; parse new/changed files; omit deleted files from the new index | Immutable generations retain prior files/chunks for existing provenance; reuse is within one repository |
| Search reuse | Reuse matching embedding inputs from the latest completed compatible search generation | Same repository, provider profile, mode and retrieval pipeline; changed path/signature/content changes the input hash; reused documents incur zero new embedding tokens |
| GitHub OAuth | Sign in or explicitly link a signed-in account; optional private repository permission; disconnect stored repository credentials | GitHub.com only; OAuth app configuration required; no email-based account merging |
| Private repositories | Owner-specific encrypted tokens used for import, refresh, pinned sandbox downloads and PR snapshots | Access removal prevents later downloads, but does not erase imported source; remove the repository to delete its local data |
| Reranking | Optional deterministic local second-stage relevance scoring after RRF | Not a learned cross-encoder; no model cost; quality improvement is not asserted without benchmark results |
| PR review | Saved review of immutable merge-base/head snapshots with validated evidence IDs and known usage | One model call, at most eight small changed files, no execution or GitHub comments |
| Deployment | Standalone HTTPS single-host Compose configuration with private database/Redis, one-shot migration and pinned image inputs | Supply images, domain, credentials, backups and hosting; no live service was provisioned |
| Observability | Authenticated Prometheus metrics for HTTP duration/status, job counts and oldest pending work; scrape and alert examples | HTTP metrics are process-local; database gauges are global; no repository text or user identifiers in labels |

## Upgrade from Milestone 12A

1. Back up PostgreSQL and retain your existing `.env`. Stop application writers,
   workers and the dispatcher while applying the upgrade. Do not delete volumes.
2. Use this full source archive, or apply `UPGRADE_FROM_MILESTONE_12A.patch` from
   the root of an unchanged 12A source tree. First run `git apply --check` on the
   patch. If you have local modifications, review and merge conflicts manually.
3. Install the locked dependencies (`uv sync --frozen` in `backend` and
   `pnpm install --frozen-lockfile` in `frontend`). The backend adds cryptography;
   the frontend adds Terser. Python 3.12 and Node 24 remain the supported setup.
4. Apply `alembic upgrade head` using your existing database settings. New head:
   `0016_extensions`. It adds import commit snapshots, GitHub account/state tables,
   and saved PR review records. Existing current completed imports are backfilled
   with the repository's last commit SHA. No existing source or conversations are deleted.
5. Rebuild and restart API, frontend, workers and dispatcher together. With the
   development Compose stack, `docker compose up --build -d` runs the migration
   service before the application starts. Check migration logs and health status.
6. Open a completed repository and choose **Refresh from GitHub**. After it
   completes, build its source index and prepare search. Index and search reuse
   happen automatically; no new embedding request is needed for reused inputs.

The parser and retrieval text versions are unchanged from 12A; no forced rebuild
is needed merely to install this release. A completed refresh switches source
browsing to the new import. Search then requires indexing/preparation for that
new generation; it does not silently answer from the older generation.
Historical conversations retain their pinned provenance. Source changes still
stop runs that require the previous active source.

The index job's `files_skipped` counter records parser reuse. Its `files_stored`
remains the total files represented in the new index. Search usage counters record
new provider work only, not the original cost of reused vectors. Old generations
consume storage until their repository is removed; automatic retention/GC is not
part of this release.

## Activate GitHub access

Create a GitHub OAuth App with the exact callback URL you will configure, for
example `https://your-domain/api/auth/github/callback`. Set these server-side values
in your existing `.env` or secret manager:

```dotenv
APP_GITHUB_OAUTH_ENABLED=true
APP_GITHUB_CLIENT_ID=your-client-id
APP_GITHUB_CLIENT_SECRET=your-client-secret
APP_GITHUB_TOKEN_KEY=your-generated-fernet-key
APP_GITHUB_CALLBACK_URL=https://your-domain/api/auth/github/callback
```

Generate the key locally, without committing it:

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

The same encryption key is required by API and workers. Keep it with encrypted
backups. Losing/changing it requires users to reconnect; there is no automatic
key rotation/re-encryption command. Tokens and PKCE verifiers are encrypted in
PostgreSQL. OAuth state is hashed, browser-bound, session-bound when linking,
single-use, and expires after ten minutes. State is consumed before exchanging
the authorization code. User identity is the stable numeric GitHub ID, never a
matching email address. A new GitHub-only user has a synthetic display email and
an unexposed random password; sign in again using GitHub.

**Connect GitHub** links an existing signed-in account. **Sign in with GitHub**
creates or signs into the GitHub-linked identity. Select private access only if
needed. GitHub's `repo` OAuth scope includes write permission; RepoPilot's implemented
operations remain read-only. The UI explains this before authorization. Organization
SSO and OAuth restrictions may require organization approval on GitHub.

Disconnect removes the stored access token but preserves the GitHub identity binding
and imported data. It does not revoke the grant remotely. Revoke it separately in
GitHub's application settings if desired. GitHub-only accounts can reconnect by
signing in again. HTTP clients do not forward OAuth bearer tokens to signed archive
URLs; redirects are restricted to HTTPS `codeload.github.com`.

Official protocol references:
- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- https://docs.github.com/en/rest/repos/contents

## Use reranking

Enable **Rerank results using local relevance scoring** in the search panel.
The API accepts `rerank: true` on search requests. The response names the policy
`local-relevance-v1`; otherwise it reports `none`. Up to 90 fused candidates are
rescored using query coverage, path/symbol/signature matches and exact identifiers,
with deterministic path/offset ties. Evidence construction still enforces the
original source and token bounds. Agent and Q&A retrieval defaults are unchanged.

Compare RRF and reranking on the same database-backed fixture:

```bash
uv run python -m app.evaluation.retrieval --output evaluation/retrieval/reports/rrf.json
uv run python -m app.evaluation.retrieval --rerank --output evaluation/retrieval/reports/reranked.json
```

Use a migrated disposable PostgreSQL database whose name ends in `_test`, as in
prior retrieval evaluations. CI runs both free keyword configurations. Semantic
benchmarks still require explicit `--allow-paid`. A successful fixture check is
not a retrieval quality score.

## Use PR review

Open a repository, enter its GitHub PR number and choose **Review PR**. AI answers
must be enabled; the action makes one bounded paid generation request using the
configured answer model and prices. Private repositories additionally require a
connected credential with access. Saved reviews are visible after reload.

The reviewer fetches a PR's base/head IDs and the immutable comparison's merge base,
then reads bounded archives at that merge base and head. It supplies diffs, affected
static symbol names, complete small changed files and a few matching nearby test
files as untrusted evidence. It never executes tests. A PR updated after the snapshot
is fetched does not change that saved review; inspect its displayed SHAs.

Limits: eight changed files, 8 KiB per changed file, at most 20 evidence regions,
64 KiB/8,000 input tokens, configured answer output cap, 90-second overall request,
ten review requests per user/day plus the shared deployment answer quota. Excluded,
binary, oversized or incomplete comparisons fail explicitly. Changes outside the
importer's supported source selection are not silently labeled reviewed. Fork commits
must be downloadable through GitHub's base repository endpoint; inaccessible commits
fail. Large PRs should be reviewed manually or split into smaller changes.

Each request key is idempotent. Retrying that key recovers its saved state without
another model call. Known usage is persisted before citation validation; unknown
usage is displayed as unknown. If the API process dies mid-request, a saved `running`
record can remain uncertain. There is no automatic paid retry. A new request key is
an explicit new review and may incur another charge; operators can inspect stale
records using the pending-work metric. A review with no supported finding is not an
approval. Claims remain model output requiring human review. Nothing is posted to
GitHub and no branch, commit or PR is created.

## Deploy and observe

See [deployment operations](deployment-operations.md). Production assets are in
`deploy/`. The production frontend build uses Terser for JavaScript and retains
unminified CSS (about 11 KiB before compression), avoiding the native minifier crash
seen in the prior execution environment. The standard `pnpm build` command is used.

## Validation and remaining live acceptance

Portable tests cover refresh availability, immutable reuse, deletion/new-source
behavior, embedding reuse without another provider call, OAuth state/CSRF/replay,
identity binding/encryption, private archive redirect isolation, PR snapshot/usage/
idempotency/citation validation, metrics authorization and frontend flows. Existing
agent, execution and repair tests remain part of the full regression suite.

See `validation.md` for final counts. PostgreSQL/Redis integration, actual Docker
sandbox isolation, a real GitHub OAuth callback/private repository, live-model review
quality, and HTTPS hosting must be verified in your configured environment. No live
GitHub authorization, paid review, deployment, or production migration was performed
while preparing this release.
