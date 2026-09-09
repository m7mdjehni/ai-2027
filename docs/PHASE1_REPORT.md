# Phase 1 implementation report

Implemented inside the existing repository on 2026-09-07. No rebuild from scratch,
provider calls, generated-code execution, or production test-record insertion.

## 1. Files created

- `app/project_workspace.py`: shared validated project filesystem operations.
- `app/routers/projects.py`: project creation/history/file APIs.
- `hermes_projects.py`: restricted Windows worker execution through the existing router.
- `hermes_project_worker.py`: real Hermes Agent with only project read/write/list dispatch.
- `app/static/projects.js`, `app/static/projects.css`: Project Builder and text viewer.
- `generated_projects/.gitkeep`: dedicated generated-project root.
- `tests/test_projects.py`: offline HTTP, filesystem, routing, and regression suite.
- `tests/frontend_projects.test.cjs`: frontend DOM behavior/security harness.
- `tests/check_hermes_worker.py`: installed-Hermes compatibility smoke test.
- `docs/PHASE1_REPORT.md`: this report.

Runtime-only files under ignored `runtime/`: host stdout/stderr logs and service PID.
Tests used temporary directories; no demonstration project was fabricated.

## 2. Files modified

- `app/main.py`: register project routes and product title.
- `app/models.py`: additive Project model.
- `app/static/index.html`: Projects navigation/screen and product title.
- `hermes_router.py`: optional execution adapter/coding override; same routing table/fallback.
- `hermes_service.py`: authenticated build endpoint and concurrency limit.
- `compose.yaml`: project token and read-only generated-project mount.
- `.env.example`: project token and optional Hermes Python configuration.
- `.env`: generated a private shared token; preserved existing values; never displayed it.
- `.gitignore`, `.dockerignore`: exclude generated output and runtime logs.
- `README.md`: architecture, startup, APIs, security, testing, and presentation guide.
- `docs/TROUBLESHOOTING.md`: actual Docker retry and test-cleanup findings.

No changes to the existing Chat/Tasks/Notes endpoint implementations, Ollama
adapter, existing table definitions, Dockerfile, or requirements pins.

## 3. New endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/projects` | Build using a prompt and return actual files/routing metadata |
| `GET /api/projects` | History, with limit/offset pagination |
| `GET /api/projects/{project_id}` | Metadata and detected files |
| `GET /api/projects/{project_id}/files` | Detected file list |
| `GET /api/projects/{project_id}/file?path=index.html` | Safe text reading |
| Host `POST /projects/build` | Private-token protected Windows execution |

## 4. Database changes

The `projects` table was created in the running PostgreSQL database through existing
startup `create_all`. Columns: id, name, original_prompt, task_type, model, provider,
fallback_used, status, message, created_at. Confirmed existing conversations, tasks,
and notes tables remain. No production rows were inserted for testing.

## 5. Security protections

Fixed UUID workspaces, strict cross-platform path validation, extension/text limits,
credential-like filename blocking, link/junction/hard-link rejection, bounded file
counts/sizes, and full replacement of Hermes tool dispatch. No terminal, arbitrary
file reading, generated-code execution, or automatic package installation. Windows
worker timeout cleanup terminates the process tree before activating fallback.
Project files are mounted read-only in Docker and displayed as inert text in the UI.
The host build endpoint requires the shared token. Logs omit prompts, tokens, and
raw provider output. This is a tool-level boundary around trusted Hermes runtime
code, not an operating-system sandbox against malicious local processes.

## 6. Exact startup commands

Services were started during implementation. The host runs in the background;
presentation-friendly logs can be followed with:

```powershell
Get-Content .\runtime\hermes.stderr.log -Wait
```

For subsequent starts, from the repository directory, use separate terminals
(stop an existing host instance first to avoid a port conflict):

```powershell
.\.venv\Scripts\python.exe hermes_service.py
```

```powershell
docker compose up --build -d
docker compose ps
Start-Process 'http://localhost:8000/static/index.html'
```

Fresh-checkout dependency/token setup and alternative Hermes Python paths are
documented in README. Preserve the existing `.env` and PostgreSQL volume.

## 7. Exact test commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app/static/projects.js
node tests/frontend_projects.test.cjs
.\.venv\Scripts\python.exe -m compileall -q app hermes_router.py hermes_service.py hermes_projects.py hermes_project_worker.py
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -I tests/check_hermes_worker.py
```

## 8. What was tested

- Python suite: **18 tests, 17 passed, 1 skipped** (Windows symlink creation privilege).
- Real ASGI creation/history/list/read and invalid requests, temporary SQLite/files.
- Traversal, drive/UNC paths, reserved names, sensitive paths, oversized/binary writes,
  hard links, and mocked Windows reparse detection.
- Actual restricted tool dispatch writes files and denies shell/outside writes.
- Existing router: primary success, 429/timeout/unavailability fallback, total failure,
  and actual selected model/provider/fallback metadata.
- Worker invocation and Windows process-tree timeout cleanup, without executing a model.
- `/api/converse`, conversation persistence, Tasks CRUD, Notes CRUD, Ollama selection.
- Node harness: submission, history, metadata, file clicks, inert malicious HTML text.
- Installed Hermes: real constructor and restricted tool dispatch with model turn
  mocked and socket connections blocked. Passed after fixing test log cleanup.
- Docker image built successfully on retry; recreated backend started successfully.
- Live PostgreSQL table inspection: conversations, tasks, notes, projects.
- Live HTTP 200 for projects/tasks/notes/conversations and frontend HTML/JS/CSS.
- Docker-to-Windows authenticated build request rejected an empty prompt with 422,
  confirming connectivity/authentication without running Hermes.
- Docker inspection confirmed `/app/generated_projects` has `writable=false`.

## 9. What still needs a real Hermes test

Build a coffee shop website from Projects using the real MiMo provider. Confirm the
model makes file-tool calls, inspect generated content, and confirm actual fallback
behavior if a real provider failure occurs. Mocked fallback proves application
routing, not external provider availability or generated project quality.

## 10. Limitations and errors

- Website preview is explicitly pending; the safe text viewer is implemented.
- No visual browser automation was available; frontend behavior used a Node DOM harness.
- Synchronous builds; no durable queue/cancellation/crash recovery. Interrupted builds
  can retain `building` or `unknown` status. Partial files remain inspectable.
- Phase 1 supports bounded UTF-8 text projects, not binary assets or arbitrary programs.
- Single-user existing dashboard; not a public multi-tenant deployment.
- First Docker build failed when pip returned no SQLAlchemy versions. Retry succeeded
  with the unchanged `SQLAlchemy==2.0.29` pin.
- First installed-Hermes smoke test encountered a Windows log lock during temporary
  directory cleanup. Explicit logging shutdown fixed it; subsequent runs passed.
- No real paid/provider calls were made.
