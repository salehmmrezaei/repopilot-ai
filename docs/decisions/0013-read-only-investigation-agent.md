# ADR 0013 — Explicit bounded read-only investigation loop

Status: accepted for Milestone 8.

The specification permits either LangGraph or an explicit orchestration loop. Choose a
small typed loop: there is one sequential actor, four read-only tools and a five-call
budget. A framework would add state machinery without simplifying this workflow.
AgentProvider, Tools and Observer protocols isolate provider transport, repository access
and persistence. The provider emits a strict structured decision; application code
validates and dispatches it. These are application-managed tool calls, not unrestricted
provider-hosted tools. No raw reasoning is requested, stored or displayed.

Investigations have separate durable runs, events and per-step call receipts. They are
associated with a conversation but do not silently enter its Q&A transcript or dialogue
memory. This avoids changing the established answer contract and makes a multi-call run
observable independently. Final source snapshots remain readable if an index is replaced.
Conversation/repository deletion cascades through investigation data.

The worker claims once, commits an unknown receipt before each model call, and fences
publication/events by lease and status. Known usage may complete after cancellation.
Ambiguous running work is never automatically retried. Queue delivery can be retried;
model execution cannot be assumed idempotent. An explicit new request key starts new paid work.

All tools query database snapshots under server-supplied user/repository/index IDs. No
filesystem, shell, interpreter, Git write, Docker socket or arbitrary network tool exists.
Read-file source regions receive stable derived IDs and include the pinned commit and
line range; their chunk_id is an evidence identifier, not a promise of a CodeChunk row.
Keyword search reuses the existing retrieval system and rejects source-index drift.
Symbol lookup uses static Python symbols; references are bounded literal matches, not a
call graph. Source-text prompt injection remains a model-quality risk, so prompts mark
it untrusted and hard tool capabilities enforce the security boundary independently.

Bounds: five model calls, at most four tool actions, 120 seconds, 8,000 total input tokens
per call, 64 KiB serialized input, 5,000 source-content tokens and 16 evidence regions.
A file read returns at most 80 complete lines/6,000 bytes; oversized individual lines
are omitted. Search/symbol/reference results are bounded to four candidates. Event count
is DB-capped at 32. Input/output estimates reuse configured answer-model rates; no query
embeddings are purchased in this milestone. User/deployment admission quotas bound cost.

SSE reauthenticates each poll and replays durable sequence IDs; React also polls saved
run detail. Public events contain action labels, tool/path/query summaries, counts and
durations. Provider schemas, source snapshots and tool results never become executable
instructions. Citation-ID validation proves reference membership, not semantic correctness.
Human support/correctness evaluation remains necessary.

Postponed: graph orchestration, parallel agents/tools, semantic reference resolution,
agent-specific hybrid retrieval, iterative writes, patch proposals and all execution.
