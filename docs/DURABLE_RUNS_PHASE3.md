# Phase 3 — durable autonomous repair runs

Implemented September 7, 2026. No frontend redesign, streaming UI, cloud deployment,
new language support, live provider calls, Codex timeout changes or installed
Hermes modifications. Phase 1 validation policy and Phase 2 budgets are unchanged.

## Files

Created:
- app/run_models.py
- app/run_store.py
- app/migrations/__init__.py
- app/migrations/v001_runs.py
- app/routers/runs.py
- hermes_run_dispatcher.py
- hermes_run_recovery.py
- hermes_checkpoint_retention.py
- tests/test_runs.py
- tests/check_runs_postgres.py
- docs/DURABLE_RUNS_PHASE3.md

Modified:
- app/database.py — run ordered migrations after existing-table initialization.
- app/main.py — register durable run routes.
- hermes_repair.py — optional observer and shared cancellation event, preserving limits.
- hermes_service.py — host dispatcher lifecycle and durable reservation checks for legacy repair.
- README.md — operating-guide link.

## Migration and schema

Version 1 is additive. The existing projects table is preserved. A
schema_migrations table records applied versions; PostgreSQL advisory transaction
locking serializes migration application. Backend startup applies pending versions.
No destructive downgrade is provided. Back up the database before future migrations.

| Table | Stored data |
| --- | --- |
| project_runs | Managed project/run identity, fixed operation/task, stage, requested/current revision, counts, repair flag, persistent cancellation, owner/lease expiry, timestamps, final classification, fixed safe summary, cleanup confirmation |
| project_run_events | Unique ordered per-run sequence, actual event, stage, fixed safe summary, timestamp |
| project_model_attempts | Repair cycle, fixed configured model/provider, started/completed/failed/interrupted result, safe category, duration |
| project_validations | Cycle, revision, fixed profile, pass/status, bounded structured errors, duration, cleanup confirmation |

Foreign keys connect records to projects/runs. A unique nullable active_project
reservation enforces one active durable modifying run per project across processes.
The reservation remains on uncertain cleanup even if the visible stage is terminal.
No raw model response, prompt, header, credential or unrestricted log is stored.
The task is fixed text. Error messages are replaced with a fixed static-validation
message; file/check/code/location fields are bounded. Model attempts describe
Hermes repair invocations, not every SDK retry inside an invocation.

## Lifecycle and leases

queued → claimed → baseline_validation → repairing → validating → completed/failed.
Cancellation uses cancelling → cancelled after confirmed cleanup. Lease loss leads
to interrupted, never successful or automatically requeued. Final failure_class is
distinct from the transient stage and preserves Phase 2 result classifications.

The Windows host runs one sequential dispatcher thread. It is not a FastAPI
BackgroundTasks queue: job ownership, cancellation and recovery state are in
PostgreSQL. Claims use SELECT FOR UPDATE SKIP LOCKED, then commit owner and expiry
atomically. Each host dispatcher claims at most one run at a time. PostgreSQL is
the production concurrency authority; SQLite is only an offline unit-test backend.

Leases last 15 seconds and renew every 2 seconds using database time. A heartbeat
observes persistent cancellation and signals the Phase 2 cancellation event.
Database failures or lost/expired ownership also signal cancellation. Every stage
event checks current ownership before committing. Database connect/statement/lock
timeouts are bounded; they do not alter provider timeouts. Expired reservations
are never handed to another model worker. The Phase 2 filesystem lock still guards
actual writes, including legacy synchronous runs. Legacy host repair admission
also refuses an existing durable reservation, and fails closed if its protection
query cannot be performed.

The four-validation/three-repair limits, 600-second run budget, 120-second repair
cap, restricted tools, tool/API budgets and no-progress decisions remain in code.
Database fields cannot grant execution permissions or raise those limits.

## Cancellation and recovery

Queued cancellation is committed immediately and frees the reservation because no
worker was launched. Active cancellation stays persistent while the heartbeat
signals the worker. Windows process-tree termination and the existing bounded
validator cleanup run before release. The database reservation is released only
when worker and all returned validator cleanup confirmations are true.

Crash handling is conservative:
- Queued jobs remain queued and can be claimed after restart.
- Claimed, repairing, validating and cancelling jobs expire into interrupted.
- Started model-attempt records become interrupted when recovery observes expiry.
- No interrupted model work is automatically retried.
- Checkpoints and the durable project reservation remain for review.
- A missing process is not treated as evidence of successful termination.

Operator recovery is explicit and never launches a model:

```powershell
# Stop the Hermes host bridge first; keep PostgreSQL and Docker available.
.venv\Scripts\python.exe hermes_run_recovery.py <run-id>
```

