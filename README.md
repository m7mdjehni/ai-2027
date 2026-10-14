# Kiln Autonomous Project Builder

Kiln turns a written brief into a local website project. The Windows helper uses
Ollama and `qwen2.5:1.5b` to generate files, runs deterministic validation in a
network-disabled Docker container, and supports bounded repair runs whose events
and changed files are visible in the browser.

## Product Flow

```text
Project brief -> local generation -> saved files -> isolated validation
              -> inspect diagnostics -> bounded local repair -> revalidation
              -> separate local website preview
```

The application UI and API focus on project building. Chat, Tasks, and Notes are
not exposed. Their ORM table definitions remain so startup does not drop tables
from an older PostgreSQL volume. Static validation does not prove design quality
or every browser interaction.

## Architecture

- FastAPI serves `app/static/builder.html` and stores projects, runs, validation
  results, and events in PostgreSQL.
- PostgreSQL runs in Docker Compose with persistent volume `pgdata`.
- `offline_host.py` runs on Windows at `127.0.0.1:8766`; it calls local Ollama,
  manages project files, and coordinates validation and repair.
- Ollama runs locally at `127.0.0.1:11434`; requests randomly select either
  `qwen2.5:1.5b` or `llama3.2:3b` from the installed local models.
- The validator uses cached image `hermes-validator-web:phase1` with networking
  disabled. `preview_local.py` serves generated sites on `127.0.0.1:8050`.
- Browser assets are local files under `app/static/`; no CDN, hosted font,
  remote image, analytics, or remote frontend dependency is required.

Generated projects use a separate preview origin. The backend displays source
as inert text; use **Open local preview** to run a project. Preview pages block
external requests and form submission.

## Local Model

Install Ollama and download the model once:

```powershell
ollama pull qwen2.5:1.5b
ollama pull llama3.2:3b
ollama list
```

Both models must be installed before disconnecting from the Internet.
`OLLAMA_MODELS` can optionally override the built-in comma-separated local model
pool. The application randomly selects a model for each chat, generation, and
repair inference. Its startup check fails explicitly if any configured model is
missing; it does not download models or fall back to a cloud provider.

Set these values in the environment of the Ollama server process:

```powershell
$env:OLLAMA_CONTEXT_LENGTH = '2048'
$env:OLLAMA_KV_CACHE_TYPE = 'q4_0'
$env:OLLAMA_NUM_PARALLEL = '1'
ollama serve
```

If Ollama is managed by its desktop app or startup service, configure the same
values for that process and restart it. A separate PowerShell environment does
not change an already-running server. The project client sends `num_ctx=2048`
and limits generated output to 1024 tokens. Kiln's application UI, assets, API,
validation and project preview are local; the preview blocks outbound requests.

## Setup

Requirements: Windows, Git, Python 3.10+, Node.js for frontend tests, Docker
Desktop with its Linux engine, and local Ollama. First-time setup needs Internet
to download software, packages, container images, and the model. Runtime
inference and browser assets remain local.

Clone the repository, then run the setup commands from its directory:

```powershell
git clone https://github.com/m7mdjehni/ai-2027.git
Set-Location ai-2027
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For a new installation only, generate private values without printing them:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; import secrets; p=Path('.env'); s=p.read_text(); s=s.replace('REPLACE_WITH_A_UNIQUE_DATABASE_PASSWORD', secrets.token_hex(24)); s=s.replace('PROJECT_BUILDER_TOKEN=\n', 'PROJECT_BUILDER_TOKEN='+secrets.token_hex(32)+'\n'); p.write_text(s)"
```

Preserve an existing `.env`. Its PostgreSQL password must match the role in the
existing `pgdata` volume; changing only the file does not change the database
role. Never use `docker compose down -v` to resolve a credential mismatch.

Cache the images during setup:

```powershell
docker pull postgres:15
docker compose build backend
docker build -f validation/Dockerfile.web -t hermes-validator-web:phase1 .
docker compose config --quiet
```

## Start

Start Docker Desktop and Ollama, then run from the repository directory:

