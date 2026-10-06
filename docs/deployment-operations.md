# Deployment and operations

`deploy/compose.production.yml` is a standalone single-host Compose deployment,
not an override of the development file. It includes PostgreSQL/pgvector, Redis,
API, worker, dispatcher, frontend and Caddy TLS ingress. Only ports 80/443 are
published. The sandbox runner remains on a separate dedicated host as documented
in Milestone 10. Do not add a Docker socket to the API or worker.

## Prepare

Build and push the backend/frontend images to a registry you control, using the
included Dockerfiles. Record the resulting immutable `@sha256:` image references.
Select tested immutable digests for pgvector (PostgreSQL 17), Redis 7 and Caddy 2.
Store `.env.production` outside version control with restrictive permissions:

```dotenv
DOMAIN=your-domain.example
ACME_EMAIL=operator@example.com
BACKEND_IMAGE=your-registry/backend@sha256:your-digest
FRONTEND_IMAGE=your-registry/frontend@sha256:your-digest
POSTGRES_IMAGE=pgvector/pgvector@sha256:your-tested-digest
REDIS_IMAGE=redis@sha256:your-tested-digest
CADDY_IMAGE=caddy@sha256:your-tested-digest
POSTGRES_USER=repopilot
POSTGRES_PASSWORD=your-url-safe-random-password
POSTGRES_DB=repopilot
APP_DATABASE_URL=postgresql+asyncpg://repopilot:your-url-safe-random-password@postgres/repopilot
APP_METRICS_TOKEN=your-random-token-at-least-32-characters
```

Replace every placeholder. Point DNS at the host and permit inbound 80/443 for
TLS issuance/application access. Set optional OAuth/model/sandbox variables only
when activating those features. Defaults leave paid generation and sandbox execution
disabled. Production settings require HTTPS and secure cookies. Use a separate OAuth
app for development and production so callback URLs and credentials are unambiguous.
Keep this deployment restricted to intended users; email verification and password
recovery are still outside the project scope.

Validate the resolved Compose configuration without publishing its secret values in
logs, then start:

```bash
docker compose --env-file .env.production -f deploy/compose.production.yml config --quiet
docker compose --env-file .env.production -f deploy/compose.production.yml up -d
docker compose --env-file .env.production -f deploy/compose.production.yml ps -a
```

Verify migration exit status, API readiness, HTTPS, secure session cookies,
register/sign-in/sign-out, a public import, indexing, free search and background job
progress. Then activate optional integrations individually and run their acceptance
checks. Check the OAuth callback through the public HTTPS origin. Application and
proxy access logging omit API request URLs to avoid logging authorization codes.
Do not enable HTTP debug logs with private tokens or signed archive URLs.

The supplied image references are mandatory deployment inputs, not prebuilt images.
The configuration must be validated on the target host. This release does not
provision servers, DNS, registry access, certificates, backups or an external alert
receiver, and does not claim those services are active.

## Metrics

Configure `APP_METRICS_TOKEN` with at least 32 random characters. `/metrics` requires
`Authorization: Bearer <token>`; an absent/short token disables access. The public
Caddy route `/api/metrics` is blocked. Scrape directly from the private container
network. Mount `deploy/prometheus.yml` and `deploy/alerts.yml` into your Prometheus
installation, and mount a file containing the same token at
`/run/secrets/repopilot_metrics_token`. Keep Prometheus access private.

Metrics:
- `repopilot_http_seconds`: duration histogram by route template and status class.
- `repopilot_jobs`: current database counts by work type and status.
- `repopilot_oldest_pending_seconds`: age of oldest queued/running work per type.

HTTP metrics are per API process and reset when it restarts. The provided deployment
uses one Uvicorn process. Scrape each API instance independently if you scale out;
do not add database job gauges across replicas. Unknown paths share one label.
Do not introduce query text, repository names, tokens or user IDs into metric labels.
Job counts are snapshots, not completion counters. Alerts cover unavailable scrapes,
stalled queues, elevated 5xx rates and latency. Wire them to your own Alertmanager
receiver before relying on notifications. PR review latency contributes to HTTP
latency; tune thresholds for actual traffic after observing a baseline.

JSON logs retain request/job IDs, duration, safe error types/codes and known usage.
Completed answer/agent/execution/repair receipts and saved PR review records provide
per-operation detail. Estimated costs are not provider invoices; unknown usage is
not zero. Preserve the configured price/model versions alongside release notes.

## Backups, rollback and incidents

Back up PostgreSQL with a tested PostgreSQL-compatible backup procedure and encrypt
backups containing source code and OAuth credentials. Store the Fernet key separately
with recoverable access controls. Include Redis persistence and configuration in your
recovery plan, but durable job state lives in PostgreSQL. Regularly restore into an
isolated environment and verify ownership, source snapshots and migrated schema.
Never test restores over production volumes.

Before an upgrade, stop writers/workers/dispatcher, take a verified backup and record
image digests. Apply the migration once, then restart matching application versions.
Prefer a forward fix. To roll back this migration, first stop all new-code processes,
export any needed new review/connection data, and assess loss: downgrading to
`0015_repair_runs` drops OAuth states/accounts, saved PR reviews and per-import commit
SHA columns. Restore a pre-upgrade backup if rollback requires exact prior state.
Do not run `down -v` or delete volumes as a routine troubleshooting step.

For stalled work, inspect the dispatcher, broker connectivity, job lease/attempt
fields, worker version and feature flags. Repeated import/search requests remain
quota-bound. Investigate database disk usage if source generations accumulate.
For an interrupted PR review, inspect its pinned IDs and known usage; do not assume
no charge and do not automatically resubmit it. Disconnect or revoke compromised
GitHub credentials; remove imported repositories separately if their source should
be deleted. An encryption-key replacement requires reconnecting affected accounts.
