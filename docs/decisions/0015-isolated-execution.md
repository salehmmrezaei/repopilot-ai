# ADR 0015 — separate execution controller and fresh test containers

Status: accepted for Milestone 10 implementation; deployment acceptance required.

Repository test code is untrusted. API/worker processes may fetch archives and submit bounded
HTTPS jobs but cannot access Docker or execute repository commands. A dedicated rootless
Docker host owns container creation. Runtime images are built/reviewed ahead of time and
identified by immutable local image IDs; repository Dockerfiles are never built implicitly.
No networking/dependency installation is permitted inside execution containers.

We fetch the proposal's exact public GitHub commit and accept its entire bounded archive,
rejecting unsupported links/special entries. A trusted entrypoint inside an isolated
preparation container verifies base hashes, patch hash and complete after-manifest. Each
baseline/patched phase repeats preparation in a fresh container so test side effects cannot
cross phases. The controller records exit status and bounded logs, not a correctness claim.

Application rows and runner receipts provide separate durable identities. A database claim
and runner idempotency key prevent duplicate launch. Cancellation tombstones defeat delayed
submissions, and timeout/reaper/startup cleanup bounds work after crashes. Lost executions
fail visibly instead of retrying. Logs can seed at most two explicitly submitted revisions;
new execution always requires a fresh user action.

Tradeoffs: a separate host and trusted dependency images require operator setup. Root-only
Python/JavaScript profiles are intentionally narrow. Archive rejection, missing dependencies
and ambiguous project roots fail explicitly. Containers share a kernel; stronger VM/runtime
isolation is needed before offering unrestricted hostile multi-tenant execution. Local tests
of contracts are not substitutes for real runtime acceptance and ongoing host patching.