```powershell
.\start_offline.ps1
```

The script uses cached images only (`--pull never --no-build`), starts the
backend and database, starts the Windows helper and preview if needed, and
checks the builder page. It does not download models or change Windows security
settings.

Open **http://localhost:8000/static/builder.html**.

The helper and preview can also be started in separate PowerShell terminals:

```powershell
.\.venv\Scripts\python.exe offline_host.py
```

```powershell
.\.venv\Scripts\python.exe preview_local.py
```

Do not use a generic static server as the application server. A static-only
origin returns HTML 404 pages for `/api/...`; the builder reports that
content-type mismatch and directs users to FastAPI. A visual-only static server
does not provide the backend API.

## Build a Project

1. Open the builder and enter a specific website brief, including its purpose,
   audience, visual style, and requested pages or sections.
2. Generate the project. Kiln saves the generated files under
   `generated_projects/` and records the project and run history in PostgreSQL.
3. Review the generated source in the builder. Open **Open local preview** to
   view the website on its separate local origin.
4. Select **Validate** to run deterministic checks in the isolated validator.
   Review the diagnostics and run events in the project monitor.
5. If needed, select **Fix Project** to request a bounded local repair. Review
   the changed files and validation result; repeat validation as appropriate.

The preview is separate from the builder: the builder displays project source
as inert text, while the preview serves the generated website and blocks
outbound requests and form submission. See [docs/DEMO.md](docs/DEMO.md) for a
guided presentation flow.

## Data and Safety

- `.env` contains private database and helper credentials; keep it local.
- Compose publishes the backend and PostgreSQL on loopback only.
- `pgdata` stores database records. Project files and repair checkpoints live in
  `generated_projects/` and `runtime/`.
- Validation copies project files into a bounded, read-only, network-disabled
  container; it does not mount the repository or `.env`.
- Project generation and repair use the local model; there is no cloud-model
  fallback in the project builder.
- Do not use `docker compose down -v`; it deletes the database volume.

## Tests

Run local unit and frontend suites from the repository directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend_projects.test.cjs tests/frontend_runs.test.cjs
git diff --check
```

`tests/check_offline_flow.py` creates and retains a new project, exercises helper
inference, PostgreSQL persistence, validation, bounded repair, and local browser
assets. Run it only when Docker, PostgreSQL, backend, helper, preview, and the
validator image are available. Never pass `--internet-disconnected` unless you
personally disconnect all network uplinks; this project makes no physical
offline-test claim.

## Current Verification Note

Historical verification on 30 September 2026: the D: copy passed 189 Python
tests (188 passed, one skipped) and both current frontend suites, including 17 mocked Run Monitor
scenarios. Ollama `llama3.2:3b` returned a
real local response at context 2048; helper chat returned JSON and a single-file
generation request succeeded without writing files. The builder rendered from
local assets at desktop and mobile sizes without horizontal overflow.

Full application verification is blocked on this machine: Docker Desktop's
Windows control/status commands time out, the backend does not answer on port
8000, and the D: `.env` password is rejected by the existing PostgreSQL volume.
The D: helper on port 8766 and preview on port 8050 returned HTTP 200 JSON. Do
not reset Docker/WSL storage; restart Docker Desktop through its UI, then
reconcile the D: `.env` password with the existing database role before running
`start_offline.ps1` again. Live project validation and database persistence were
not verified during this migration.

## Repository Map

```text
app/                    FastAPI project API, persistence, and local UI assets
app/static/builder.html  Project-first browser experience
compose.yaml             PostgreSQL and FastAPI services
start_offline.ps1        Local readiness checks and startup
offline_host.py          Windows Ollama/project/validation helper
ollama_projects.py       Local generation and repair
preview_local.py         Isolated loopback project preview
validation/              Offline deterministic validator image
generated_projects/      Local generated websites (ignored)
runtime/                 Local checkpoints and test evidence (ignored)
tests/                   Unit, browser, and live acceptance checks
docs/                    Engineering notes and historical phase records
```