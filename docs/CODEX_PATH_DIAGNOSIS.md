# Project Builder Codex execution diagnosis

Inspected the current repository and installed Hermes source on 2026-09-07.
No production code, timeouts, installed Hermes files, or running services were changed.
No provider calls were made. Added only an offline probe and this report.

## Confirmed failure mechanism

The reported error originates in the generic `interruptible_api_call` watchdog in
installed `agent/chat_completion_helpers.py`, around lines 1744–1783. It compares
elapsed request time against `_stale_timeout` and raises `TimeoutError` if no full
response has been assembled. The quoted message does not mean `stream=False` was
sent to Codex, nor does “no response” prove that no SSE events arrived.

The default is 90 seconds in `run_agent.py::_resolved_api_call_stale_timeout_base`
(around line 1470). Provider/model configuration and request context-size scaling
can affect it. The total elapsed-time check is separate from Codex first-event and
event-idle checks: SSE activity updates `_codex_stream_last_event_ts`, but does not
reset `_call_start` for the total-request check. Increasing the outer worker timeout
does not fix that inner failure.

## Actual request path: CLI and worker

```text
resolve_runtime_provider(openai-codex) -> api_mode=codex_responses
AIAgent.run_conversation
  -> interruptible_streaming_api_call (normally preferred even in quiet mode)
  -> interruptible_api_call (Codex-specific delegation)
  -> _dispatch_nonstreaming_api_request
  -> _run_codex_stream / run_codex_stream
  -> responses.create(stream=True)
```

The function named `_dispatch_nonstreaming_api_request` explicitly special-cases
`codex_responses` to call `_run_codex_stream` (chat_completion_helpers.py, ~936).
The streaming wrapper delegates back to the same interruptible helper for Codex
(~3454). `codex_runtime.py::run_codex_stream` explicitly sets `stream=True` (~1636).
Adding a no-op streaming callback or invoking CLI instead would not bypass this.

## Exact construction differences

| Concern | Installed normal `hermes -z` | Project Builder |
|---|---|---|
| Agent | `AIAgent` | Subclass of the same `AIAgent` |
| Provider resolution | `resolve_runtime_provider` | Same resolver |
| API mode | Passes `runtime.get('api_mode')` | Same |
| Platform | `cli` | Omitted (`None`) |
| Tools | Configured CLI tools and MCP discovery | Initially empty; replaced by three project tools |
| Dispatch | Hermes built-in tool execution | Restricted project-only dispatcher |
| Context | Normal rules/memory/context and optional skills | Context/memory/background review disabled |
| Session DB | Creates session store when available | `None` |
| Internal fallback | Profile fallback chain | Disabled; host router handles attempts |
| Display callbacks | Explicitly clears text/tool callbacks | None by default |
| Approvals | One-shot sets YOLO and accepted hooks | Worker removes those environment flags |
| Task | Depends on CLI prompt | Requires actual project-file tool calls |

These differences affect request contents and behavior. None inspected selects an
incompatible non-streaming Codex HTTP transport. CLI may have different context,
reasoning workload, tool output sizes, credentials/profile environment, or timing.
There is no captured, matching successful CLI request to establish which accounts
for the previously successful run. A context-size difference can also select a
different watchdog floor; this is a source-supported possibility, not a measured
explanation for these particular live attempts.

## Custom tools are supported in the installed adapter

`CodexTransport.build_kwargs` calls `_responses_tools`. That converter accepts the
worker's OpenAI function schemas, flattens them into Responses function tools, and
sets `strict=False`. All three current tools passed the real installed preflight.
The final stream boundary retains tools (this version can move them into
`extra_body` to bypass SDK transformation) and sends `stream=True`.

`_normalize_codex_response` converts completed Responses `function_call` items to
the `.tool_calls` shape consumed by `ProjectToolBoundary`. A mocked Codex response
was converted through this real adapter and wrote an actual temporary index.html.
Therefore the installed integration supports these custom tools locally. Offline
tests cannot establish live acceptance/availability of a particular model account.

