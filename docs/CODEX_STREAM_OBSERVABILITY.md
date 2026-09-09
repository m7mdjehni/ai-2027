# Restricted Codex stream diagnostics

This patch observes the existing installed Hermes parser. It does not change parsing, tools, provider selection, retries or timeout values. No live provider requests were made.

## Capture points

`ProjectToolBoundary._run_codex_stream()` supplies a worker-local client proxy to the unchanged Hermes runtime. The proxy intercepts `responses.create()` **after** Hermes preflight, Relay transformations, consumer sanitization and SDK-transform bypass. It checks the effective top-level/`extra_body` policy using the same override precedence as the SDK. It records only validated constant tool names, choice enums, booleans and a tool count. Unexpected tools or a changed initial forced-write policy fail closed before SDK delivery.

The returned stream proxy observes each event once, then yields that same event unchanged to Hermes. It does not buffer arguments or reasoning. Up to 128 bounded item identifiers are retained transiently for association; identifiers never enter diagnostics. An overflow flag makes that limit explicit.

The transport proxy counts the result of Hermes's actual `normalize_response()` without changing it. Consecutive inspections of the same response are counted once. Existing dispatch counters still measure tool execution separately.

Creation requests now enable the existing safe atomic snapshot mechanism. During streaming it is throttled to once per second; request checks, normalization and completion/error paths force a snapshot. Files live at `runtime/repair_diagnostics/PROJECT_UUID.json`, outside managed workspaces. A forced process termination can leave a snapshot up to approximately one second behind. Every persisted/returned field passes `safe_diagnostics()`.

## Added fields

Counters:

- `codex_function_calls_announced`
- `codex_function_argument_delta_events`
- `codex_function_argument_bytes` (UTF-8 size, not content)
- `codex_function_argument_done_events`
- `codex_function_output_items_done`
- `codex_normalized_tool_calls`
- `codex_incomplete_function_calls` (unsettled tracked calls plus explicitly incomplete done items)
- `codex_terminal_output_function_calls_detected`
- `codex_unassociated_argument_events`
- `final_request_tool_count`

Booleans:

- `codex_response_completed_seen`
- `codex_response_failed_seen`
- `codex_response_incomplete_seen`
- `codex_observation_tracking_overflow`
- `final_request_policy_rejected`
- `final_request_parallel_tool_calls`

Strict allowlisted strings:

- `final_request_tool_names` (one fixed string representing the three permitted names)
- `final_request_tool_choice_type`
- `final_request_forced_tool_name` (only a permitted tool name)

Counters aggregate physical stream attempts; the final request fields describe the latest checked request. Terminal-output detection counts function items in a terminal response whether or not item events also exist. Compare it with announced/done/normalized counters to distinguish terminal-only output. Successfully completed streams settle pending calls according to Hermes's existing behavior; explicitly incomplete done items remain visible.

## Offline regression

`tests/check_codex_stream_diagnostics.py` runs with Hermes's Python, real agent construction/request building, real Hermes preflight/Relay/runtime, the installed OpenAI SDK, and `httpx.MockTransport`. Socket connections are blocked and credentials/provider resolution are mocked. The mock inspects the **serialized HTTP request body**, after SDK merging, and verifies exactly the three allowed tools, forced `project_write_file`, `parallel_tool_calls=false`, and `stream=true`.

Each fixture makes exactly one mocked HTTP request:

1. Successful stream: over 5,000 argument deltas, arguments-done, item-done, response-completed. Exactly one normalized and dispatched call creates the temporary managed file; persisted diagnostics contain no content.
2. Incomplete stream: announced call plus over 5,000 deltas, then injected `TimeoutError`. The unchanged worker exception handler classifies timeout; no partial write, no normalized/dispatched call, one incomplete call. This deterministically exercises failure propagation without waiting for or changing production timers. Existing process tests separately verify deadline/cancellation cleanup.
3. Terminal-output-only stream: response-completed contains a function call but no item events. Diagnostics detect it; the installed parser intentionally does not reconstruct it, and no file is written. No parser change was made.

`tests/test_codex_observer.py` additionally covers late `extra_body` policy overrides, repair/post-write automatic policy, association failures, incomplete done items, bounded tracking and diagnostic-value filtering.

One controlled live Project Builder test is justified to identify which stream stage stalls. These offline fixtures do not establish that the live timeout is fixed.
