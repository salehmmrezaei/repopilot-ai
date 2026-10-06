# Security model — through Milestone 7C2B

Users, sessions, bounded imports, indexing, optional embeddings and grounded answers
are implemented. Repository code execution is not available. Public deployment
still requires the controls described below.

## Identity and writes

- Argon2id password hashes, 15–128 character new passphrases, no password trimming.
- Random session cookies; only their SHA-256 digests are stored in PostgreSQL.
- HttpOnly, host-only, SameSite=Lax cookies; production settings require Secure/HTTPS.
- Login rotates the browser session; logout revokes it; expiry is checked server-side.
- Every authenticated endpoint derives its user from the session. Frontend IDs do
  not establish authority. Repository and file actions now enforce ownership in database queries.
- Exact Origin plus a custom request header and JSON content type protect auth
  writes. Authenticated writes also require a session-bound CSRF proof. No permissive CORS.
- Validation errors omit raw values, preventing password reflection.
- Login failures use the same message for unknown accounts and wrong passwords.
- Duplicate registration returns 409; this exposes account-registration status.
  Email verification and privacy-preserving registration are postponed together.

## Resource limits and deployment boundary

Authentication and import submissions use atomic shared Redis counters. Redis
failures fail closed; no process-local fallback is used in production code. Imports
are limited to five attempts/hour/user and twenty stored repositories/user. The
hourly counter survives deletion. Redis is configured with a bounded noeviction
policy and append-only persistence. Password hashing remains offloaded and bounded
to two concurrent Argon2 operations per process.

Repository URLs are restricted to public github.com roots. HTTP requests only target
api.github.com and codeload.github.com with bounded bodies and no redirects. Commit
metadata is validated, and archives are pinned to the resolved SHA. No files are
extracted to the host. Unsafe paths reject imports; links/special files, credential
filenames, binaries, vendor/generated files, and oversized files are skipped.
Expansion, entry count, retained content, parsing time, and overall runtime are capped.
Filename exclusions are not a complete secret scanner. Never assume a public repo
contains no secrets. Source text is displayed through React's escaped rendering.

Jobs use atomic claims and expiring lease tokens. Cancelled/stale workers cannot
publish. Successful file publication is transactional. Repository removal cascades
to jobs and files. Retries cannot execute repository content.

Compose binds ports to loopback. API and frontend containers run as non-root. The
frontend sends a content security policy. Secrets stay server-side; `.env*` files
are excluded from Docker contexts and `.env` is excluded from Git. Existing DB
credentials must be retained during upgrades.

The local PostgreSQL provisioning account is not a least-privilege production
runtime account. Before public deployment, separate migration/runtime roles,
provision TLS, review shared abuse controls, add email verification/account recovery,
review dependency/image versions and pin deployable image digests, and review
operational endpoint exposure. Session idle timeout and all-device logout are later.

## Logging and data handling

Structured logs contain request IDs, route templates, duration, status, and safe
exception types. They omit request bodies, query strings, full connection URLs,
passwords, session cookies, CSRF proofs, and repository content. No error tracker
receives these values. Errors include a request ID for correlation.

Expired session rows are deleted during session creation. Logout deletes the
current row immediately. Users own sessions through a cascading foreign key.
Application responses never serialize password hashes or token hashes.

No arbitrary commands or imported code are executed. The import worker has CPU,
memory/process limits and a read-only filesystem, but is trusted application code
with database access. It is not a code-execution sandbox. Executing repository
code will require an independent isolation boundary with no application secrets.

See ADR 0002 and ADR 0003 for implementation choices and remaining tradeoffs.


## Milestone 4 static indexing

AST parsing occurs only in the existing resource-limited worker; imported source
is never executed. The API only queues jobs and reads results. Parser node/scope,
file, generation and deadline budgets are enforced. The Python parser can allocate
before node-count checking, so worker memory limits remain necessary. This worker
has trusted service credentials and must never become the repository-code executor.

