# Phase 2: bounded static website repair

This phase wraps the unchanged Phase 1 validator and existing Hermes AIAgent
worker. It repairs existing managed UUID projects only. There are no direct model
API calls, new-project redesign, durable jobs, database migrations, live-stage UI,
or cloud integrations. Installed Hermes files and Codex timeouts are unchanged.

## API

1. Read `GET /api/projects/{project_id}/revision`.
2. Choose a fresh UUID in lowercase hexadecimal (32 characters).
3. Send `POST /api/projects/{project_id}/repair` with:

```json
{"revision":"<64-character content hash>","run_id":"<32-character UUID>"}
```

The synchronous response contains classification, model/provider, baseline,
validation results, repair revisions, checkpoint IDs and final revision.
To cancel concurrently, send
`POST /api/projects/{project_id}/repair/{run_id}/cancel`.
Cancellation acknowledges a request, not completed cleanup. A matching active run
is required. Stale initial revisions, concurrent repairs and reused run IDs return
409. The backend checks the existing PostgreSQL project row; host calls require
the existing shared token. There is no user-supplied prompt, command, image,
workspace path, model, profile, or timeout option on this API.

## State machine

Acquire exclusive project lock → capture protected baseline checkpoint → validate.
On trusted pass, finish. On structured validation failure, prepare bounded error
feedback → fresh restricted Hermes process → checkpoint resulting revision →
validate again. Every exit is classified. Hermes claiming completion never
establishes success. An already valid baseline returns repaired_successfully with
reason already_valid and zero repair cycles.

Checkpoints are under ignored `runtime/repairs/<run-id>/<checkpoint>/`, outside
the project tools' reachable workspace and outside the Docker project mount.
Checkpoint 0 preserves the baseline. Each completed/terminated repair is captured
when the workspace still satisfies filesystem policy, including partial edits
after provider failure or cancellation. Checkpoints are not automatically restored:
the final revision may contain unvalidated partial work and is marked unsuccessful.
Malformed/unsafe files are not copied into a trusted checkpoint.

## Exact limits and stop conditions

- Maximum four validations including baseline, three repairs.
- Total run budget 600 seconds, measured with a monotonic clock.
- Each repair subprocess gets at most 120 seconds, reduced for remaining run time.
- Each fresh Hermes instance has eight conversation iterations, at most 24
  guarded API attempts and at most 24 dispatched tool calls. No fallback model.
  Exhausted explicit attempt guards return unresolved_validation_errors.
- Reserve 90 seconds before starting validation and after allocating a repair
  timeout for the existing validator's execution/control/cleanup bounds. If this
  reserve is unavailable, stop as deadline_exceeded without starting more work.
- Two consecutive identical content revisions after repairs stop as no_progress.
- Repeated error fingerprints after changed revisions also stop as no_progress,
  including oscillations. Fingerprints retain file/check/code and multiplicity;
  line/column/message changes alone do not count as progress. This is deliberately
  conservative. Only the bounded feedback set is compared.
- Stop on security violations, untracked/stale revisions, cancellation, provider
  failure, validator infrastructure failure, deadline, or exhausted repair budget.
- Success requires the trusted static-web-v1 validator to pass the matching final
  revision with no errors, confirmed cleanup, and a fresh live revision check.

Classifications: repaired_successfully, unresolved_validation_errors,
provider_failure, validation_infrastructure_failure, security_violation,
cancelled, deadline_exceeded, no_progress. Initial admission conflicts are HTTP 409.

## Cancellation, cleanup and concurrency

The Windows process supervisor polls cancellation at 200 ms intervals and uses
taskkill on the worker process tree, then waits for termination. Killing only the
launcher would be unsafe. Unconfirmed termination preserves the project lock and
fails closed, preventing another modifying repair until operator recovery.

An in-flight Phase 1 validation is allowed to complete its already bounded cleanup
before the repair returns cancelled; the supervisor is not killed and no next
repair starts. The validator's 20-second execution and separate bounded Docker
control calls remain unchanged. Cancellation is therefore not instantaneous during
validation. OS/filesystem/Docker failure may delay cleanup; an unavailable daemon
cannot be promised successful cleanup. Deadline checks reserve cleanup time, and
an expired run never claims success.

An in-process registry plus exclusive project lock files prevents overlapping
modifying repairs. Two repairs across different projects are allowed by the host.
This phase uses one host service process; cancellation is process-local. Locks
left by crashes fail closed and require operator review. No automatic stale-lock
deletion or restart recovery is implemented.

## Security

Hermes still has only project_read_file, project_write_file and project_list_files.
Repair mode treats forbidden tools, malformed arguments and filesystem-policy
failures as terminal decisions rather than retryable tool messages. It checks
revisions at tool boundaries. Writes use a same-directory temporary file, flush,
fsync and atomic replacement, retaining existing path/link/size protections.
There is no shell command tool and generated code is never executed.