The worker prompt already explicitly says to create the requested project using
`project_write_file`, avoid code-only final answers, and check with `project_list_files`.
`write_file_invoked=False` means execution did not reach that tool; it does not by
itself prove the model refused tools or returned code as text. Tool execution waits
for an assembled model response, so a timeout during reasoning/argument generation
can produce exactly this outcome.

Each host attempt launches a new worker process and constructs a new agent, using
the same server-created UUID workspace. No agent conversation is passed to the
next attempt. Existing workspace files are not cleared between attempts.

Important current-state discrepancy: `hermes_router.py::PROJECT_MODELS` currently
selects GPT-5.6 Sol as the project primary with no fallback. The earlier MiMo ->
GPT logs describe an earlier routing configuration, not the current code. This
inspection did not alter that configuration.

## What is not yet established

The exact inner watchdog and request path are established. The available evidence
does not identify why a complete Codex response did not arrive within 90 seconds:
provider stall, slow generation, middleware, or a request-specific condition remain
possible. There is no HTTP status or schema-validation error in the supplied timeout
message. The earlier primary HTTP 400 has no response body in the supplied evidence;
its precise cause cannot be inferred from the status code alone.

Existing diagnostics also lose information: api_mode is collected but not included
in the host log line; returned timeout failures leave exception_type/timeout_reason
unset; blanket logging suppression hides transport phases. The current regex-based
raw-text redactor is not sufficient to guarantee removal of arbitrary OAuth JSON,
quoted secret values, or unlabeled tokens. Avoid adding raw request/response dumps.

## Recommended minimal next step

Do not change architecture on the premise that custom tools force non-streaming;
the installed source and mocked transport probe disprove that premise. The smallest
useful change is safe transport telemetry in repository code: actual api_mode,
transport class, endpoint category (not full URL), first/last SSE event timing,
event counts/types, text/argument character counts, request-size estimate, selected
watchdog value, terminal status, and tool-attempt counts. Preserve scalar exception
type/status; avoid raw credentials, headers, prompts, tool arguments, and response
bodies. This can distinguish stream progress from a stalled request without changing
timeouts or installed Hermes. No such production logging change was applied here.

The manifest proposal is supported as an output strategy: generate a bounded JSON
manifest with zero model tools, validate the entire manifest, then write through
the existing trusted workspace functions on Windows. It removes custom dispatch
and keeps Docker/API/database boundaries unchanged. However, it uses the SAME
Codex streaming transport and watchdog; it is not a demonstrated timeout cure.
A large all-files manifest could take longer than an individual file tool call.

Do not run unmodified `hermes -z` with its normal enabled tools and rely on a prompt
to prevent writes: one-shot enables configured tools and auto-approves actions.
An empty CLI toolset argument is normalized to defaults, not guaranteed zero tools.
A manifest implementation must enforce zero tools/no executable dispatch, not only
ask the model to avoid using tools. Existing traversal/size/count/UUID/auth/history/
read-only mount protections must remain; validation must complete before publishing
files. Treat generated code strictly as data.

Potential modification scope, if selected after this report:
- `hermes_project_worker.py`: safe telemetry, or enforced tool-free manifest generation.
- `hermes_projects.py`: log safe scalar telemetry; validate/materialize manifests if selected.
- `tests/test_projects.py` and offline Codex probe: corresponding regressions.
- README/documentation: describe the actual execution behavior.
- `app/project_workspace.py` only if an atomic manifest-validation helper is needed.

## Offline verification

Ran existing mocked tests first: 20 tests, 19 passed, one Windows symlink-permission skip.
Then added and ran:

```powershell
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/diagnose_codex_path.py
```

Four probe groups passed: installed CLI initialization contract, current schemas,
both outer helpers reaching mocked `Responses.create(stream=True)`, and normalized
Codex tool calls reaching the actual restricted writer. Socket connections were
blocked, credentials were not resolved, and all files used temporary directories.
