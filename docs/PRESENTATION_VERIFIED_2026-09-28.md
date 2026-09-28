# Presentation verification — 28 September 2026

The web application demo passed with real AI and Docker validation. VS Code
Hermes ACP is blocked by Windows Device Guard. These are separate outcomes.

## Start the presentation

From this project directory in your normal PowerShell terminal:

```powershell
.\start_presentation.ps1
```

Open http://localhost:8000/static/index.html. The script reuses existing
containers and router processes, checks the validator image, checks Codex login,
and prints status. It does not recreate containers, delete data, read credentials
for diagnosis, or change security policies. Applications use their existing
configuration and authentication. The already-running-services path was tested.
Cold-start and missing-image branches have not been exercised in this session.

After administrator approval of Hermes, the optional dashboard command is:

```powershell
.\start_presentation.ps1 -StartHermesDashboard
```

The optional dashboard branch is NOT TESTED. Port 9119 was not listening.
It uses the installed Hermes executable with a loopback bind and preserves auth.

## ACP diagnosis and prepared configuration

Installed extensions: ACP Client 0.2.0 and Hermes Agent VS Code 0.2.53.
ACP Client's default was `hermes acp`, inherited environment, current workspace
as cwd. On Windows the installed extension launches through `cmd.exe` with
`shell: true`. Logs at 08:27, 08:30, and 08:31 UTC explicitly report that
Device Guard blocked Python311's `Scripts\hermes.exe`, followed by exit 4551
and `ACP connection closed`. This is an OS application-control failure before
the ACP handshake, not a missing ACP dependency.

Workspace `.vscode/settings.json` now selects the absolute installed executable:
`C:\Users\Abdullah Almakni\AppData\Local\Programs\Python\Python311\Scripts\hermes.exe`.
It is quoted for the extension's Windows shell behavior. Arguments are `["acp"]`,
environment overrides are empty, cwd follows the open workspace, and permission
requests remain `ask`. Existing user settings and Hermes configuration were not
changed. The workspace settings are local and ignored by the existing Git rules.

The fresh probe reproduced Device Guard exit 4551 with that exact saved command.
**Connection remains FAIL.** An administrator must approve the Hermes executable
or provide an approved installation. No alternate launcher was used to evade the
block, and no security policy was disabled.

After that approval, reopen the workspace, run `ACP: Connect to Agent`, and select
Hermes Agent. `ACP: Set Agent Model` is provided by the installed extension.
No Hermes model list or model inference was verified in this session. Neither
ACP workspace reads nor writes were tested because initialization was blocked.
For a later write test, restrict it to a newly created temporary file.

```powershell
py -3.11 -c "import sys; sys.argv=['hermes','acp','--check']; from hermes_cli.main import main; main()"
node tests/check_acp_connection.cjs
```

The first command passed. The second mirrors the extension launch and prints
only a classified failure, or handshake/session status and advertised models.
A successful session alone does not prove model inference or file access.

## Verified live project

- Project: `fd28085590c6494093c5a5ce20992d66`
- Files: `index.html`, `styles.css`, `script.js`
- Actual API-reported model/provider: `gpt-5.6-sol` / `openai-codex`
- Baseline validation: PASS
- Deliberate `const demoBroken = ;` added only to this new project's JavaScript:
  detected, PASS
- Durable AI repair: PASS, run `df468bffbeb744499682649adef62b1f`
- Repair history contains failed validation cycle 1, a Codex model attempt,
  and passed validation cycle 2.
- Final standalone validation: PASS
- Four persisted Run Monitor entries were retrieved after completion.

No pre-existing generated project was edited or removed. Tasks and Notes GET
endpoints returned HTTP 200. No publication was requested or performed.

## Test evidence

- PASS: backend HTTP 200 and router `/health` HTTP 200.
- PASS: Docker backend running; PostgreSQL healthy; validator image present.
- PASS: Chat POST HTTP 200 with nonempty reply and no fallback.
- PASS: native Codex starts, existing login reported, and minimal live request
  returns `CODEX_PRESENTATION_OK` using the restricted application adapter.
- PASS: complete `tests/check_presentation_flow.py` with real AI and validation.
- PASS: final Python suite, 181 tests run, 180 passed and one skipped, including
  two new native-install discovery regression tests.
- PASS: three frontend test scripts, including 17 mocked Run Monitor scenarios.
  Those UI tests are distinct from the live API and durable history checks.
- PASS: changed Python files compile and discovery imports; PowerShell parses.
- PASS: repeated startup retained existing service PIDs; no restart was required.
- PASS: all four requested website sections exist, responsive CSS is present,
  and the deliberately introduced syntax error was removed by the repair.
- FAIL: ACP launch, Device Guard exit 4551.
- NOT TESTED: Hermes model catalog/inference, ACP file access, visual VS Code
  connection, dashboard startup, interactive browser rendering, cold startup.

Codex discovery now checks the standalone `current` pointer and the native
installer location before extension copies when PATH is unavailable. Explicit
invalid overrides still fail closed. No adapter sandbox, subprocess, path,
credential-filtering, revision, timeout, or validation restrictions changed.
The installed native pointer resolved to release 0.154.0 during verification.
The probe follows the CLI's documented noninteractive flow:
[official OpenAI documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

## Professor demo

1. Run the startup script and show the service statuses.
2. Open Chat and ask for a one-sentence explanation of autonomous coding agents.
3. Show Tasks and Notes without deleting existing entries.
4. Select the verified project, show its three files, and open its `index.html`.
5. Show Run Monitor's baseline pass, deliberate failure, repair, and final pass.
6. For a fresh live demonstration, create a new coffee-shop project. Validate it,
   introduce `const demoBroken = ;` only in that new project's JavaScript,
   validate again, then click Fix Project and show the retained history.
7. Demonstrate VS Code Hermes only after the Device Guard blocker is resolved
   and the ACP connection, model, and temporary-file tests pass.

To repeat the complete web-app acceptance test (creates another new project):

```powershell
.\.venv\Scripts\python.exe tests/check_presentation_flow.py
```
