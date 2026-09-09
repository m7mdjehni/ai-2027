# Project Builder Codex watchdog

The application-owned `hermes_project_watchdog.py` applies only through
`ProjectToolBoundary` when provider is `openai-codex` and API mode is
`codex_responses`. It covers Project Builder creation and repair requests.
Installed Hermes files, normal CLI behavior, provider configuration, model
selection, parser, request tool policy and dispatch authorization are unchanged.

The initial request budget still comes from Hermes (90 seconds in the regression).
Before any successful tool dispatch, that budget terminates requests without
recent structured progress.
Only nonempty `response.function_call_arguments.delta` events associated with an
announced, still-pending, allowlisted project function qualify as structured
progress. Empty/unassociated deltas and text/keepalive events cannot extend the
initial budget. If argument progress stops, its extension expires after the
inactivity interval even if keepalives continue.

After a successful allowlisted tool dispatch, subsequent requests enter
`post_tool_continuation`. They use fresh first-event, inactivity and absolute
deadlines, but no longer apply the generic initial 90-second cutoff. This lets
the model prepare its next tool call while sending valid non-argument events.
Failed/denied tools cannot enable this phase. Each request still starts with no
observed event or argument progress; previous progress timestamps are never reused.

The previous integration unconditionally started every request in initial mode.
Hermes's conversation loop increments `_api_call_count` outside the retry loop,
dispatches completed tools through `_execute_tool_calls`, then requests another
model turn. The integration did not carry successful-tool state into that turn.
Cumulative Codex counters could therefore show a completed function while the
latest request had no structured progress and expired at 90 seconds. Those
counters alone do not establish the event history or retry count of the failed
live request. The fix carries only the trusted lifecycle transition, not stream
progress or provider-supplied state.

First-event protection remains enabled (120 seconds by default; the initial
budget can terminate earlier). Stream inactivity remains enabled (the existing
Project Builder setting defaults to 60 seconds). Invalid, infinite or disabled
values cannot disable these protections in this integration. No settings are
written by the new watchdog.

Every request has a fixed **300-second absolute deadline**, measured from before
client creation with a monotonic clock. Activity never resets it. Five minutes
gives structured file generation more than three times the former 90-second
budget while staying well below the existing 900-second default worker-process
limit. The policy is checked every 0.3 seconds, with at most two seconds of abort
join grace afterwards (plus ordinary thread scheduling overhead). This is a
request acceptance deadline, not a guarantee that Python can forcibly stop a
blocked thread; the existing process supervisor remains the final cleanup bound.

Each request gets a client, cancellation state and observer context of its own.
Timeout/interruption aborts the worker-owned transport; the worker owns close.
The timeout decision wins over a late response or an abort-induced transport
error. A cancelled stream cannot update a retry's watchdog or dispatch its late
result. Hermes's existing bounded conversation retries and worker process limit
are unchanged. The five-attempt historical test is a synthetic retry fixture;
the actual installed conversation regression currently performs three attempts.

Diagnostics expose only allowlisted scalar values for the most recent request:

- `initial_watchdog_seconds`
- `active_stream_watchdog_mode`: `initial_request`, `post_tool_continuation` or
  `structured_argument_progress`
- `watchdog_turn_phase`: `initial_request` or `post_tool_continuation`, independent
  of whether the current turn has started streaming arguments
- `model_turn_number`: Hermes's model turn counter (retries retain their turn)
- `watchdog_request_number`: request attempt counter, including retries
- `prior_tool_call_dispatched`, `prior_successful_tool_call`: state at request start
- `total_build_elapsed_seconds`: elapsed build time at the watchdog snapshot
- `hard_absolute_deadline_seconds`: 300, relative to request start
- `watchdog_elapsed_seconds`: monotonic elapsed time, captured before abort grace
- `watchdog_termination_reason`: pending, completed, provider_error, interrupted,
  time_to_first_event, stream_inactivity, initial_request_watchdog or
  hard_absolute_deadline
- `structured_argument_progress_observed`

No arguments, event payloads or credentials are added to diagnostics. Complete
function arguments still pass through the installed parser and the existing
restricted dispatch and workspace checks.

Offline verification:

- `tests/check_hermes_watchdog_stream.py` is retained unchanged: installed generic
  watchdog branch at 90 seconds with a deterministic fake worker.
- `tests/test_project_watchdog.py` checks progress qualification, expired progress,
  first-event/inactivity/absolute limits, retry isolation, scope and redaction.
- `tests/check_codex_watchdog_retries.py` uses installed Hermes and its parser with
  OpenAI SDK mock HTTP SSE. Virtual ticks use event acknowledgments, not polling
  sleeps or elapsed real-time joins. It proves complete dispatch at 120 seconds,
  inactivity termination at 60 seconds, first-event termination at 120 seconds,
  and three actual conversation attempts terminated at 300 seconds despite
  continuous argument progress. All timeout cases have zero dispatches/files;
  the success case writes only after complete arguments and response completion.
- `tests/check_codex_watchdog_continuation.py` runs the actual installed Hermes
  conversation loop and parser with mocked HTTP. It writes index.html, returns
  that tool result in the next request, then writes styles.css at 130 seconds
  (argument progress starts at 100 seconds) and script.js at 120 seconds, before
  a final text response. Separate continuations test 60-second inactivity,
  120-second first-event protection, 300-second endlessly active streams, and
  complete-looking responses delivered after cancellation. All failed
  continuations retain only the already-completed index.html; no partial or late
  file is dispatched. The three retries remain model turn 2, requests 2 through 4.

No live provider request is part of these checks.

Validation on 2026-09-09: Python discovery ran 141 tests successfully (one Windows
symlink privilege skip), including path security and watchdog policy tests. All
three frontend test files, seven installed-Hermes offline scripts, 11 Docker
integration tests, 41 PostgreSQL integration tests and both process-tree cleanup
tests passed. The intercepted Edge browser regression also passed; its existing
fixture screenshots were preserved. Docker, PostgreSQL and process cleanup ran outside the sandbox.
The historical live acceptance monitor is excluded because it reads a live API.
