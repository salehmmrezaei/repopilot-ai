# Implementation roadmap

Source requirements: project-specification.md. The user selected React; ADR 0001
records the React + TypeScript + Vite change from the proposed Next.js frontend.

1. Runnable foundation — implemented; user reported completion of local validation.
2. Authentication — implemented; user reported completion of local validation.
3. Public repository import — implemented; user reported completion of local validation.
4. Python indexing and source browsing — implemented; user reported local validation complete.
5. Hybrid search and benchmark — implemented; user reported completion of local validation.
6. Grounded Q&A — implemented; user reported local completion.
7. Streaming MVP — implemented; user reported local completion:
   - 7A: saved conversations/messages and source snapshots — implemented; user reported local completion.
   - 7B: durable background answer runs, idempotent submission, cancellation, completed
     usage and polling/reload recovery — implemented; user reported local completion.
   - 7C1: bounded, frozen conversation context — implemented; user reported local completion.
   - 7C2A: durable lifecycle timeline, authenticated SSE reconnect/replay — implemented; user reported local completion.
   - 7C2B: real provider text streaming and provisional-answer UI — implemented; user reported local completion.
   - 7C3: durable generation/query-embedding receipts, React usage UI and final MVP acceptance procedure — implemented; user reported local completion.
8. Read-only investigation agent — implemented: typed tools, five-call bounded loop,
   durable jobs/usage/events, SSE timeline, source-backed answers and evaluation fixtures.
   Local infrastructure/browser/live-model acceptance pending; see milestone-8.md.
9. Plans and patch proposals — implemented: pinned base, source-checked diffs and downloads.
10. Isolated execution — implemented: separate runner, curated test environments.
    Deployment acceptance remains documented in milestone-10.md.
11. Bounded repair loop and coding benchmark — implemented; see milestone-11.md
    for PostgreSQL/runner/browser acceptance and separately authorized live evaluation.
12. Extensions individually: TypeScript, OAuth/private repositories, reranking,
    incremental indexing, PR review, deployment and deeper observability.

Milestones 1–7 deliver the initial repository-understanding MVP. Evaluation starts
with retrieval, background jobs start with import, and authorization starts before
user repositories. No major milestone advances without a clear validation gate.

Postponed after Milestone 7: React Query, Tailwind/shadcn, routing, local embedding adapter, learned reranking,
invoice reconciliation and indexing/standalone-call receipts, successful-import refresh, code execution, email verification/recovery and OAuth.
These are intentionally absent, not stub implementations.
