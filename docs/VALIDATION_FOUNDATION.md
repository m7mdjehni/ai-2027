# Safe Validation Foundation — Phase 1

Implemented September 7, 2026. This is the autonomous-agent validation foundation,
not the older Project Builder milestone documented in PHASE1_REPORT.md.

## Architecture and use

The backend accepts only an existing project ID and expected content revision.
The authenticated Windows bridge copies validated project bytes into a temporary
snapshot. A fixed, locally built Docker image parses that snapshot and returns
structured errors. The supervisor validates the response, removes the container
and snapshot, and checks whether the live project revision changed. A stale
snapshot cannot claim that the current project passed.

Build the trusted image once (dependency downloads occur only during setup):

```powershell
docker build -f validation/Dockerfile.web -t hermes-validator-web:phase1 .
```

Run the existing `hermes_service.py` bridge and backend. Obtain the revision with
`GET /api/projects/{project_id}/revision`, then send
`POST /api/projects/{project_id}/validate` with `{"revision":"<64-character hash>"}`.
Validation does not generate files, call Hermes, or update project history/status.
There is no validation UI or automatic repair loop in this phase.

The profile checks index.html presence, HTML parser errors, CSS syntax,
JavaScript syntax (including inline scripts), and supported local asset references.
Acorn, parse5, and css-tree versions are locked inside the image. The supervisor
resolves the fixed image tag to its immutable local image ID before execution;
it never pulls an image during validation.

## Security controls

- Fixed argv with `shell=False`; requests cannot choose commands, images, paths,
  profiles, flags, or deadlines. No package scripts or project configuration run.
- Only a copied project snapshot is mounted, read-only at /project. The container
  root filesystem is also read-only. No Docker socket, repository, credentials,
  environment file, or host home is mounted.
- Network none; non-root UID/GID 10001; all capabilities dropped;
  no-new-privileges; 256 MiB memory/swap ceiling, one CPU, 32 PIDs, 128 file descriptors.
- Existing UUID workspace, traversal, link/reparse-point, text-file and size checks
  remain. Limits: 100 files, 512 KiB each, 5 MiB total, 240-character paths.
- Validation execution deadline 20 seconds; Docker control calls separately
  bounded at 10 seconds. Captured output limited to 64 KiB; at most 100 errors.
  These are new validator limits, not changes to Codex timeouts.
- Container removal runs after success, failure, output overflow, and timeout.
  Unconfirmed cleanup fails closed. If Docker itself becomes unavailable, manual
  recovery may be needed; affected containers carry label hermes.validation=phase1.
- Backend-to-host requests retain shared-token authentication. This is not new
  user authentication: the existing application remains a trusted-local demo.
- Worker diagnostic logging uses allowlisted categories and counters, not raw
  provider text, prompts, tokens, headers, or OAuth data. Stream/reasoning counters
  and the computed watchdog value add observation without changing transport.

## Files created

- app/validation_contracts.py
- hermes_validation.py
- hermes_diagnostics.py
- validation/profiles.py
- validation/Dockerfile.web
- validation/package.json
- validation/package-lock.json
- validation/check_web.mjs
- tests/test_validation.py
- tests/check_validation_docker.py
- tests/fixtures/static_valid/index.html
- tests/fixtures/static_valid/style.css
- tests/fixtures/static_valid/script.js
- tests/fixtures/static_broken/script.js
- docs/VALIDATION_FOUNDATION.md

## Files modified

- app/project_workspace.py — content revision and bounded snapshot capture.
- app/routers/projects.py — revision and validation endpoints.
- hermes_service.py — authenticated validation endpoint and separate capacity limit.
- hermes_project_worker.py — safe observational telemetry; same restricted tools.
- hermes_projects.py — allowlisted worker diagnostics instead of raw responses.
- tests/check_hermes_worker.py — offline telemetry/installed-runtime assertions.
- .gitignore and .dockerignore — exclude local validator dependencies.
- README.md — link to this implementation and operating guide.

No schema migrations, frontend changes, installed Hermes modifications, provider
calls, or timeout increases were made. Runtime service logs/PID are ignored files.

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests/frontend_projects.test.cjs
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/check_hermes_worker.py
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/diagnose_codex_path.py
.venv\Scripts\python.exe tests/check_validation_docker.py
```

Results: 33 discovered Python tests, 32 passed, one skipped because Windows did not
permit creating a symlink. Separate reparse detection and hardlink checks passed.
Frontend harness passed. Installed Hermes smoke and all four offline Codex
transport/schema checks passed. Seven real local Docker fixture tests passed.
Those tests verified blocked network connections, read-only mounts/root, non-root
identity, absent credentials/socket, inert generated scripts/configuration,
structured syntax failures, valid-site success, timeout, output cap, and cleanup.
Test-only sandbox probes use trusted fixed code; no API exposes their commands.

Existing Chat, Projects, Tasks, Notes, and Ollama paths passed mocked regression
tests. Live read-only checks of the page and Projects, Tasks, Notes, and conversation
history endpoints all returned HTTP 200. Live model generation was deliberately
not tested. An expected metadata-fetch message in the Hermes smoke test confirms
that its network interception blocked the attempted metadata lookup.

Example actual broken-fixture result (identity/hash/timing fields omitted):

```json
{
  "schema_version": 1,
  "profile": "static-web-v1",
  "passed": false,
  "status": "failed",
  "files_checked": 3,
  "errors": [{
    "check": "javascript-syntax",
    "code": "syntax_error",
    "file": "script.js",
    "line": 2,
    "column": 16,
    "message": "JavaScript syntax is invalid."
  }],
  "warnings": [],
  "snapshot_passed": false,
  "cleanup_confirmed": true
}
```

## Limitations and next phase

Passing means the listed static checks passed; it does not prove visual quality,
browser behavior, accessibility, full HTML conformance, or CSS property semantics.
Project JavaScript is never executed. External assets are not fetched. Event
handler attributes, srcset, and some module resolution cases remain unchecked and
may produce warnings. JSX, TypeScript, build systems and binary assets are outside
this profile. Docker Desktop and the prebuilt validator image must be available.
Validation results are returned to the caller, not yet persisted as agent runs.
The existing live Codex watchdog issue remains unverified without provider calls.

Phase 2, only after approval: add a bounded repair workflow for managed static
projects using this same validator. Capture a protected baseline/revision; validate;
pass structured errors to Hermes through restricted project tools; accept targeted
edits; revalidate. Limit to four validation cycles and three repair attempts, with
explicit cancellation/deadlines and stop conditions for success, no changes,
repeated failures, stale revision, infrastructure errors, or exhausted budget.
Preserve checkpoints and never give Hermes shell access or authority over validator
commands. First verify orchestration with mocked Hermes success, repair, failure,
cancellation, and stale-project cases. Do not add durable run queues, new history
schemas, live-stage UI, or cloud deployment in Phase 2. Provider testing still
requires separate authorization under the current restriction.