Every indexing route enforces session ownership, and every write enforces CSRF.
Repository locks and the unique-current index serialize starts; shared Redis limits
new attempts to five per minute per user. Conditional lease publication prevents
cancelled/expired workers publishing data. Composite foreign keys enforce snapshot,
parent-symbol and chunk-symbol consistency. Source is escaped React text. Logs omit
source, signatures and docstrings. Failed replacement attempts retain the prior
completed index. Imported repository text remains untrusted prompt input when RAG
is added; indexing does not make it trusted instructions.


## Milestone 5 retrieval and external embeddings

Keyword/symbol mode does not call a model. Semantic mode defaults off and requires
server configuration plus an explicit user preparation/search action. Source and
queries are sent only to the fixed OpenAI embedding endpoint; keys never reach the
frontend or source context. The UI discloses this data transfer and possible charges.
Provider redirects/proxy inheritance are disabled, responses are bounded/validated,
and raw provider bodies are excluded from error messages. Tokenizer assets are
cached during image build so read-only workers do not need a runtime asset download.

All retrieval/preparation endpoints enforce repository ownership; POST queries
require CSRF. SQL is parameterized, and all candidate channels are filtered to a
completed authorized generation. Partial preparation is never searched. Native
vectors and chunk links are tied to the same source snapshot by composite keys.
The database connection is released before waiting for query embeddings.

Preparation reserves tokens before external calls and retains uncertain reservations
through crashes/retries. Per-user rate limits and a 25-second query deadline bound
requests. They do not replace a global paid-deployment budget or prevent multiple-
account abuse. Public registration/spend enforcement remains a deployment gate.
Source text is rendered escaped and remains untrusted input for future RAG prompts.
Retrieval scores are not confidence probabilities or authorization decisions.


## Milestone 6 generation boundary

Every settings/answer route checks repository ownership before provider configuration
or spend. POST requires session-bound CSRF. Generation is disabled by default.
When enabled, a deliberate question sends bounded retrieved source and the question
to a fixed HTTPS provider endpoint, with redirects and proxy inheritance disabled.
No tools, secrets, arbitrary endpoints or execution interfaces are exposed to the
model. The versioned system prompt treats all source as untrusted data. This reduces
impact; it does not prove prompt-injection resistance or factual accuracy.

Responses are size-limited, typed and checked for refusal/incompleteness. Model paths
and line ranges are never accepted as source authority: the UI gets those from
retrieval. Unknown/duplicate citation IDs reject the answer. React displays source
and claims as text with local anchors, without rendering model HTML/links. CSP remains.

The service has a 60-second deadline and releases DB connections before generation.
Shared Redis limits bound attempts per user and across this deployment. Failed calls
consume attempts; there are no automatic paid retries. These quotas are not a durable
financial ledger. Reset Redis, multiple deployments and uncertain provider outcomes
remain accounting risks. Configure provider-side spend controls and review account
abuse before any public paid deployment. Existing embedding spend needs its own
aggregate enforcement too.

Question and answer bodies are not stored or logged. Logs record generated answer
IDs, safe failure codes, model, accepted usage/cost and timing. Rejected citations
can still incur cost; usage is logged first. Incomplete/malformed/transport failures
can have unknown usage. Client disconnection is not a billing cancellation guarantee.
The provider request sets store=false, which is not a zero-retention assurance.
Review provider data handling terms before sending source. Private repositories
and execution remain outside the present scope.


## Milestone 7A transcript storage and retention

Named conversations now persist user questions, validated answers, quoted source
snapshots and accepted usage metadata in PostgreSQL. The M6 temporary-answer route
remains stateless. The UI distinguishes these modes. Earlier no-storage statements
above describe M6, not the new saved-conversation routes. Neither mode logs bodies.

Each conversation uses a composite repository/owner foreign key; all reads/writes
also check the authenticated owner. Client IDs alone convey no authority. Writes
require CSRF; model-produced or client-submitted roles/answer payloads cannot bypass
the existing provider and citation validation. Only server-validated results are
saved, with atomic question/answer publication. React renders stored titles/messages
and code as text. Source snapshots may outlive deleted indexes intentionally.

