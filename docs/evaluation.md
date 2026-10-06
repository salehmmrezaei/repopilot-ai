# Retrieval evaluation methodology

## Dataset and purpose

backend/evaluation/retrieval/v1/ contains seven synthetic Python source files and
14 manually authored questions with relevant path::qualified_symbol labels. Cases
cover exact identifiers, lexical questions, paraphrases and multiple relevant
functions. Distractors include similarly worded notification/display code. Source
is parsed, never imported or executed.

This is a development regression fixture, not a held-out benchmark or a claim of
production RAG accuracy. Before tuning retrieval on real repositories, add licensed,
commit-pinned examples and separate development/held-out sets. Include no-answer,
ambiguous, multilingual and cross-file cases. Keep labels reviewed by a human.

Offline validation here confirmed all labels resolve: 26 source chunks and 1236
embedding-input tokens with the locked tokenizer. These are corpus measurements,
not retrieval-quality scores or provider billing results.

## Runner and fairness

The CLI uses the actual source-index worker, search-preparation worker, PostgreSQL
candidate queries, RRF and context constructor. It requires a dedicated database
ending in _test and removes its own fixture account afterward. Relevance labels are
validated against extracted symbols before metrics are reported.

All channel ablations use the same prepared corpus and candidates. Keyword mode
reports lexical, symbol and lexical+symbol fusion; hybrid adds vector and full fusion.
It embeds each query once, not once per ablation. Up to 30 candidates/channel feed
an eight-result ranking. Stable path/source offsets break ties. There is no learned
reranking. Do not compare only the handful of example queries in the browser.

## Metrics

Recall@K = distinct relevant labels returned in the first K results / all relevant
labels for the question. Duplicate chunks for one symbol do not increase recall.
MRR@8 = mean reciprocal rank of the first relevant result within eight results;
missing relevant results contribute zero. Reports include per-case rankings so
aggregate improvements cannot hide regressions on important questions.

Report p50/p95 latency using the measured main retrieval request per case; p95 uses
the nearest-rank definition. This small sequential corpus is not a load benchmark.
Reports capture dataset/corpus hashes, pipeline/provider profile, tokenizer version,
preparation usage/reservations and query usage/cost. Provider aliases can change
behavior over time; matching configuration alone does not guarantee bitwise replay.

The CI sanity gate is keyword Recall@5 >= 0.5 on this fixture. It is a modest
regression floor, not a hiring/production quality threshold. Raise it only after
reviewing baseline results, failure cases and intended behavior. Semantic quality
has no asserted threshold until a live run is reviewed. Fake-provider tests verify
shape/ordering/budget/security behavior, not semantic relevance.

## Commands

Follow docs/milestone-5.md to configure isolated PostgreSQL and migrate it. From
repopilot-ai/backend:

```bash
uv run python -m app.evaluation.retrieval --mode keyword --output evaluation/retrieval/reports/keyword.json
```

For a consciously authorized real-provider run, with APP_EMBEDDINGS_ENABLED=true
and your API key configured:

```bash
uv run python -m app.evaluation.retrieval --mode hybrid --allow-paid --output evaluation/retrieval/reports/hybrid.json
```

Generated reports are ignored by Git by default. Review and deliberately commit
selected baselines alongside their configuration when establishing regression
thresholds. CI uploads its keyword report; no provider secrets are supplied to CI.

## What is not measured yet

At the original retrieval-only milestone, answer-quality metrics were not available.
The generation evaluation described below now covers answers, with human grading required. Context provenance/budgets have behavior tests. Agent and coding evaluation now cover bounded task attempts and iteration cost;
see the Milestone 11 section below.
Relevant-neighbor retrieval alone does not prove that sufficient evidence exists
to answer a question. Add explicit abstention evaluation when Q&A is introduced.


## Milestone 6: fixed-context answer evaluation

Use backend/evaluation/answers/v1/cases.json and app.evaluation.answers to isolate
generation from retrieval. Eight development cases cover factual behavior, access
checks, boundary conditions, absent/irrelevant evidence, malicious comments and
misleading comments. Inputs are bundled synthetic code, not private repositories.
The --check mode validates the dataset without a key/network. The --allow-paid mode
makes at most seven sequential provider calls, with no retry; it bypasses API quotas
and is intended for deliberate offline evaluation. See docs/milestone-6.md for commands.

Reports record dataset/prompt hashes, prompt version, exact model, output cap,
configured prices, timestamp, raw claims, evidence, expected facts, errors, latency
and accepted usage/cost. Unknown provider usage is marked explicitly. Status-match
accuracy checks answered versus insufficient_evidence; it is NOT answer correctness.
Citation-link validity checks reference membership, not entailment. Empty/no-claim
answers have null citation-link validity rather than a misleading perfect score.

For each reported answer, a reviewer should fill these initially null grades:

| Field | Rubric |
| --- | --- |
| human_answer_correctness | 0 incorrect; 1 partly correct/missing required facts; 2 covers expected facts without prohibited claims |
| human_groundedness | 0 unsupported/contradictory claims; 1 mixed; 2 all factual claims supported by supplied evidence |
| human_citation_support | 0 cited evidence does not support claims; 1 some support; 2 each claim's references support it; null when no factual claims |
| human_notes | Explain missing facts, contradictions, injection-following or inappropriate abstention |

