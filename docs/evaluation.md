# Evaluation

Versioned synthetic fixtures exercise retrieval, answers, investigations, patch
proposals, and coding tasks. They are development regression datasets.
Results on these small fixtures do not establish production model quality.

## Free fixture checks

From `backend/`, after installing locked dependencies:

```bash
uv run python -m app.evaluation.retrieval --check
uv run python -m app.evaluation.retrieval --check --dataset evaluation/retrieval/typescript-v1
uv run python -m app.evaluation.answers --check
uv run python -m app.evaluation.answers --check --dataset evaluation/answers/followups-v1/cases.json
uv run python -m app.evaluation.agents --check
uv run python -m app.evaluation.agents --mode propose --check
uv run python -m app.evaluation.coding --check
```

These validate fixture structure and source labels without provider or sandbox calls.

## Database-backed retrieval

Configure the migrated disposable `_test` database using
[the integration setup](setup.md#infrastructure-integration-tests), then from `backend/`:

```bash
uv run python -m app.evaluation.retrieval --output evaluation/retrieval/reports/keyword.json
uv run python -m app.evaluation.retrieval --rerank --output evaluation/retrieval/reports/reranked.json
uv run python -m app.evaluation.retrieval --dataset evaluation/retrieval/typescript-v1 --output evaluation/retrieval/reports/typescript.json
```

The runner uses the actual indexing, search preparation, PostgreSQL candidate
queries, fusion, and context construction. Compare reranking on the same corpus.
CI runs free keyword benchmarks and uploads their reports.

| Metric | Meaning |
| --- | --- |
| Recall@K | Fraction of distinct relevant source labels found within the first K results |
| MRR@8 | Average reciprocal rank of the first relevant result within eight results |
| p50/p95 latency | Measured request times on the sequential fixture, rather than a load test |
| Tokens and estimated cost | Known provider work, with unknown usage recorded separately |

The Python fixture has 14 questions; TypeScript/TSX has ten. The keyword
Recall@5 regression floor is 0.5. Review per-case failures and retain dataset,
corpus, pipeline, prompt, model, and pricing provenance with selected baselines.

## Paid model evaluation

Configure provider settings first. These commands explicitly permit paid calls;
offline evaluators bypass application request quotas.

```bash
uv run python -m app.evaluation.retrieval --mode hybrid --allow-paid --output evaluation/retrieval/reports/hybrid.json
uv run python -m app.evaluation.answers --allow-paid --output evaluation/answers/reports/answers.json
uv run python -m app.evaluation.answers --allow-paid --stream --dataset evaluation/answers/followups-v1/cases.json --output evaluation/answers/reports/followups-streamed.json
uv run python -m app.evaluation.agents --allow-paid --output evaluation/agents/reports/agents.json
uv run python -m app.evaluation.agents --mode propose --allow-paid --output evaluation/proposals/reports/proposals.json
```

Answer reports separate answered/insufficient-evidence status and citation membership
from correctness. A reviewer grades correctness, groundedness, and citation support
from 0 (unsupported/incorrect) to 2 (fully supported/correct), retaining notes.
Missing grades stay null. Check abstention and malicious-source/history cases.
Compare matching fixtures and record model/prompt changes.

Streaming reports include first-delta and final latency. A first provider delta can
contain JSON syntax, so it does not equal the first readable browser draft.

## Coding and sandbox evaluation

The four-task coding fixture protects test/configuration files and records
first-attempt and final pass rates, failures, usage, and human-review fields.
It produces JSON and a static HTML dashboard. Configure `APP_DATABASE_URL`
(required by shared settings), `APP_SANDBOX_URL`, `APP_SANDBOX_TOKEN`, and
`SANDBOX_IMAGE_ID` using the [accepted dedicated runner](sandbox.md).

First verify baseline/reference pairs with no model calls:

```bash
uv run python -m app.evaluation.coding --verify-fixtures --allow-execution --image-id "$SANDBOX_IMAGE_ID" --output evaluation/coding/reports/reference.json
```

A deliberate live run also permits provider charges:

```bash
uv run python -m app.evaluation.coding --allow-paid --allow-execution --image-id "$SANDBOX_IMAGE_ID" --output evaluation/coding/reports/live.json
```

Reference verification checks fixtures and the execution environment, rather than
model quality. Failed or missing execution is never counted as a pass.
Generated reports are ignored by Git; deliberately retain reviewed baselines and
their configuration. [Recorded CI evidence](project-status.md).
