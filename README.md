# Hermes Autonomous Coding Agent

## Project Overview

An autonomous AI development system by Abdullah Almakni. It combines local AI
chat, website generation, validation, error repair, and a Run Monitor in one web
application. FastAPI connects the browser to PostgreSQL and host-installed Ollama.
Hermes and Codex remain optional online integrations.

**Verified local workflow:** generation → validation → deliberate syntax error →
Ollama repair → validation PASS. Core components use local Ollama without requiring
a cloud LLM. **Physical Wi-Fi-disconnection verification: SKIPPED IN FINAL
REGRESSION PASS**, by user request. This is not a physical offline PASS claim.

## Main Objective

Build an autonomous coding workflow where a user describes a project and the
system can generate files, detect errors, attempt repairs, and record the results.
The educational focus is the complete development loop and its observable limits.

## Key Features

- Local AI Chat with Ollama and Llama 3.2 3B.
- Offline-first architecture using installed models and cached dependencies.
- Project Builder for HTML, CSS, and JavaScript websites.
- Deterministic validation and bounded autonomous repair.
- Run Monitor with persisted events, model attempts, and changed files.
- PostgreSQL persistence for application data and execution history.
- Docker services for FastAPI and PostgreSQL.
- Local generated projects and a separate website preview.
- Optional Hermes, Codex, and GitHub publication integrations.

## Architecture

```text
User / Browser (:8000/static/index.html)
      |
      v
FastAPI Backend (Docker)
      +---- Ollama (Windows host :11434)
      |         +---- Llama 3.2 3B
      |
      +---- PostgreSQL 15 (Docker; pgdata volume)
      |
      +---- Local Python Helper (Windows :8766)
                +---- Project Builder ---- Ollama
                +---- Validator --------- Docker, network disabled
                +---- Local AI Repair ---- Ollama -> Validator
                +---- Generated Projects
                          +---- Local preview (:8050)

Optional online integrations:
      Hermes / Codex through the legacy host router (:8765)
      Hermes gateway through the Compose "online" profile
```

Docker runs the backend, database, and optional Hermes container. Windows runs
Ollama, its downloaded model, the project helper, and the preview server.
The local helper reuses `hermes_service.py` and related project modules without
requiring the Hermes package or Codex CLI. The backend mounts generated files
read-only; the host helper performs controlled writes and starts validation.

## Technology Stack

| Technology | Verified version / role |
|---|---|
| Python | Backend 3.11.16; host virtual environment 3.10.0 |
| FastAPI | 0.100.0; API and UI hosting |
| PostgreSQL | 15.19, using `postgres:15` |
| Docker / Docker Compose | Engine 29.6.2; core service orchestration |
| Ollama | 0.34.4 on Windows |
| Llama 3.2 3B | Local model `llama3.2:3b` |
| HTML / CSS / JavaScript | Browser UI and generated websites |
| Node.js parsers | Acorn, css-tree, parse5 in the validator image |
| Hermes | Optional online routing and gateway integration |
| Codex | Optional online coding adapter |

Versions describe the tested presentation machine. Fresh downloads may differ.

## Local AI Model

| Item | Value |
|---|---|
| Runtime | Ollama 0.34.4 |
| Model | `llama3.2:3b` |
| Model ID | `a80c4f17acd5` |
| Approximate size | 2.0 GB |

The approximately 2 GB model is stored by Ollama outside this repository and is
**not uploaded to GitHub**. Download it once during setup:

```powershell
ollama pull llama3.2:3b
ollama --version
ollama list
ollama run llama3.2:3b
```

Enter a short prompt; use `/bye` to exit the interactive model session.

## How AI Routing Works

The default core provider is **Ollama / llama3.2:3b**. Backend chat calls Ollama at
`http://host.docker.internal:11434`. The Windows project helper uses
`http://127.0.0.1:11434` for generation and repair.

Local startup enforces Ollama. The transport accepts local endpoints, disables
HTTP proxies and redirects, and rejects cloud-tagged models. If local inference
fails, the application reports an error; it does not silently fall back online.
The UI and repair history record the provider and model actually used.

Hermes and Codex require explicit online configuration. They are not required for
local chat, generation, validation, or repair.

## Project Builder Workflow

```text
User Prompt
    |
    v
Ollama / Llama 3.2 3B
    |
    v
Generate Files -> Validation -> Errors?
                                |     |
                               No    Yes
                                |     |
                               Done  Local Repair -> Revalidate
```

Project Builder requests one file at a time: `index.html`, `styles.css`, and
`script.js`. The host applies validated file proposals inside the project folder.
Build completion means files exist; click **Validate** to check their syntax.

