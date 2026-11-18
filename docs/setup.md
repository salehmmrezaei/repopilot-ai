# Development and configuration

Start with the [README quick start](../README.md#quick-start).
Host development uses Python 3.12, uv 0.12.17, Node.js 24, and pnpm 11.19.0,
matching CI. Keep both dependency lockfiles.

## Check local startup

From the project root:

```bash
docker compose ps -a
curl -i http://localhost:8000/health/live
curl -i http://localhost:8000/health/ready
curl -i http://localhost:3000/api/health/ready
docker compose run --rm migrate alembic current
```

Expect HTTP 200, healthy PostgreSQL/Redis/backend, running frontend/worker/dispatcher,
and a successful migration service. The current schema head is `0016_extensions`.
Liveness checks the API process; readiness checks dependencies.

## Host development

Stop the Compose application processes if already running, then start dependencies.
The following environment-loading commands use Bash and your local `.env`:

```bash
docker compose stop frontend backend worker dispatcher
docker compose up -d postgres redis
set -a
. ./.env
set +a
export APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@127.0.0.1:}"
export APP_REDIS_URL=redis://127.0.0.1:6379/0
cd backend
uv sync --frozen
uv run python -m app.embeddings.tokens
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log
```

In separate terminals, load the same backend environment and run these from `backend/`:

```bash
uv run celery -A app.jobs.celery_app:celery_app worker --loglevel=WARNING --concurrency=1
```

```bash
uv run python -m app.jobs.dispatcher
```

Start the frontend from the project root in another terminal:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open [localhost:3000](http://localhost:3000). Vite proxies `/api/` to
`API_PROXY_TARGET`, defaulting to `http://127.0.0.1:8000`.
Backend processes read exported variables; they do not load `.env` automatically.

## Quality checks

From `backend/`, with dependencies installed:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m 'not integration'
```

From `frontend/`:

```bash
pnpm check
pnpm test
pnpm build
```

These checks need no model key. CI additionally applies migrations, checks schema
drift, runs infrastructure tests and free retrieval benchmarks, and builds Docker images.

## Infrastructure integration tests

Use a disposable database ending in `_test`. With Compose dependencies running,
create it once from the project root; keep it if it already exists:

```bash
docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" repopilot_test'
```

In a fresh Bash terminal at the project root:

```bash
set -a
. ./.env
set +a
export APP_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/repopilot_test"
export APP_REDIS_URL=redis://127.0.0.1:6379/1
export APP_ANSWERS_ENABLED=false
export APP_EMBEDDINGS_ENABLED=false
cd backend
uv sync --frozen
uv run alembic upgrade head
uv run alembic check
RUN_DB_TESTS=1 RUN_QUEUE_TESTS=1 uv run pytest -m integration
```

Sandbox cases stay skipped unless separately enabled on the
[dedicated test runner](sandbox.md#verify-the-runner).
Do not point integration tests at application or production data.

## Optional features

Edit `.env`, then recreate Compose services with `docker compose up -d`.
For host development, reload the environment and restart affected processes.

| Feature | Configuration |
| --- | --- |
| Semantic search | Set `APP_EMBEDDINGS_ENABLED=true` and `APP_OPENAI_API_KEY`; prepare semantic search for the repository. |
| AI answers and PR review | Set `APP_ANSWERS_ENABLED=true` and `APP_OPENAI_API_KEY`. |
| Investigation and patch proposals | Also set `APP_AGENTS_ENABLED=true`; each run permits up to five model calls. |
| Test execution | Configure the [separate sandbox](sandbox.md) before setting `APP_EXECUTIONS_ENABLED=true`. |
| Automatic repair | Enable agents and execution, then `APP_REPAIRS_ENABLED=true`; authorize the bounded loop in the UI. |
| Local reranking | Select the search panel's relevance-scoring option; no model call is required. |
| Operations metrics | Set `APP_METRICS_TOKEN` to a random token of at least 32 characters; see [operations](deployment-operations.md). |

AI features send selected source, questions, and sometimes recent conversation or
test feedback to the provider and can incur charges. Set model, token limits,
request quotas, and estimated prices in `.env`; price settings are estimates.
The full setting definitions and validation live in
[`config.py`](../backend/app/core/config.py).

### GitHub OAuth and private repositories

Create a GitHub OAuth App and set `APP_GITHUB_OAUTH_ENABLED=true`,
`APP_GITHUB_CLIENT_ID`, `APP_GITHUB_CLIENT_SECRET`, `APP_GITHUB_TOKEN_KEY`,
and `APP_GITHUB_CALLBACK_URL`. The callback must match the OAuth App exactly;
the local default is `http://localhost:3000/api/auth/github/callback`.

Generate a Fernet key from `backend/`:

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use the same key in API and workers, and preserve it with encrypted backups.
Choose **Connect GitHub** to link an existing account. Private access requires
GitHub's broad `repo` scope; implemented application operations are read-only.
[Identity and token handling](security.md).

## Updates and troubleshooting

Before updating an existing installation, back up PostgreSQL and preserve `.env`
and volumes. Stop application writers, workers, and dispatcher; update the source,
then run `docker compose up --build -d`. Verify migration logs, readiness, and
`alembic current`. [Production upgrades](deployment-operations.md#backups-rollback-and-incidents).

| Symptom | Action |
| --- | --- |
| Port already in use | Stop the process using 3000, 8000, or 5432. |
| Missing database URL | Export settings for host commands or use Compose. |
| Host cannot resolve `postgres` | Use `127.0.0.1` for host processes; `postgres` is the container hostname. |
| Readiness returns 503 | Inspect `docker compose logs postgres redis migrate backend` and migration status. |
| Database password mismatch | Existing volumes retain their original credentials; restore matching settings or change the role password. |
| AI action unavailable | Check feature flags, provider key, and search preparation for the current source generation. |

Stop the stack with `docker compose down`. Keep volumes to preserve saved work.