The command requires Windows inspection to confirm no Hermes bridge/worker Python
process is running and Docker inspection to confirm no Phase 1 validator containers
remain. It rejects uncertain inspection, unsafe paths and mismatched lock ownership.
It clears the matching filesystem lock and durable reservation, records
recovery_confirmed, and keeps the previous run unsuccessful. Restart the bridge and
explicitly submit a fresh run with the current revision. Do not restart the bridge
concurrently with operator recovery. This is a trusted-local operator procedure,
not automatic distributed fencing of an administrator-controlled Windows machine.

## Checkpoint retention

After confirmed normal cleanup, preserve checkpoint 0, the latest checkpoint whose
revision received a completed trusted validation, and the latest repair checkpoint
(also the final partial/failed checkpoint when present). Delete only other numeric
checkpoint directories and their metadata inside that validated run directory.
UUID, containment, symlink/reparse/hardlink checks apply before deletion. Interrupted
or uncertain runs are not pruned. Model tools cannot access this runtime directory.

This bounds copies within a run (normally at most three retained; at most four
before pruning). Historical runs are not globally expired in this phase, so disk
usage can still grow across runs. No database-history deletion policy is applied.

## APIs

Use a fresh lowercase UUID (32 hexadecimal characters) as run_id and the content
revision from the existing revision endpoint:

```json
{"run_id":"<32-character UUID>","revision":"<64-character hash>"}
```

| Method and route | Behavior |
| --- | --- |
| POST /api/projects/{project_id}/runs | Fast transactional submission, HTTP 202; same run ID/revision is idempotent |
| GET /api/projects/{project_id}/runs | Bounded recent-run list |
| GET /api/projects/{project_id}/runs/{run_id} | Durable status, attempts and validation results |
| GET /api/projects/{project_id}/runs/{run_id}/events | Ordered polling with after sequence and limit |
| POST /api/projects/{project_id}/runs/{run_id}/cancel | Persistent cancellation request |

Stale admission, reused identity with different parameters and project reservation
conflicts return 409. Existing Chat, Projects, Tasks, Notes, validation and legacy
repair endpoints remain. There is no arbitrary folder, command, model, image,
profile, timeout or secret-bearing task field in run admission. The application
retains its existing trusted-local access model; this phase adds no user accounts.

## Events

run_created, run_claimed, baseline_started, baseline_failed, repair_started,
files_changed, repair_unchanged, validation_started, validation_failed,
repair_retry, validation_passed, cancellation_requested, run_completed,
run_cancelled, run_failed, run_interrupted, recovery_confirmed.

These originate at actual state transitions, not synthetic timers. Transactional
row locks and a unique run/sequence constraint preserve event ordering. A crash
can leave a started event without a finished event; recovery reports interruption.
Validation counts describe completed recorded validations, with started checks
also visible in events.

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -q
.venv\Scripts\python.exe tests/check_runs_postgres.py
.venv\Scripts\python.exe tests/check_validation_docker.py
.venv\Scripts\python.exe tests/check_repair_docker.py
.venv\Scripts\python.exe tests/check_repair_process.py
node tests/frontend_projects.test.cjs
```

Results: 78 discovered Python tests, 77 passed and one Windows symlink-creation test
skipped. All 18 real PostgreSQL tests passed in disposable schemas, including
concurrent claims/submissions, persistent cancellation, heartbeat lease loss,
recovery, event ordering and record persistence. All seven Phase 1 Docker tests,
four Phase 2 Docker tests and two Windows synthetic process-tree tests passed.
Installed Hermes construction/repair smoke checks, all four offline Codex
transport checks, frontend regressions and Python compilation passed.

PostgreSQL integration uses mocked Hermes and temporary managed workspaces. Tests
do not insert production projects or runs. The additive production migration was
verified at version 1, with zero queued production runs before dispatcher startup.
No real provider call was made. No generated project scripts were executed.
Live read-only checks confirmed all durable API routes are registered; the existing
page and Projects, Tasks, Notes and conversation-history endpoints returned 200,
as did host health. No validator containers remained after the tests.

## Limitations and Phase 4 proposal

Live provider behavior remains an untested release gate. Restart recovery is
quarantine plus explicit cleanup confirmation, not automatic resume. Graceful
shutdown requests cancellation; abrupt shutdown may leave processes/containers
requiring operator cleanup. Cancellation during validation waits for Phase 1's
bounded cleanup. Database or OS outages cannot guarantee immediate cleanup.
One dispatcher per Windows bridge is supported; job claiming is PostgreSQL-safe
across claimants, but cross-host filesystem replication is not provided. Windows
administrator access remains outside the model-tool threat boundary. Historical
run/checkpoint storage has no global retention quota yet.

Phase 4, only after approval: add a small Projects run monitor using these durable
APIs. Show real stage, final classification, validation errors, attempts, revisions
and cancellation state; use bounded polling and ordered event cursors. Add submit,
cancel and explicit fresh retry controls with stale-revision handling. Render all
diagnostics as inert text. Test reload/reconnect, duplicate clicks, cancellation,
interrupted runs and accessible interaction against mocks. Do not add cloud
deployment or perform provider calls without separate approval. SSE/WebSockets are
not needed for this proposed polling-based phase.