Example prompt:

> Create a simple water shop website. Use index.html, styles.css, and script.js.
> Use only local assets, system fonts, and vanilla JavaScript.

The UI displays **Provider: Ollama**, **Model: Llama 3.2 3B**, and
**Fallback used: No** for a successful local build. Files open as text in the UI.
Open the actual website separately at:

```text
http://127.0.0.1:8050/<project-id>/index.html
```

## Validation

Validation uses deterministic local parsers for HTML, CSS, JavaScript syntax, and
referenced local files. A cached validator image runs with networking disabled,
a read-only filesystem, and bounded resources. It returns file/line diagnostics,
a revision, and a pass/fail result stored in PostgreSQL.

A syntax PASS does not prove all interactions, accessibility, or visual quality.
External references can produce warnings and need separate browser review.

## Autonomous Repair

**Fix Project** queues a bounded repair run. The local helper gives diagnostics to
Ollama, applies allowed edits, and revalidates. A single JavaScript syntax error
uses a focused line-repair prompt; broader errors use bounded file context.
The Run Monitor shows real stages, validation results, model attempts, and changes.

A run allows at most three repairs and four validations within 600 seconds.
Cancellation, stale-revision checks, checkpoints, and no-progress detection bound
the workflow. Optional Hermes/Codex workers use the same repair supervisor.

The 3B model may need focused prompts. Earlier attempts mixed file contents or
repeated invalid code and stopped without progress. Per-file generation and
focused repair succeeded in the recorded demonstration; success is not guaranteed.

## Generated Projects

Files live in `generated_projects/<project-id>/`. The preserved presentation
examples below passed local validation and browser load checks. They are ignored
by Git, so a fresh clone does not include these websites or their database records.

| Demo | URL on the presentation machine |
|---|---|
| Water Shop | http://127.0.0.1:8050/0b59fd2ac45b45aabe3febf97060f464/index.html |
| Coffee Shop | http://127.0.0.1:8050/fd28085590c6494093c5a5ce20992d66/index.html |
| Portfolio | http://127.0.0.1:8050/024b7d588ddd4c579bac7ee7351b07a2/index.html |

Start all previews with `start_offline.ps1`. On another computer, generate a new
website and use its new project ID. Copying only website files permits static
preview, but does not recreate Project Builder history in the new database.

## Database

PostgreSQL stores conversations, tasks, notes, project metadata, repair runs,
ordered events, model attempts, validation results, and optional publication
records. Generated files and repair checkpoints are stored on the Windows
filesystem. The named Docker volume `pgdata` retains the database between starts.

## Docker Compose

[compose.yaml](compose.yaml) defines three services:

| Service | Purpose | Default startup |
|---|---|---|
| `db` | PostgreSQL 15; persistent `pgdata`; readiness healthcheck | Required |
| `backend` | FastAPI application; waits for healthy `db` | Required |
| `hermes` | `nousresearch/hermes-agent:latest`, `gateway run`, port 9119 | Optional `online` profile |

The Hermes service remains visible in Compose, including its `~/.hermes:/opt/data`
mount. The backend does not depend on it. To select it deliberately while online:

```powershell
docker compose --profile online up -d hermes
```

Ollama is intentionally installed on Windows, so there is no duplicate Ollama
container. Docker Desktop resolves `host.docker.internal` to the host; the verified
backend URL is `http://host.docker.internal:11434`. The host helper also launches
short-lived containers from the cached `hermes-validator-web:phase1` image.

`BACKEND_PORT` controls the published UI port (default 8000). Within Compose,
the database address is always `db:5432`. Keep host database port 5432 for this
setup. Do not use `docker compose down -v`; it deletes the database volume.

## Installation — New Computer

### Requirements

