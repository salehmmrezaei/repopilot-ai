# Validation and scope

The source implements repository import/refresh, Python and TypeScript/TSX indexing,
search, cited answers, saved conversations, bounded investigations, patch proposals,
sandbox validation, repair, GitHub OAuth/private access, saved PR reviews, and
deployment/monitoring configuration.

## Recorded CI evidence

The existing review records a successful
[GitHub Actions run #65 on 6 October 2026](https://github.com/salehmmrezaei/repopilot-ai/actions/runs/37426995740)
at [commit `6fcaab0`](https://github.com/salehmmrezaei/repopilot-ai/commit/6fcaab0d7cbd61cd38de80a745669090b12d2f38).

| Check | Recorded result |
| --- | --- |
| Backend unit/API tests | 358 passed |
| PostgreSQL/pgvector and Redis integration | 12 passed; six sandbox tests skipped |
| Frontend | 69 tests across 16 files passed; checks and production build passed |
| Backend static checks | Ruff lint/format and mypy passed |
| Database | Migration to head and schema-drift check passed |
| Retrieval | Keyword, TypeScript, and reranked benchmark commands completed |
| Packaging | Backend and frontend Docker builds passed |

These historical results are retained from the project review. They are not a new
test run, a coverage percentage, or an AI-quality score. The
[CI workflow](../.github/workflows/ci.yml) and [local commands](setup.md#quality-checks)
define reproducible checks.

## Environment checks still needed

- **Sandbox:** run isolation/coding cases and baseline/patched validation on the dedicated host.
- **GitHub:** verify real OAuth callbacks, private permissions, and disconnect/reconnect.
- **Models:** review actual grounded answers, PR findings, and paid evaluation reports.
- **Operations:** verify HTTPS, migrations, backup/restore, monitoring, and recovery on the target host.
- **Demo:** replace the labelled generated preview with an actual application capture when available.

Patches and PR reviews require human review. The application does not push changes
or post reviews. Email verification/recovery, learned reranking, automatic cleanup
of old source generations, and unrestricted execution environments are outside the current scope.
