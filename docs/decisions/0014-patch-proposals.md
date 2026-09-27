# ADR 0014 — structured edits and deterministic review patches

Status: accepted for Milestone 9.

The existing bounded agent provides read tools, durable events, ownership, cancellation
and call accounting. Reusing its run lifecycle avoids a second queue and ensures a
cancelled proposal cannot publish after a late provider response. A frozen mode and
separate provider prompt/schema distinguish investigations from proposals. Existing
investigation fingerprints remain stable.

The model returns small typed edits, not arbitrary diff text. The application checks
old text/ranges against both pinned source and observed evidence, then renders unified
diffs deterministically. It computes content/patch hashes and stores the result in the
same completion transaction as the final event. Model usage commits independently.

This establishes syntactic source correspondence, not behavioral correctness. New-file
absence is only checked against imported paths; incomplete snapshots cannot prove full
repository absence. Original file modes are not known. These limitations are shown in
the UI and are mandatory inputs to a later complete-checkout validation stage.

We allow at most four files and one contiguous edit per file, LF text, no renames or
mode changes. This deliberately bounds review/output cost and fails unsupported tasks
rather than silently widening permissions. No repository writes, shell tools, test
execution, approval side effects, branches or pushes exist in this milestone.

Alternative: accept raw model diffs and run Git on uploaded repositories. Rejected for
this slice because raw patch parsing and workspace execution introduce a separate
security/operational boundary. Milestone 10 will handle isolated checkout/application/
testing with explicit execution controls. Patch download is an authorized read only.
