# Durable standalone validation

The standalone validation endpoint previously checked only the revision and
boolean result from the trusted host, then returned that response. It never
created the `ProjectRun` / `RunValidation` evidence required by the existing
publication gate. The UI could therefore show a pass while preview correctly
returned `validation_required`.

## Persistence design

`POST /api/projects/{project_id}/validate` still invokes only the authenticated
Hermes validation service, with the fixed existing request contract. It now:

1. Checks the requested revision against the selected workspace before delivery.
2. Rejects redirects, non-200 responses, wrong project/revision/profile, malformed
   status/boolean fields and missing cleanup confirmation. Host responses must
   conform to `static-web-v1`; a pass also requires matching `current_revision`.
3. Rechecks the local revision after the host returns.
4. Atomically commits one terminal `ProjectRun`, one `RunValidation` (cycle 1),
   and two timeline events, then returns the host result plus `run_id`.

The run has `operation="validation"`, task `Validate managed static website`,
zero repairs, one validation, no model attempts, no lease, and no active-project
reservation. It never enters the autonomous repair queue. It is `completed` only
for a trusted pass with confirmed cleanup; otherwise a valid failed response is
recorded as `failed`. Profile, pass/status, revision and cleanup confirmation are
taken from the checked host response, not filled with success defaults. False
cleanup remains false. Missing cleanup is an invalid response, not a clean pass.
Structured diagnostics are bounded and stored without free-form error messages.

Each actual validation invocation receives a new server-generated run ID.
Repeated or concurrent invocations append independent observations and never
overwrite repair history, validation cycles or earlier evidence. A persistence
failure rolls back the whole record and returns an error, never a saved-success
response. If the response is lost after commit, a retry can append another valid
observation; both remain visible in history.

The existing `publication_service.gate()` is unchanged. Each accepted certificate
remains bound to its exact content revision. A later mutation yields a different
revision and needs matching evidence. Existing active-repair reservations,
trusted export checks, preview/confirmation separation and GitHub restrictions
remain authoritative. An eligibility read is advisory; preview and confirmation
still execute their existing gates and trusted export checks.

No tables, columns or migrations changed. The existing operation/task/status
fields already support this distinct terminal run. Existing repair admission,
leases, observer, finish logic and historical rows remain unchanged. No old UI
passes are backfilled: obtaining new durable evidence requires a new trusted
validation.

## UI behavior

Before: Validate rendered only its immediate result, without refreshing the
publication panel. View Runs contained no record of that operation.

After: Validate renders the committed result, offers **View validation run**,
refreshes run history and reads `/publications/eligibility`. That endpoint checks
the current revision with the unchanged backend gate. A refresh clears old
preview/confirmation state, and stale in-flight previews cannot restore it.
There is also an explicit **Refresh eligibility** button. No validation action
automatically previews, confirms or publishes anything.

Run history labels standalone operations as Validation, shows zero repairs and
one validation, and does not offer a failed validation as a repair retry. Failed
and unconfirmed-cleanup results are not rendered as clean validation passes.

## Regression coverage

`tests/test_standalone_validation.py` covers pass -> fresh-session durable read ->
preview, failure, changed revision, missing/false cleanup, wrong profile/project/
revision, strict response types, repeated validations alongside an existing
successful repair, active repair reservations, authentication/redirect/timeout
failures, mutation during validation, and atomic rollback on commit failure.

The PostgreSQL suite reuses these cases and adds simultaneous append transactions.
The Docker suite adds real sandbox validation output -> authenticated-response
fixture -> persistence -> preview only. Frontend tests cover the backend refresh,
cleared preview state, component wiring, saved-run link and standalone history.

## One local acceptance test (preview only)

Prerequisites: updated backend/frontend loaded, PostgreSQL and Docker available,
validator image installed, trusted Hermes host validation service authenticated,
and an existing ready/completed static project with no active repair. No model
or GitHub credentials are needed for this preview exercise. Live publication can
remain disabled.

1. Open `http://localhost:8000`, select that existing project, and open browser
   developer tools' Network tab. Do not choose Build or Fix Project.
2. Click **Validate** exactly once. Expect one POST to
   `/api/projects/<id>/validate`, a 200 response with `run_id`, `passed: true`,
   `status: "passed"`, `profile: "static-web-v1"`, `cleanup_confirmed: true`, and
   the same requested/current revision. Stop if any check fails.
3. Click **View validation run**. Confirm task `Validate managed static website`,
   completed status, zero repairs, one validation, matching revision and confirmed
   cleanup. Reload/View Runs if desired to verify it is durable.
4. Check the automatic GET `/api/projects/<id>/publications/eligibility` returns
   `eligible: true` for that same revision. Do not alter workspace files.
5. Enter `example` as the GitHub account name and click **Preview publication**
   exactly once. Expect a preview response with the same revision and actual file
   list. This calls only the local trusted export service, not GitHub.
6. **Stop with the preview visible. Do not click Confirm public publication.**
   Network history must contain no `/confirm`, `/reconcile` or `/actions-migrate`
   POST. The preview record is allowed to remain unconfirmed in local history.

## Validation status on 2026-09-09

Python discovery: 151 tests, one existing Windows symlink-privilege skip.
All three frontend test files (including 17 run-monitor scenarios), seven
installed-Hermes offline scripts, intercepted Edge browser checks and two
process-tree cleanup tests passed. No live provider or GitHub calls were made.

Full infrastructure sign-off is pending. The 12 Docker integration tests were
attempted outside the sandbox and failed because the Docker Linux engine pipe
was unavailable (`dockerDesktopLinuxEngine`; validator reports
`validator_image_missing`). The 52 PostgreSQL integration tests were attempted
outside the sandbox and failed to connect to `127.0.0.1:5432` (connection refused).
Restore those services and rerun these suites before claiming complete
integration validation. The historical live acceptance monitor was excluded
because it reads a live API.
