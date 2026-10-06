# README visuals

## product-preview.png

This is the previously prepared AI-generated illustration of RepoPilot's
`GroundedAnswer` presentation, with an authored source-verified example.
It is **not an application screenshot, recorded model answer or live execution result**.
The disclosure appears in both the image and README caption.

The illustrated question is: “How does cancellation prevent a stale worker from
publishing results?” The excerpt is from `backend/app/jobs/state.py`, lines 73–79
at reviewed commit `6fcaab0d7cbd61cd38de80a745669090b12d2f38`.
Supporting implementation lives in `backend/app/services/repository.py` and
`backend/app/jobs/import_repository.py`. No latency, cost or accuracy metrics
are fabricated for the visual.

### Replace with a real screenshot/GIF

1. Start the application using the root README. Import this repository and pin the
   displayed commit. Build its source index and prepare search.
2. Enable the configured AI feature deliberately; a real answer incurs provider
   usage. Ask the cancellation question above.
3. Wait for the final validated response. Expand the relevant source evidence.
   Preserve the actual answer, citations, source SHA and displayed metadata;
   do not substitute the authored example.
4. Capture the real question, answer and source panel at a readable desktop
   viewport. Exclude secrets and unrelated personal information.
5. Save as `docs/assets/product-screenshot.png` (or a short GIF), replace the
   README image link and caption, and record the capture date/commit here.

A screenshot was unavailable during this review: the repository contained no
product capture and the available local environment had no browser executable.
The preview is temporary and must not be described as a screenshot.

## architecture.svg

Editable, deterministic SVG of the implemented control plane and durable work
path, based on `docker-compose.yml`, the dispatcher, job state and execution
boundaries. It is a conceptual architecture image, not a network/deployment inventory.
API-to-provider/GitHub calls used by synchronous PR review and OAuth are noted in
the image; individual endpoints and all database connections are intentionally omitted.
