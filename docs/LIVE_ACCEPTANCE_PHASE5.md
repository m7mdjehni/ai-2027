# Phase 5 — live provider acceptance: PASS

Executed September 8, 2026. Exactly one durable run was submitted through
`POST /api/projects/{project_id}/runs`, the production path used by Fix Project.
There was no second submission, unrestricted CLI, model switch, timeout increase,
installed Hermes modification, cloud integration or generated-code execution.

Project: `70bdf99fa96e485da131d8d9ad2b9509`

Run: `05ec40fcee7843299f5814b4fadf0a13`

[Open the actual persisted monitor](http://localhost:8000/static/index.html#project=70bdf99fa96e485da131d8d9ad2b9509&run=05ec40fcee7843299f5814b4fadf0a13)

[Actual live-run screenshot](screenshots/phase5-live-acceptance.png)

## Pre-test checks

Backend and Windows host health: HTTP 200. PostgreSQL available; migrations 1 and 2
applied. Docker Desktop 29.6.2 available, with the existing local static-web-v1
validator image. No active project reservation existed before admission.

The disposable UUID workspace contains only index.html and script.js. Independent
pre-test validation found exactly one intended JavaScript syntax error in script.js,
line 3, column 1. The validator returned failed with cleanup confirmed.

The selected primary was gpt-5.6-sol / openai-codex with no fallback. Existing limits
were retained: four validations, three repairs, 600-second run budget, 120-second
repair subprocess cap, 20-second validator execution cap, eight Hermes iterations,
24 guarded API attempts and 24 tool calls per repair. Existing timeout helper source
hashes matched before/after; no environment variable values were recorded.

## Actual persisted events

All times below are UTC on September 8, 2026. The browser displays local time.

| Sequence | Timestamp | Event | Stage |
| --- | --- | --- | --- |
| 1 | 03:42:29.700523 | run_created | queued |
| 2 | 03:42:30.616698 | run_claimed | claimed |
| 3 | 03:42:30.708979 | baseline_started | baseline_validation |
| 4 | 03:42:32.353646 | baseline_failed | baseline_validation |
| 5 | 03:42:32.395343 | repair_started | repairing |
| 6 | 03:43:06.640501 | files_changed | repairing |
| 7 | 03:43:06.676478 | validation_started | validating |
| 8 | 03:43:08.622799 | validation_passed | validating |
| 9 | 03:43:08.667319 | run_completed | completed |

## Outcome

- Real Hermes provider activity occurred through codex_responses, using
  gpt-5.6-sol / openai-codex. Stream activity and restricted tool invocation were
  observed, not inferred from a model's final text.
- One persisted model attempt / repair cycle completed (34,219 ms including worker
  overhead). No safe provider failure category was recorded.
- Two durable validations: the baseline failed, then the final revision passed.
  The separate pre-test validation is not included in this durable count.
- Only script.js changed. index.html is unchanged. Both remain inside the original
  managed UUID workspace; actual files and baseline checkpoint were compared.
- Final validator: static-web-v1, passed=true, status=passed, errors=[],
  cleanup_confirmed=true, duration=1,891 ms.
- Final run: completed / repaired_successfully, cleanup_confirmed=true.
- Run duration from started_at to finished_at: approximately 38.05 seconds.

Baseline revision:
`8bef197fca38c35f75b35993b004856d056ffbfc607dcaf1dad4e875fa337d7a`

Validated final revision:
`8e45e3c945f29f52a2b8768d591cc753e61fb6eba56de9a77a109ed86966c105`

## Safe diagnostics

| Field | Recorded value |
| --- | --- |
| API mode | codex_responses |
| Recorded watchdog | 90 seconds |
| Fresh worker session | true |
| Workspace matches request | true |
| Write-file invoked | true |
| Completed | true |
| Guarded API-path entries | 10 |
| Tool calls | 5 |
| Stream activity events | 100 |
| First event | 6,062 ms |
| Last event | 28,750 ms |
| Agent elapsed | 28,781 ms |
| Text characters | 119 |
| Reasoning characters | 43 |

The API counter counts guarded Hermes wrapper entries; it is not asserted to equal
distinct HTTP requests. The durable model-attempt count is one. Watchdog telemetry
records the value returned by the existing Hermes computation, without changing it.
No raw model text, reasoning text, provider payload, OAuth data, credentials,
cookies, headers or environment values were recorded in the acceptance artifacts.

## Cleanup and UI verification

Zero Hermes worker processes and zero validator containers remained. The durable
active-project reservation, worker ownership and lease expiry were cleared.
Baseline/final checkpoints, the disposable project and run history are preserved.
Exactly one durable run exists for this fixture. Final revision matches the current
workspace and the persisted passing validation.

Headless Edge opened the existing Phase 4 monitor against real localhost backend
responses. No mock data was substituted; only GET requests were permitted. Run ID,
model/provider, timeline, validation records, changed file and successful final
result were verified. Reload reconstructed the same persisted run. Nothing was
published or deployed.

## Implementation and evidence

Added tests/live_provider_acceptance.py with separate prepare, submit, observe and
finalize actions. An exclusive submission.once marker is written before the single
POST; uncertain responses are never automatically resubmitted. Added
tests/check_live_acceptance_monitor.py and tests/test_acceptance_diagnostics.py.

Updated hermes_diagnostics.py, hermes_project_worker.py and hermes_repair.py only
to preserve allowlisted scalar diagnostic snapshots outside model-readable project
workspaces. Snapshots are atomic, throttled during streaming and do not change
tools, transport selection or limits. The diagnostic sanitizer test and installed
Hermes repair smoke test passed offline before the live call.

Ignored local evidence:
- runtime/phase5/acceptance.json — safe prechecks, persisted run/events and cleanup.
- runtime/phase5/submission.once — prevents a second acceptance submission.
- runtime/phase5/ui-verification.json — read-only real UI verification.
- runtime/repair_diagnostics/70bdf99fa96e485da131d8d9ad2b9509.json — scalar diagnostics.

Do not rerun submit or create another live acceptance test under this authorization.
Observe and finalize are read-only checks of the preserved run.

## Recommendation

PASS demonstrates this small end-to-end real repair, not general reliability for
larger workloads. No timeout or architecture fix is indicated by this result.

Recommend Phase 6: explicitly approved GitHub/static cloud publication for one
selected, validated revision. Keep GitHub credentials outside Hermes; export only
approved project files; exclude runtime/checkpoints/secrets; require review of the
repository, visibility and publication target; use a trusted static Pages setup
without generated build commands. Record the published revision and URL. Preserve
the local repair/validator boundaries. Do not auto-publish repaired projects.

Phase 6 has not started.
