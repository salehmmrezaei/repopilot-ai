# ADR 0016: transactional repair controller and isolated coding evaluation

Status: accepted for Milestone 11 implementation; deployment acceptance pending.

A repair consists of existing proposal and execution records. A small durable parent
controls their order; it is not a new long-running worker with hidden in-memory state.
Transitions lock the parent and atomically create one child with a deterministic request
key. A nested transaction rolls back refused work before saving a stop. PostgreSQL
uniqueness enforces one running repair per conversation. Cancellation locks in the same
order and fences children. Worker guards check parent authorization/deadline so a paused
dispatcher cannot authorize stale queued model calls or container launches.

The user authorizes the whole loop once, with at most two additional proposals, five
model calls per proposal, three sandbox attempts and a 20-minute deadline. All existing
quotas remain effective. Every new proposal targets the original source; bounded prior
diff and test output are untrusted observations. Repeated patches, changed environment,
resource failures and uncertain work stop rather than trigger more spending. Final
patches remain explicit review artifacts. Passing tests is not a correctness guarantee.

The coding benchmark uses these same agent, source validation, repair policy and runner
contracts. A separate fixed synthetic corpus avoids private code and protects test files
from edits. Reference verification proves the fixture/image contract only after real
sandbox execution; it is never a live-model score. Free --check validates structure and
source matching without execution. JSON records unknown usage and per-case failures;
a static escaped HTML dashboard exposes aggregates without introducing a dashboard
service or arbitrary HTML from test logs. Real retrieval and held-out repository tasks
remain future evaluation work.