Deletion of a conversation removes its messages; deleting a repository or account
cascades down the ownership chain. Backup policies are separate and not implemented
as a user erasure guarantee. Bound lists/messages and per-repository/per-conversation
limits constrain growth. This is not yet a public retention/export policy.

No lock spans generation. If a conversation is deleted before publication, its answer
is not saved or the conversation recreated. Provider cost can still occur. Failed
or interrupted requests have no durable pending run, financial ledger or idempotency
key in 7A. Refresh history before a deliberate retry; do not describe this slice as
exactly-once or crash-recoverable generation. 7B must address those execution states.


## Milestone 7B durable execution

Run submissions persist the user question before generation, including questions
whose runs later fail or are cancelled. Runs inherit ownership via their conversation;
all submit/read/list/cancel endpoints enforce owner checks and POST CSRF. Cascade
deletion removes run records. Broker messages contain only run UUIDs, never source
or credentials. Configuration fingerprints exclude secrets.

Unique request keys and active-run constraints bound duplicate submission. Shared
admission/model quotas bound casual use, not a financial balance. Running work is
never automatically reclaimed after lease expiry because its provider outcome may
be unknown. Cancellation/deletion fence late publication but cannot guarantee remote
call cancellation. Run state, completed usage and transcript pairs commit atomically.
The legacy synchronous routes remain available and do not share run idempotency.

Usage marked unknown may be billable; completed totals are not an account-wide ledger.
The UI does not persist question/source bodies in localStorage. Reload fetches owned
server state without automatic resubmission. Streaming and event replay are not yet
implemented; polling reauthenticates on every request. Model history is still absent.


## Milestone 7C1 conversation context

Named background runs snapshot at most three recent answered pairs from the owned
conversation and the same source-index ID. Snapshot selection stops at an ineligible
pair. History is limited to 1,000 estimated tokens and 8 KiB; the complete prompt
retains its 8,000 estimated-token / 64 KiB limit. Whole oldest pairs are omitted first.
Only prior question and claim text are copied, not old evidence or citation metadata.
History remains untrusted JSON in a user payload, never a system/assistant role.
Prompt instructions discourage following embedded instructions; they are not a hard
prompt-injection defense. No execution tools or privileges have been added.

Current source evidence remains the only citation authority. Citation membership
checks do not prove factual grounding. History poisoning/incorrect previous answers
remain model-quality risks covered by the new evaluation fixture, not claimed solved.
The snapshot is retained with its answer_run until conversation/repository deletion;
existing cascading authorization and deletion boundaries apply. No history is logged.
The external-model disclosure now includes selected previous turns. There is no extra
model call; larger generation input can cost more. Temporary and legacy synchronous
questions keep empty history. New configuration hashes fence old queued runs across
this prompt/policy change. Do not requeue paid work automatically during upgrade.


## Milestone 7C2A lifecycle event streaming

Events contain only run ID, sequence, status and timestamp; no source code, questions,
credentials, prompts or hidden reasoning. The session cookie authenticates stream
requests, with an application header preventing simple cross-origin browser requests.
No CORS access is enabled. Read-only streams do not need the write CSRF token. Session
validity and repository/conversation ownership are rechecked on each one-second poll.
Already transmitted bytes cannot be recalled; logout/deletion stops subsequent polls.
No database session/connection remains open while awaiting the client or sleeping.

Connections last at most approximately 25 seconds of normal polling, with heartbeat
comments every five polls. Network/DB delays can extend this; browser requests abort
at 35 seconds. Redis limits starts to 30/minute and 300/hour per user, independent of
model-generation quotas. This is an admission rate limit, not a global concurrency
semaphore or deployment DDoS defense. Each stream performs bounded DB reads per poll;
production load should be measured before replacing polling with shared notifications.
Only one latest-run timeline per mounted composer streams; 10-second status polling
continues as fallback. Disconnecting never cancels or resubmits paid generation.

