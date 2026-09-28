# Presentation reliability

Current live results and startup script: [28 September verification](PRESENTATION_VERIFIED_2026-09-28.md).
The web build/validation/repair flow now passes; VS Code ACP remains blocked by Device Guard.

For the latest executable-discovery fix, exact startup commands and current
acceptance blockers, see [Tomorrow's demo](TOMORROW_DEMO.md). The historical
results below predate that fix.

Build Project, Fix Project, and Chat now default to the installed native Codex
CLI. No Hermes Python SDK or hermes.exe is needed for this path. The backend's
existing `AI_PROVIDER=hermes` bridge setting remains compatible; `codex` is also
accepted. No existing projects or database data are removed by these changes.

## Start from the project directory in PowerShell

Use the existing `.venv`, which was verified to have FastAPI 0.100.0,
Pydantic 1.10.15, and SQLAlchemy 2.0.29. It currently uses Python 3.10; the Codex
adapter does not require the Hermes interpreter. The global Python 3.11/Hermes
installation has incompatible application dependencies. Do not use it to run
the host directly.

```powershell
docker compose up -d --build
docker build -f validation/Dockerfile.web -t hermes-validator-web:phase1 validation
.\.venv\Scripts\python.exe hermes_service.py
```

Keep the final command running in its terminal. If port 8765 already has the
old host process, stop that process in its original terminal before restarting.
Reuse the existing service configuration and login; do not copy credentials.
The host and backend still require their matching project service token and
PostgreSQL settings. These files/settings were not inspected or changed.

For a fresh Python 3.11 environment, use `python -m venv .venv-demo` followed by
`.\.venv-demo\Scripts\python.exe -m pip install -r requirements.txt`, then use
that interpreter in place of `.venv`. Dependency downloads were blocked in the
editing session; the existing `.venv` was used for verification instead.

Open `http://localhost:8000/static/index.html` (or your configured backend port).
The host health URL is `http://localhost:8765/health`.

Optional nonsecret engine settings are `PROJECT_AI_ENGINE=codex` and
`CHAT_AI_ENGINE=codex` (both defaults). `PROJECT_CODEX_EXECUTABLE` can point to
the native codex.exe if it is absent from the host terminal's PATH. Explicit
`hermes` engine settings retain the old integration for compatibility, but it
is not recommended for this presentation.

## Professor demo

1. In Chat ask “Explain what an autonomous coding agent does in two sentences.”
2. Add a task and a note with the existing Tasks and Notes controls.
3. In Project Builder submit:
   “Create a modern responsive coffee shop website with Home, About, Menu,
   and Contact sections.”
4. Wait for completed status. Show the actual generated HTML, CSS, and JS in
   the file viewer. The app intentionally displays code as text. To show the
   website itself, open this new project's index.html locally in a browser.
5. Click Validate Project and wait for a passing result.
6. In your editor open a `.js` file inside this new project's
   `generated_projects/<project_id>` folder. Append `const demoBroken = ;`
   and save. Do not edit an older generated project.
7. Reload/select the project and click Validate Project again. Show the syntax
   failure. Fix Project must use the newly captured revision.
8. Click Fix Project. Show the queued/running repair and validation events in
   Run Monitor. Wait for validation to pass and show the corrected file.
9. If presenting GitHub publication, use its existing preview/confirmation
   workflow only after validation passes. Existing publication security gates
   remain in place.

## Verification and limitations of this editing session

The new regression suite exercises build file application, default chat routing,
CLI arguments, safe subprocess invocation, environment filtering, malformed
responses, path/size restrictions, cancellation, stale revisions, and repair.
The repair integration uses a real `node --check` syntax failure and the real
repair controller with mocked AI output. It does not claim a live AI response
or Docker validator run.

Verification on 2026-09-14:

- Full pinned-environment suite: 171 tests, 170 passed and 1 skipped.
- Syntax parsing: all 72 Python source files passed; changed Python files also
  compiled under Python 3.10 and 3.11.
- Frontend scripts, inline page JavaScript, and validator JavaScript passed
  `node --check`.
- Backend startup/migrations passed against isolated SQLite. Host lifespan
  startup/shutdown passed with an isolated dispatcher; ASGI health checks passed.
- Existing live backend and host health endpoints returned HTTP 200.
- Live Chat failed before project creation. Live Codex build/repair and Docker
  validator acceptance are not certified by these results.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe -m compileall -q app codex_adapter.py hermes_projects.py hermes_router.py hermes_repair.py hermes_service.py
.\.venv\Scripts\python.exe tests/check_presentation_flow.py
```

This editing session could not access Docker's engine or download dependencies
(Windows access restrictions). The existing backend and host health endpoints
both returned HTTP 200, but the live acceptance stopped at Chat before creating
a project. A separate CLI smoke check could not confirm process-tree cleanup
under these restrictions. Complete the live steps after restarting the host in
your normal terminal; offline tests are not evidence that provider availability
or Docker validation are currently working.

The live acceptance command creates one new coffee project, introduces a syntax
error only in that new project, validates it, submits a durable repair, and checks
the final validation. It retains the project and Run Monitor history. It does
not publish to GitHub. A failed run is not reported as a passing demonstration.

## Implementation boundary

Codex runs with `shell=False`, stdin prompts, selected project cwd, read-only
sandbox, no approvals, ephemeral sessions, ignored user configuration, disabled
shell/apps/browser/agent tools, and structured output. Only the trusted Python
file API applies returned contents after validating all paths, limits, and the
current revision. Repair retains its existing cancellation, process-tree cleanup,
checkpoints, validation budgets, durable reservations, and publication gates.
No raw CLI stderr or credential environment variables enter API responses.

CLI options were checked against installed Codex 0.154.0 help and the
[official configuration reference](https://developers.openai.com/codex/config-reference/).