Feedback copies only file, line, column, check, code and message from at most 30
errors. String fields are limited to 200 characters and locations are bounded.
The prompt labels diagnostics and source contents as untrusted data. Tool authority
is enforced in code independently of these instructions. Raw provider exception
text is never returned. The Phase 1 network-disabled, non-root, read-only Docker
profile, resource limits and fixed command are unchanged.

This remains a trusted-local application, not an OS sandbox for a hostile Windows
user. A local administrator can modify workspaces or runtime files outside these
APIs. Revision checks detect observed concurrent changes; they are not filesystem
locking against arbitrary external editors. Checkpoint protection is separation
from model tools, not a new Windows ACL scheme.

## Files

Created:
- app/repair_contracts.py
- hermes_repair.py
- tests/test_repair.py
- tests/check_repair_worker.py
- tests/check_repair_docker.py
- tests/check_repair_process.py
- docs/BOUNDED_REPAIR_PHASE2.md

Modified:
- app/project_workspace.py — atomic writes.
- app/routers/projects.py — existing-project repair and cancellation APIs.
- hermes_service.py — authenticated host endpoints and repair capacity.
- hermes_projects.py — cancellable worker wait; existing creation call unchanged.
- hermes_project_worker.py — repair-only budgets, policy stops and revision checks.
- README.md — link to this guide.

## Tests and observed traces

Run offline tests:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests/frontend_projects.test.cjs
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/check_hermes_worker.py
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/check_repair_worker.py
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/diagnose_codex_path.py
.venv\Scripts\python.exe tests/check_validation_docker.py
.venv\Scripts\python.exe tests/check_repair_docker.py
.venv\Scripts\python.exe tests/check_repair_process.py
```

Mocked unit tests cover repair success, multiple errors, newly introduced errors,
valid baselines, both no-progress conditions, oscillation, limits, stale revisions,
concurrency, cancellation, deadlines, provider/infrastructure/security failures,
strict final success, API authentication and atomic-write failure. Existing Chat,
Projects, Tasks, Notes, Ollama and Phase 1 regression tests remain in the suite.
Installed Hermes tests block networking and mock provider resolution/model turns.

Final results (September 7, 2026): 61 discovered Python tests, 60 passed and one
Windows symlink-creation test skipped because the OS does not permit creation.
Independent hardlink/reparse checks passed. All seven Phase 1 Docker tests, four
Phase 2 Docker tests, and two Windows process-tree tests passed. Both installed
Hermes smoke checks, all four Codex transport/schema checks, frontend regression
harness and Python compilation passed. After reloading the local services, the
page and Projects/Tasks/Notes/conversation-history endpoints returned HTTP 200;
repair and cancellation routes were registered, host health returned 200, and
unauthenticated repair returned 401. No validator containers remained. No real
provider request or production test-project insertion was performed.

Real Docker validation around mocked Hermes produced:

```json
{"status":"repaired_successfully","validation_passes":[false,true],"repairs":1,"checkpoints":2}
```

```json
{"status":"no_progress","reason":"two_unchanged_repairs","validation_passes":[false,false,false],"repairs":2}
```

The Docker integration also repairs multiple errors over three cycles, including
a newly introduced error, and verifies cancellation leaves no validator containers
or snapshots. Separate trusted synthetic Windows processes verify real tree
termination for cancellation and deadlines. No generated project code is executed.

## Limitations and Phase 3 proposal (not implemented)

Live provider behavior remains a release gate requiring separate authorization.
Offline compatibility is not proof that the current Codex watchdog issue is fixed.
Static validation retains all Phase 1 limitations. There is no repair UI, durable
result retrieval, automatic rollback, crash recovery, or checkpoint retention job.
Synchronous callers should retain the returned trace; disconnected calls can keep
running until their deadline. Checkpoints consume up to four bounded project copies
per run and remain on disk for operator review.

Phase 3, only after approval:
1. Add explicit PostgreSQL run/attempt records with baseline/final revisions,
   sanitized validation results, model, stop reason and checkpoint references.
2. Add idempotent run admission and a bounded durable dispatcher using project
   leases, preserving exactly the Phase 2 limits and trusted validator.
3. Persist cancellation and safe interrupted/recovery states; never resume unknown
   worker activity or delete a lock before confirming process/container cleanup.
4. Expose run status/result retrieval without redesigning the frontend.
5. Define bounded checkpoint/result retention and operator recovery procedures.
6. Test duplicate submission, service restart, lease loss, cancellation, cleanup
   failure and stale revisions entirely offline first. Any provider release test
   remains separately approved. UI redesign and cloud deployment remain later work.
