# Troubleshooting

## Builder shows an HTML/JSON error

Open the UI through FastAPI at `http://localhost:8000/static/builder.html`.
The project API uses same-origin `/api/...` requests. A generic static server can
serve the HTML and JavaScript but has no API routes; it returns an HTML 404 page
instead of JSON. The builder detects the content-type mismatch and explains the
required application origin. The separate website preview on port 8050 is not
the application API.

## Docker or backend does not respond

`start_offline.ps1` expects Docker Desktop's Linux engine and cached images to be
available. Check Docker Desktop in its UI and use its supported restart action if
the CLI/status endpoint is unresponsive. Do not force-kill WSL processes, run
`wsl --shutdown`, move Docker VHDX files, or use `docker compose down -v` as a
troubleshooting shortcut.

If PostgreSQL reports healthy but the backend cannot connect, the password in
`.env` may not match the existing role in `pgdata`. Updating `.env` alone does
not update that role. Preserve the volume and reconcile the credential through
database administration; never recreate the volume merely to change a password.

## Ollama returns an error

Confirm the local service and model with:

```powershell
ollama list
ollama ps
```

Confirm `ollama list` shows both `qwen2.5:1.5b` and `llama3.2:3b`. Set
`OLLAMA_CONTEXT_LENGTH=2048`,
`OLLAMA_KV_CACHE_TYPE=q4_0`, and `OLLAMA_NUM_PARALLEL=1` in the Ollama server
process environment. The client uses the configured context (2048 by default)
and a 1024-token output cap. Restart the server after changing process settings.

## Current D: verification limitation

During verification on 30 September 2026, Docker Desktop's Windows CLI/status
commands timed out. PostgreSQL's D: `.env` password was rejected by the existing
volume, the backend did not answer on port 8000, and `docker compose config
--quiet` also failed to return. The helper on port 8766 and preview on port 8050
returned HTTP 200 JSON. Docker/WSL storage was not reset or moved.

The static-only preview on port 8767 returned `404 text/html` for
`GET /api/projects`; this is expected because it is not FastAPI. The production
browser URL remains `http://localhost:8000/static/builder.html`.

No physical Internet-disconnection test was performed. It remains pending the
user's manual test.