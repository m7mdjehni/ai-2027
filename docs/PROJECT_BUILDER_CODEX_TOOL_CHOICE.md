# Restricted Project Builder: offline Codex diagnosis

No live provider call was made. This is a tested request-policy correction, not proof that the observed live timeout is resolved.

## Findings from the installed Hermes implementation

- `hermes_cli/oneshot.py` constructs `AIAgent` with the resolved provider, credentials and `api_mode`, as the project worker does. CLI enables its normal toolsets and wires interactive behavior; copying its unrestricted access or YOLO/hooks setup is inappropriate here.
- `agent/agent_init.py` obtains tool definitions and derives `valid_tool_names`. The worker deliberately replaces both before conversation startup. This replacement is supported by the downstream path: `agent/conversation_loop.py` reads `agent.tools` when preparing requests.
- `_skip_mcp_refresh=True` prevents turn-context MCP refresh from replacing that snapshot. The worker's three-function dispatch overrides reject terminal and other names regardless of advertised schemas.
- System prompts are built at conversation startup, after replacement. The ephemeral project instructions are appended to the effective system prompt. They already instruct Hermes to invoke `project_write_file`; the defect is not a missing instruction to write.
- `agent/transports/codex.py` converts OpenAI function schemas into flat Responses function tools and sets `tool_choice="auto"`. `agent/codex_responses_adapter.py` preserves explicit Responses `tool_choice` in preflight. `run_agent.AIAgent._build_api_kwargs` is the local subclass seam used by this correction.
- Both installed outer API wait paths reach a streaming Responses request. Previous outer-helper wording about non-streaming does not prove the actual Codex HTTP request is non-streaming.
- Existing schema conversion and mocked function-call normalization/dispatch tests pass. No evidence was found that custom tool registration itself is incompatible with Codex.

## Small correction

For new-project `openai-codex` / `codex_responses` requests only, override the final Hermes-built request's choice with:

```json
{"tool_choice":{"type":"function","name":"project_write_file"},"parallel_tool_calls":false}
```

Retain all three allowed tools, and fail closed if the outgoing tool names/types do not match that exact set. Keep the choice until a write actually succeeds; a rejected write does not unlock automatic selection. After the first successful write, Hermes's normal `auto` selection resumes. Repair requests are unchanged, including their read-before-edit and revision policies.

No timeout, provider fallback, installed Hermes file, generated project, plugin/hook switch or filesystem protection was changed. Existing no-file and timeout failures remain failures.

## What is and is not established

The confirmed gap is that mandatory artifact creation previously relied only on prompting while the API explicitly allowed automatic/text-only behavior. The correction enforces the first write at the supported request layer. Streaming counters alone cannot reveal why the particular live request never completed a tool call: provider stalls and incomplete streamed tool arguments remain possible. A forced function choice cannot guarantee provider responsiveness.

`tests/check_project_codex_choice.py` uses the installed constructor, actual system/request construction, Responses preflight and response normalization with temporary workspaces and blocked sockets. It reproduces the original `auto` choice, verifies the corrected choice and exact schemas, dispatches a mocked write, rejects forbidden writes/tools, verifies automatic selection resumes only after success, preserves repair behavior, and checks no-tool/timeout failures. It does not contact Codex.

One controlled live build is the next acceptance gate. Success must include an actual managed file write and a completed build; do not declare the live issue fixed from offline tests alone.
