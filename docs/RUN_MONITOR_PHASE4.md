# Phase 4 — Projects autonomous-run monitor

Implemented September 8, 2026. No real provider calls, cloud/GitHub integration,
generated-code execution, installed Hermes modifications or Codex timeout changes.
Phase 1 validation, Phase 2 repair limits and Phase 3 lease/recovery guarantees remain.

## Files

Created: app/static/project-runs.js; app/migrations/v002_changed_files.py;
tests/frontend_runs.test.cjs; tests/test_run_monitor_metadata.py;
tests/check_run_monitor_browser.py; this guide; docs/screenshots/phase4-completed.png
and docs/screenshots/phase4-provider-failure.png.

Modified: app/static/projects.js, app/static/projects.css, app/static/index.html,
app/run_models.py, app/run_store.py, app/migrations/__init__.py, hermes_repair.py,
tests/test_runs.py and README.md.

## Workflow and monitor

Open an existing project and choose Validate, Fix Project or View Runs. Validate
checks its current revision through the unchanged validator API. Fix Project reads
the current revision, creates a fresh UUID and submits the durable run. The monitor
opens immediately after acceptance. View Runs reconstructs any selected run from
the database. New-project generation is unchanged.

The monitor shows run identity, recorded status/stage, model/provider when an
attempt exists, start time, elapsed time, repair/validation counts, final result,
ordered events, validation details and changed files. Elapsed time is computed
from persisted timestamps; it is not a simulated stage. Provider failures have
distinct wording and an amber border; successful repairs have a green border.
The URL retains project/run IDs so browser refresh reconstructs the same run.

## Polling and submission safety

Active runs poll status and events every two seconds without overlapping scheduled
polls. Each ordinary request has a ten-second client timeout. Events use an ordered
sequence cursor, at most 100 per request, with at most 200 retained in the view.
Terminal runs stop polling after recorded events are retrieved. Network failures
back off to at most 15 seconds; five failures pause monitoring with a Refresh Run
control. A paused browser monitor does not imply that backend work stopped.

Submission controls lock before asynchronous work. A pending UUID/revision is
retained in session storage so a lost response can be reconciled without silently
creating a second run. PostgreSQL reservations remain the cross-tab authority.
UUID generation supports local HTTP origins using cryptographic getRandomValues
when randomUUID is unavailable. No permission or command comes from browser storage.

## Cancellation and retry

Cancel Run calls the persistent cancellation endpoint and disables repeat clicks.
The UI shows Cancelling while awaiting backend confirmation, and shows Cancelled
only when returned by the backend. Polling continues while cancellation is pending.
Retry as New Run is available for failed/interrupted runs, checks the current
revision and submits a new UUID. It never resumes the prior provider operation.
Reserved/interrupted projects may still require Phase 3 operator recovery; 409
responses explain revision/reservation conflicts without bypassing protection.

## Errors and changed files

Structured diagnostics display file, line, column, check, code and safe message.
All content is assigned through textContent, including hostile HTML/script fixtures.
Changed-file buttons use the existing safe text viewer and display current file
contents, not historical diffs or executable previews.

Additive migration 2 introduces changed_files JSON on project_model_attempts.
Existing rows receive an empty list. The repair observer derives file names from
actual before/after content and the store checks managed paths and the 100-file
bound. No file contents or raw provider responses are added to the database.
Older attempts cannot retroactively reconstruct their file lists and say that no
changed files were recorded.

## Security

No terminal, command input, arbitrary folder, secret/configuration display, preview
iframe or generated script execution was added. The UI selects only known public
run fields and does not render lease internals or host paths. Network errors use
fixed user-facing messages instead of raw response bodies. Backend validation and
exclusive durable reservations remain authoritative.

## Tests and offline demo

```powershell
node tests/frontend_runs.test.cjs
node tests/frontend_projects.test.cjs
.venv\Scripts\python.exe -m unittest discover -s tests -q
.venv\Scripts\python.exe tests/check_runs_postgres.py
.venv\Scripts\python.exe tests/check_run_monitor_browser.py
```

The DOM harness covers 15 scenarios: starting, duplicate clicks, ordered events,
safe diagnostics, changed-file viewing, cancellation request/confirmation, fresh
retry, distinct terminal outcomes, reload reconstruction, polling recovery/pause,
lost submission response, standalone validation and run history.

The Edge browser test intercepts every page/API request. It verifies safe rendering,
the existing viewer, reload, provider-failure display, fresh retry and cancellation
confirmation. Screenshots are visibly labelled offline fixtures, not evidence of
a live provider run. Playwright is a development-only virtual-environment dependency;
it was not added to application requirements. Installed Microsoft Edge is used.

Open the screenshots for an offline visual demo:
- [Completed run](screenshots/phase4-completed.png)
- [Provider failure](screenshots/phase4-provider-failure.png)

The Python suite has 79 tests (78 passing, one Windows symlink-creation skip).
PostgreSQL integration has 18 tests, including changed-file persistence and the
unchanged lease, cancellation and recovery cases. Existing Chat, Tasks, Notes and
Projects regressions remain in the suites. Browser and unit tests never submit
production jobs or call model providers.

Final verification also passed all 11 Phase 1/2 Docker tests and both Windows
process-tree cleanup tests. The resumed checks initially found Docker stopped;
after restoring it, the PostgreSQL and Docker reruns passed. Migration versions
1 and 2 are applied, production run count is zero, the new UI asset and existing
app endpoints return 200, host health returns 200, and no validator containers
remain. The installed-Hermes repair smoke test passed with networking blocked.

## Limitations and Phase 5 recommendation

No live Codex/provider acceptance test has been performed. Historical file content
diffs are not available; changed-file links open the current managed file. Five
network failures require manual Refresh Run. Interrupted runs can require operator
recovery before retry. Session-storage restrictions reduce lost-response recovery
across browser reloads, while database reservations still prevent concurrent runs.

Recommend Phase 5 as a separately approved live-provider acceptance gate before
cloud work: prepare a disposable tiny managed static website and protected baseline;
authorize one bounded durable repair run through Hermes; inspect real events,
attempts, validated final revision and cleanup; stop and diagnose any watchdog or
provider failure without raising timeouts or changing permissions. Keep the current
limits, never execute generated code, and record only safe diagnostics. Cloud and
GitHub integration should remain a later separately approved phase.

Phase 5 has not started.