Event rows commit atomically with transitions. Completion also commits its messages
and usage in that transaction. A unique (run_id, sequence) primary key orders replay;
state guards suppress duplicate delivery. Rows cascade on run/conversation deletion.
Old runs get one honest current-state baseline during migration, not invented history.
Terminal streams end; reconnects use Last-Event-ID without placing credentials in URLs.
The browser validates event identity, sequence, schema and bounded frame/stream size.
The SSE channel is lifecycle-only. Token deltas and unvalidated answers are not emitted.


## Milestone 7C2B provisional model output

Streaming is opt-in at the read protocol via ?preview=true and automatic for the
React background-answer view. The worker uses the provider's streaming capability
when available; stateless/synchronous APIs still wait for their final result. No
provider key, prompt, reasoning event, tool event or raw provider envelope is sent
to the browser. Only selected claim/limitation strings from partial structured output
form the draft. They are untrusted text, explicitly provisional and potentially wrong.
The UI renders them as escaped plain text with no links/HTML execution or citations.
Already displayed text cannot be recalled from a user's memory or screenshots.

Preview fields never feed conversation history, retrieval or tools. A provider's
completed result must match the accumulated text and pass the existing model, usage,
structured-output and citation validations. Only then does normal atomic publication
occur. Refusal/error/truncation/cancellation never promote a preview into an answer.
Citation membership is still not proof of factual accuracy.

Updates require the running state, matching lease token and unexpired lease. They
are limited to one write per 250 ms, 256 revisions and 8,000 Unicode characters.
Terminal state/event writes clear text in the same transaction. A crashed worker's
text is hidden after lease expiry and cleared when dispatcher expiry runs; if the
dispatcher is unavailable, data remains stored until that transition or deletion.
Browser reconnects receive the latest snapshot, not a log of provisional tokens.
Lifecycle sequence IDs stay unchanged. Polling that discovers terminal state also
hides a stale draft if the stream is unavailable. Auth and ownership checks cover
preview frames exactly as lifecycle events; snapshots do not enter logs.

Bounded parsers reject oversized/malformed streams. Provider traffic is limited to
2 MiB, 256 KiB per SSE frame, 64 KiB accumulated JSON and 12,000 events. Existing
service/worker deadlines remain. There is no retry on an ambiguous provider failure.
After cancellation, a subsequent preview write can stop local reading; the remote
request may still complete and incur charges. Full per-call receipt accounting remains 7C3.


## Automatic repair (Milestone 11)

Disabled by default. Explicit consent bounds automatic generation/execution to two
revisions, ten additional model calls, three sandbox runs and a 20-minute deadline.
Existing quotas and source/owner/CSRF checks apply to each child. Parent transitions and
child creation/cancellation commit atomically; worker guards reject expired authorization.
In-flight charges and sandbox cleanup latency remain possible. Feedback contains bounded,
untrusted prior diff and logs; source must be read again and edits validated against the
original commit. Changing model configuration or execution environment stops the loop.
The app permits repository test edits, so test success must never stand in for human
review. The coding benchmark separately protects tests/configuration and labels reference
verification independently of live-model results. No automatic repository write or PR is
introduced. See milestone-11.md for operational acceptance and artifact retention.


## TypeScript parsing (Milestone 12A)

Source is parsed as data using pinned native grammars. No Node process, compiler, project
configuration, npm hook or repository code is executed during indexing. File/native-time/
node/depth/symbol/traversal bounds fall back to text with diagnostics. Native dependency
code still requires worker process/container limits and maintenance. Source/docstrings
remain untrusted and are rendered as escaped text. Exact symbol terms use parameterized
SQL arrays; they are not interpolated into full-text query expressions. Index/search/tool
versions prevent silently reusing incompatible generations. See milestone-12a.md for the
native-timeout compatibility note and explicit rebuilding procedure.
