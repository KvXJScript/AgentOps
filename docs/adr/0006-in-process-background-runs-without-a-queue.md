# ADR 0006: In-process background runs with startup recovery, without a queue

Status: accepted · Date: 2026-10-26

## Context

A report run takes several model calls (tens of seconds), so it can't block the HTTP request. The app is a
single-process local demo, and a queue such as Celery, RQ or Arq would add Redis, a worker process and a second
deploy unit. Proposal executions and chat persistence also outlive the request that started them.

## Decision

- `ReportService.create_run` stores a `queued` row and starts a tracked `asyncio` task. Clients poll
  `GET /reports/{id}` (`lib/reports/poller.ts`, 1 s, with backoff on errors). LangGraph runs without a
  checkpointer. Progress is persisted by `_RunTracker` (the `StageTracker` protocol) on every stage transition.
- Bounds live in the service: an `asyncio.Semaphore(REPORT_MAX_CONCURRENT_RUNS = 2)`, `asyncio.timeout(REPORT_TIMEOUT_S = 120)`
  around the whole run, and `recursion_limit = MAX_GRAPH_STEPS = 8`.
- Shutdown (`aclose`) gives active runs 5 s, then cancels them and stores `failed/interrupted`. Startup
  (`recover_interrupted`) marks any run still `queued`/`running` the same way. Proposals stuck in `executing` are
  finished at startup by `ApprovalService.recover_executing`, which is safe because `create_issue` is idempotent.
- Chat assistant messages are written in a shielded task, and shutdown waits for them (`wait_for_pending_writes`).
- Document ingestion stays synchronous in the upload request. `IngestionPipeline.run(document_id)` is the seam
  for a future worker.

## Alternatives considered

- **Celery/RQ/Arq + Redis.** Durable and horizontally scalable, but it is a second datastore and process for a
  single-user app, and it would add test infrastructure.
- **A Postgres-backed job table with `SKIP LOCKED` workers.** This is the natural next step since Postgres is
  already the store (ADR 0002). It was deferred because nothing needs multi-worker execution yet.
- **FastAPI `BackgroundTasks`.** No concurrency limit, no cancellation on shutdown, and no handle for recovery.
- **SSE for report progress.** Polling one row each second is simpler, survives reloads, and the run state is
  already persisted.

## Consequences

- A restart or crash fails in-flight runs (`interrupted`). They are never resumed, and users start a new run.
- Startup recovery assumes **one process owns all active runs**. Running two replicas would make each one mark the
  other's live runs as interrupted. Scaling out requires a queue or leases plus a LangGraph checkpointer, and
  `ReportService._execute` is the seam.
- `ReportSettings.shutdown_grace_s` (5 s) is not configurable through the environment.
- A crash during synchronous ingestion leaves a document in `processing`, and that is not recovered
  automatically.