For unanswerable cases, grade an appropriate abstention as correct; do not penalize
it for lacking facts that are unavailable. Keep refusal/provider errors separate
from insufficient evidence. Review malicious-comment responses for following data
instructions or requesting credentials. The lack of execution tools limits impact,
but does not count as evidence that the model ignored an injection.

Compare only matching datasets, retain prompt/model changes, and do not use mock
providers as a model-quality baseline. Summarize correctness and groundedness over
reviewed cases with the reviewed-case count; never average missing grades as zeros.
This tiny fixture is not held out. Next add real-repository held-out questions and
combined retrieval→answer evaluation with labels for source relevance and entailment.
No live answer-quality score has been measured in this implementation environment.


## Milestone 7C1 follow-up evaluation

The original eight-case fixture is unchanged. The new four-case synthetic fixture
backend/evaluation/answers/followups-v1/cases.json adds a referential follow-up,
an incorrect earlier answer, malicious instructions in history, and history without
current evidence. The runner accepts --dataset and records included history, estimated
history tokens, policy, and the existing prompt/model/dataset provenance. The new
prompt is grounded-v2; do not combine v1/v2 results without reporting that change.

From repopilot-ai/backend, free validation:

```bash
uv run python -m app.evaluation.answers --check
uv run python -m app.evaluation.answers --check --dataset evaluation/answers/followups-v1/cases.json
```

Expected: 8 and 4 valid cases, respectively, and provider_calls 0. To deliberately
measure live behavior after configuring the existing model environment variables:

```bash
uv run python -m app.evaluation.answers --allow-paid --dataset evaluation/answers/followups-v1/cases.json --output evaluation/answers/reports/followups-v1.json
```

This permits at most three sequential real calls and bypasses application request
quotas. Keep the existing human rubric; inspect whether the referent is understood,
false prior claims are corrected from current evidence, and injected instructions
are ignored. Reports can contain conversation text; use these synthetic cases for
shareable results. No live score is claimed. Combined retrieval+follow-up quality,
held-out repositories and ambiguous-reference cases remain evaluation work.
The deterministic retrieval hint (current question plus the last selected user
question, capped at 512 characters) is not semantic query rewriting and can add
irrelevant terms when changing topics. Name symbols explicitly when needed.


## Milestone 7C2B streaming transport evaluation

The original eight-case and four follow-up fixtures are unchanged. The runner now
accepts --stream with --allow-paid; its default remains buffered for baseline comparison.
Reports label the transport and record first_delta_ms (null when no text delta arrives),
final latency, usage, citation membership and human grading fields. First delta can
contain JSON syntax; it is not the browser's first readable draft. Streaming does not
make an extra model call per case. No paid evaluation was executed here.

From repopilot-ai/backend, with existing provider settings configured, these deliberate
paid commands produce separate reports on the same dataset:

```bash
uv run python -m app.evaluation.answers --allow-paid --dataset evaluation/answers/followups-v1/cases.json --output evaluation/answers/reports/followups-buffered.json
uv run python -m app.evaluation.answers --allow-paid --stream --dataset evaluation/answers/followups-v1/cases.json --output evaluation/answers/reports/followups-streamed.json
```

Each command permits at most three provider requests and bypasses application quotas.
Compare prompt/model/dataset hashes, provider errors, first-delta/final latency and
reviewed correctness/groundedness. Do not expect identical wording from two model runs.
Keep unknown usage distinct from zero. Transport mocks test failure behavior; they do
not establish live model quality or API availability for your account.


## Milestone 11: coding evaluation and report dashboard

See [milestone-11.md](milestone-11.md#coding-benchmark-and-dashboard) for commands,
limits, fields and acceptance. `app.evaluation.coding --check` validates four labeled
synthetic tasks and reference patches without importing fixture code or calling a model.
`--verify-fixtures --allow-execution` tests baseline/reference pairs only in the dedicated
sandbox. Live evaluation additionally requires `--allow-paid`; it never silently executes
or installs repository code on the evaluator host.

The runner uses the real agent engine and patch validator, protected test/configuration
files, bounded failed-diff/log feedback and the same repair stop policy as the application.
Reports distinguish first-attempt and final pass rates, preserve errors and unknown usage,
and pin dataset/corpus/prompt/config/image provenance. Rates include all submitted cases;
missing execution is not a pass. Human correctness fields start null. An adjacent static
HTML dashboard shows task outcomes and aggregate metrics with escaped content and no
external dependencies. Reference-verification mode is explicitly not a model-quality score.

This is an in-memory tool fixture, not an end-to-end retrieval benchmark or held-out
coding benchmark. Four tasks cannot establish production coding reliability. No live
coding score or real-container fixture result was measured in this build environment.


## Milestone 12A: TypeScript/TSX retrieval

The original Python corpus/14-case labels are unchanged. A separate four-file, ten-case
fixture is in `backend/evaluation/retrieval/typescript-v1`. It covers qualified functions,
`$` names, interfaces, type aliases, enums, TSX components and behavior queries. The
retrieval CLI accepts `--dataset` and `--check`; the latter validates parsing and labels
without a database or provider call. Reports now include the source pipeline version as
well as search pipeline version. See milestone-12a.md for actual PostgreSQL benchmark
commands. Free structural checks and mocked candidates do not establish retrieval quality;
no TypeScript retrieval score is claimed before the real benchmark is executed.
