# Presentation startup and acceptance

## Fix delivered September 14, 2026

The adapter previously used only `shutil.which('codex')`. This PowerShell has no
Codex PATH entry, although native executables are installed in the VS Code
OpenAI extension. The running host must restart to load the updated adapter.

`codex_discovery.py` now uses an explicit `PROJECT_CODEX_EXECUTABLE` first, then
a native PATH executable, standard user installs, npm native payloads, and
versioned VS Code/Insiders extensions (newest version first). Discovery repeats
on invocation so extension updates do not leave a cached stale path. It does not
scan credential directories or execute npm/PowerShell wrappers. An invalid
explicit override fails closed. All engines share this adapter: Chat, Builder,
and autonomous repair. `shell=False`, read-only sandbox, disabled tools,
environment allowlist, trusted file writes and revision checks remain intact.

The host now loads its existing `.env` relative to its source file, independent
of the launch directory. No credentials were copied or changed.

## Exact startup commands (normal Windows PowerShell)

Run these from your normal Windows account, not an agent sandbox:

```powershell
Set-Location 'C:\Users\Abdullah Almakni\Desktop\AI-Personal-Assistant'
.\.venv\Scripts\python.exe tests/check_codex_host.py
```

The preflight prints only the executable path and fixed status messages. If it
reports login unavailable, authenticate through the CLI itself:

```powershell
$demoCodex = & .\.venv\Scripts\python.exe -c "from dotenv import load_dotenv; load_dotenv(); from codex_discovery import resolve_codex; print(resolve_codex())"
& $demoCodex login
.\.venv\Scripts\python.exe tests/check_codex_host.py
```

Use the same terminal and existing CODEX_HOME setting for preflight, login and
host startup. Do not copy auth files or paste tokens into the application.

Once preflight passes:

```powershell
docker compose up -d --build
docker build -f validation/Dockerfile.web -t hermes-validator-web:phase1 validation
.\.venv\Scripts\python.exe hermes_service.py
```

If the old host is still running, press Ctrl+C in its original terminal before
the final command. Keep the new host terminal open. In a second PowerShell:

```powershell
Set-Location 'C:\Users\Abdullah Almakni\Desktop\AI-Personal-Assistant'
Invoke-RestMethod http://localhost:8765/health
Invoke-RestMethod http://localhost:8000
.\.venv\Scripts\python.exe tests/check_presentation_flow.py
Start-Process 'http://localhost:8000/static/index.html'
```

Automatic discovery needs no PATH modification. To pin an installation, set
`PROJECT_CODEX_EXECUTABLE` to a fully qualified native `codex.exe` path in the
host environment or existing local `.env`; no surrounding quote characters in
the environment value. Remove/update a pin when that installation is removed.
Do not point it at `codex.cmd`, `codex.ps1`, or a command plus arguments.

## Exact professor demo steps

1. Open http://localhost:8000/static/index.html.
2. In Chat: “Explain autonomous coding agents in one sentence.” Show its answer.
3. In Project Builder: “Create a modern responsive coffee shop website with
   Home, About, Menu, and Contact sections. Use index.html, styles.css, and
   script.js with a working mobile menu.” Wait for success.
4. Show the generated files. Open this new project's index.html locally to
   preview the website. The application's file viewer displays source text.
5. Click Validate Project and show a passing result.
6. Append `const demoBroken = ;` to script.js in this new project's
   `generated_projects/<project_id>` directory and save.
7. Select/reload the project and Validate Project again. Show the JS failure.
8. Click Fix Project. Show queued/running events and the repair in Run Monitor.
9. Wait for completed status, then Validate Project again and show the pass
   and corrected JavaScript. Existing projects and publication gates stay intact.

## Verified results and external blockers

- Full regression suite: 177 tests, 176 passed, one skip.
- Python compilation passed.
- Both live health URLs returned HTTP 200.
- Native CLI discovery and required CLI flags verified against installed
  0.154.0-alpha.6.2. Two VS Code bundled native installations were found.
- Live acceptance stopped at Chat HTTP 503 on the existing, unrestarted host.
- CLI login status in the editing environment reports not logged in. This
  does not establish the login state of your normal Windows terminal.
- Docker engine access is denied to the editing environment.
- Builder, deliberate JavaScript syntax failure and autonomous repair pass
  offline tests (real node syntax check, mocked model output); live AI build,
  Docker validation and live repair are NOT certified until acceptance passes.

Run the acceptance command above after host restart/login. It creates one new
project, breaks only that project, repairs it, and retains its history. It never
publishes anything. Do not claim the presentation passed on health checks alone.