- Windows 10/11 compatible with your Docker Desktop installation; Windows 11 was tested.
- Git and Docker Desktop with its Linux engine running.
- Ollama installed from the [official Windows download](https://ollama.com/download/windows).
- Python 3.11 recommended for the host helper (the existing 3.10 environment also works).
- Node.js only for frontend tests; Microsoft Edge and Playwright for browser checks.

## Setup on Another Computer

**First-time setup requires Internet** for the repository, software, Python
packages, container images, and model download. Run these PowerShell commands:

```powershell
git clone https://github.com/Makni011/Hermes-Autonomous-Coding-Agent.git
cd Hermes-Autonomous-Coding-Agent
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull llama3.2:3b
ollama list
Copy-Item .env.example .env
```

For this **new installation**, generate private database and helper credentials
in `.env` without printing them. This replaces only the template placeholders:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; import secrets; p=Path('.env'); s=p.read_text(); s=s.replace('REPLACE_WITH_A_UNIQUE_DATABASE_PASSWORD', secrets.token_hex(24)); s=s.replace('PROJECT_BUILDER_TOKEN=\n', 'PROJECT_BUILDER_TOKEN='+secrets.token_hex(32)+'\n'); p.write_text(s)"
```

Preserve an existing `.env`. Changing a password in that file does not update an
already initialized PostgreSQL volume. The helper and backend need the same
`PROJECT_BUILDER_TOKEN`, at least 32 characters long.

Build/cache the required images during setup:

```powershell
docker pull postgres:15
docker compose build backend
docker build -f validation/Dockerfile.web -t hermes-validator-web:phase1 .
docker compose config --quiet
docker compose up -d
docker compose ps
.\start_offline.ps1
```

`docker compose config` also validates configuration, but prints resolved private
values. Use `--quiet` when presenting or saving terminal output.

Open **http://localhost:8000/static/index.html**. The startup script also starts
the Windows helper and preview; `docker compose up -d` alone does not start them.
A clean-machine installation has not been executed in this final pass.

## Environment Configuration

[.env.example](.env.example) is the public template. Keep `.env` private.

| Setting | Default / action |
|---|---|
| `AI_PROVIDER` | `ollama` |
| `OLLAMA_MODEL` | `llama3.2:3b` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` for Docker |
| `OLLAMA_HOST_BASE_URL` | `http://127.0.0.1:11434` for Windows |
| `PROJECT_AI_ENGINE`, `CHAT_AI_ENGINE` | `ollama` |
| `HERMES_ROUTER_URL` | `http://host.docker.internal:8766`, the local helper |
| `HERMES_ROUTER_TIMEOUT` | 620 seconds |
| `PROJECT_BUILDER_TOKEN` | Required private random token, at least 32 characters |
| `POSTGRES_USER`, `POSTGRES_DB` | `assistant`, `assistantdb` |
| `POSTGRES_PASSWORD` | Replace the template placeholder privately |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `db`, `5432`; helper connects through host loopback |
| `BACKEND_PORT` | `8000`; startup follows this configured port |

The legacy variable name `HERMES_ROUTER_URL` is retained for compatibility; it
points to the local helper in the core setup. `HERMES_ROUTER_PORT=8765` applies
only to the optional legacy router; `offline_host.py` uses 8766.

## Running Locally After Setup

Start Docker Desktop and Ollama, then run:

```powershell
.\start_offline.ps1
```

The script verifies local configuration, Docker, cached images, Ollama/model,
PostgreSQL, backend, actual backend-to-Ollama inference, helper, preview, and UI.
It prints PASS/FAIL and the UI URL. It uses `--pull never --no-build`, requires no
Hermes/Codex, and reuses healthy running services. It never downloads models or
changes Windows security settings.

If PowerShell reports that scripts are disabled, the tested process-only fallback is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_offline.ps1
```

This does not change the saved execution policy. Manual fallback, in two terminals:

```powershell
# Terminal 1, with local settings in .env
docker compose up -d --pull never --no-build db backend
.\.venv\Scripts\python.exe offline_host.py
# Terminal 2
.\.venv\Scripts\python.exe preview_local.py
```

After setup, the core uses the local model, backend, database, and project files.
Optional Hermes/Codex and publication may require Internet. No network-state
change is part of the presentation or final regression procedure.

## Testing and Verification

Final local regression, 28 September 2026:

| Check | Result |
|---|---|
| Python tests | PASS: 189 run, 188 passed, one skipped |
| Frontend tests | PASS: three scripts, including 17 mocked Run Monitor scenarios |
| Compose configuration / core containers | PASS |
| Startup, PostgreSQL, backend, Ollama/model, container inference | PASS |
| Generation → validation → controlled error → local repair → validation | Previously verified PASS in this session |
| Main UI, Water Shop, Coffee Shop, portfolio browser loads | Previously verified PASS with external page requests blocked |
| Physical Wi-Fi-disconnection verification | SKIPPED IN FINAL REGRESSION PASS |

Reproduce the inexpensive regression checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend_projects.test.cjs tests/frontend_runs.test.cjs tests/frontend_publications.test.cjs
docker compose config --quiet
docker compose ps
```

The earlier live run created project `25491a9c18d842d3b8b274172fe129cb` and repair
run `2099c0b6d92642edaa8441ab4d636577`. One real Ollama attempt changed `script.js`
and passed revalidation. Browser checks found no external page requests, failed
resources, JavaScript exceptions, or console errors in five tested page loads.
Coffee's two validator warnings were telephone/email links.

The more expensive `tests/check_offline_flow.py` creates another project and
repair run; it was not repeated in this final regression pass. Its browser checks
require `pip install -r requirements-dev.txt` in the virtual environment and Edge.
Evidence remains under ignored `runtime/offline/` and in PostgreSQL.
See [Offline audit](docs/OFFLINE_AUDIT.md) and [Submission audit](docs/SUBMISSION_AUDIT.md).

## Hermes Integration

Hermes remains an optional online gateway/router integration. The Compose service
is retained for inspection and explicit use. The local core needs neither its
container nor its Python package.

The legacy router uses port 8765 and requires separate provider configuration.
Select `AI_PROVIDER=hermes`, `CHAT_AI_ENGINE=hermes`, and `PROJECT_AI_ENGINE=hermes`
with the legacy router URL to use it. Stop the local helper before switching
repair engines so only one dispatcher claims queued runs.

## Codex Integration

`codex_adapter.py` supports optional online coding workflows through an installed
CLI and its private login. The legacy host path can select `AI_PROVIDER=codex`,
`CHAT_AI_ENGINE=codex`, and `PROJECT_AI_ENGINE=codex`. Local startup selects Ollama
again. Codex is not required for the university's core local workflow.

## Presentation Demo

1. Open this README; explain the objective and architecture.
2. Show `ollama --version` and `ollama list`: Llama 3.2 3B, approximately 2 GB.
3. Open `compose.yaml`: explain `db`, `backend`, optional `hermes`, and host Ollama.
4. Run `start_offline.ps1` using the command below; open the UI.
5. Send a local chat message, then create a small website with the water-shop prompt.
6. Show **Ollama / Llama 3.2 3B / Fallback used: No** and the three generated files.
7. Open the website at the preview URL and click **Validate** in Project Builder.
8. Explain **Fix Project** and show the saved repair run in **View Runs**.
9. Open the preserved Water Shop and Coffee Shop previews.
10. Show `docker compose ps`; explain PostgreSQL persistence and optional integrations.
11. Finish with the architecture and the stated verification limits.

```powershell
ollama --version
ollama list
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_offline.ps1
Start-Process 'http://localhost:8000/static/index.html'
Start-Process 'http://127.0.0.1:8050/0b59fd2ac45b45aabe3febf97060f464/index.html'
Start-Process 'http://127.0.0.1:8050/fd28085590c6494093c5a5ce20992d66/index.html'
docker compose ps
```

## Security

`.env`, credentials, virtual environments, runtime files, generated demos, browser
caches, and model weights are excluded from Git. `.env.example` contains public
placeholders. The backend and database publish loopback ports; generated websites
use a separate preview origin with external resources and form submission blocked.

A heuristic scan found no suspected real credentials in the proposed Git files.
A private-key header in a rejection test contains no key material. Review the
staged diff before committing; automated secret scans have limits.

## Known Limitations

- A 3B model can produce weak layouts or invalid code; complex repairs may require
  focused prompts. Validation does not guarantee runtime or design correctness.
- Initial downloads require Internet; optional online integrations may also require it.
- Physical disconnection was intentionally skipped in the final regression pass.
- Fresh-computer setup is documented but has not been tested on a second laptop.
- Presentation demos/history are local-only and absent from a fresh clone.
- The preview blocks network requests and form submission; contact forms are demos.
- Historical projects may contain external references and need separate review.
- Windows Hermes ACP was previously blocked by Device Guard/Smart App Control;
  it is not claimed working, and Windows security was not weakened.
- Floating Docker tags and model downloads can change versions on a new installation.

## Repository Structure

```text
app/                    FastAPI, persistence, provider transport, browser UI
compose.yaml            Core backend/db and optional Hermes
.env.example            Safe public configuration template
start_offline.ps1        Local startup and readiness checks
offline_host.py          Windows project/validation/repair helper
ollama_projects.py      Local model generation and repair
preview_local.py        Loopback website preview
hermes_*.py             Shared execution machinery and optional online adapters
codex_adapter.py        Optional online Codex adapter
validation/             Deterministic validator image and parsers
generated_projects/     Local generated websites (ignored)
runtime/                Checkpoints and local test evidence (ignored)
tests/                  Unit, frontend, and live acceptance checks
docs/                   Technical and submission evidence
```

## GitHub Repository

[Hermes Autonomous Coding Agent](https://github.com/Makni011/Hermes-Autonomous-Coding-Agent)

After reviewing the working tree and staged diff, commit and push manually:

```powershell
git status
git diff --check
git add .
git diff --cached --stat
git diff --cached
git commit -m "Finalize offline AI project and documentation"
git push origin main
```

No staging, commit, or push is performed by the startup or verification scripts.
