# Security and data handling

## Identity and access

Passwords use Argon2id with 15–128 character passphrases. Session cookies are
HttpOnly, host-only, and SameSite=Lax; production requires HTTPS and Secure cookies.
Only session-token hashes are stored. Login rotates sessions and logout revokes them.

Every protected operation checks the authenticated owner. Writes require the
expected Origin, application header, JSON content type, and session-bound CSRF proof.
Shared Redis limits bound authentication and workload submissions and fail closed.

GitHub OAuth uses single-use, expiring browser-bound state and stable GitHub IDs.
Account linking is explicit; matching email addresses do not merge accounts.
Access tokens and PKCE verifiers are encrypted with a server-side Fernet key.
Private access uses GitHub's `repo` scope, which includes write permissions, although
RepoPilot's GitHub operations are read-only. Disconnect removes the stored token;
revoke the grant separately in GitHub settings.

## Repository and model input

Imports use commit-pinned GitHub archives with host restrictions, size and time
limits, and unsafe-path checks. Static indexing parses source without executing it.
Filename exclusions reduce accidental ingestion but are not a complete secret scanner.

Repository text, conversation history, diffs, and logs remain untrusted input.
Models receive bounded context through fixed provider endpoints. Their tools can
read authorized snapshots, with no shell, arbitrary network access, or Git writes.
Prompts and validated tool schemas limit capabilities; prompt injection and incorrect
answers remain model-quality risks.

Citation checks validate membership in retrieved evidence. They do not establish
that a claim is true. React renders source and model text as escaped text.
Draft output is provisional and enters saved history only after final validation.

## Execution boundary

Repository tests run only after approval on a [separate sandbox host](sandbox.md).
Application services never receive a Docker socket. Fresh rootless-Docker containers
use trusted images, disabled networking, read-only roots, and resource/time limits.
Containers share a kernel; this is not VM isolation.

Automatic repair requires authorization and allows up to two revisions, ten
additional model calls, three sandbox attempts, and a 20-minute deadline.
Passing untrusted tests does not establish correctness; inspect the patch and logs.

## Storage, logging, and cost

PostgreSQL retains imported source, saved conversations, run events, and usage
receipts. Named runs retain their submitted questions even after failure or cancellation.
Deleting a conversation or repository cascades its related application records.
Disconnecting GitHub does not erase imported code. Historical generations have no
automatic garbage collection. Backups follow separate operator retention policies.

The runner keeps independent bounded receipts for up to seven days; application
deletion does not immediately erase those records. Logs omit source, questions,
credentials, connection URLs, and OAuth query strings, retaining safe IDs and timing.

AI calls can still incur charges after cancellation or failed publication.
Unknown usage stays unknown; estimates and application quotas are not a billing
ledger. There are no automatic retries of ambiguous paid work. Review provider data
handling and configure provider-side spend limits before enabling paid features.

## Deployment limits

Email verification, password recovery, idle-session timeout, and all-device logout
are outside the current scope. Duplicate registration exposes registration status.
Keep deployments restricted to intended users. Production needs reviewed database
roles, TLS, secrets, image digests, backups, monitoring, and host maintenance.
[Deployment guide](deployment-operations.md) · [Pending acceptance checks](project-status.md).
