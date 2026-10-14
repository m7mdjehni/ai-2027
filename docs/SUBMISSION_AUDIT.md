# University submission audit

Updated 28 September 2026. This replaces the earlier online-default audit.

## Current architecture

Browser -> FastAPI (Docker) -> host Ollama / randomly selected local Qwen 2.5 or
Llama 3.2 for chat.
FastAPI -> authenticated local Python helper on port 8766 for generation,
network-disabled Docker validation, and local Ollama repair. PostgreSQL 15
stores application data and run history. Preview runs on loopback port 8050.
Hermes and Codex are optional online integrations.

## Tested status

- PASS: 189 Python tests run, 188 passed, one skipped.
- PASS: all three frontend test scripts, including 17 Run Monitor scenarios.
- PASS: offline startup checks, Compose configuration, healthy PostgreSQL,
  backend HTTP, backend-to-Ollama inference, local helper, preview, Web UI.
- PASS: actual local chat, generation, validation, deliberate syntax failure,
  one local Ollama repair, and final validation.
- PASS: Water Shop, Coffee Shop, and preserved portfolio validation and Edge
  rendering with external page requests blocked. Desktop screenshots inspected.
- SKIPPED BY USER REQUEST: physical Internet disconnection in final regression.
- NOT TESTED: fresh-machine installation, optional online providers, ACP, publication.

See [Offline audit](OFFLINE_AUDIT.md) for current project/run IDs and evidence.
See [README](../README.md) for setup, demo URLs, and presentation instructions.

## Security and GitHub

The current file scan found no suspected real credentials, model binaries, private
configuration files, runtime artifacts, or files over 10 MB in the proposed Git file
set. A private-key header is an intentional test fixture without key material.
`.env.example` remains included; `.env` is excluded. A heuristic scan has limits.

All previous working changes and presentation websites were preserved. Nothing
was staged, committed, or pushed. Review `git diff` and the new files before your
manual staging, then inspect `git diff --cached` before committing.

## Final local regression

The final pass reran Python (189 tests, one skipped), all three frontend scripts,
Compose validation, and local startup successfully. No new AI project was generated;
the earlier real Ollama generation/repair and demo browser evidence remains valid.
Startup now checks the helper token and follows BACKEND_PORT. Compose comments
make the host Ollama and optional Hermes architecture explicit. README includes
fresh-computer setup, private credential generation, model/image caching, and a
concise presentation sequence. No network changes or physical-disconnection tests
were performed in this final pass.

The repository is ready for manual review and submission under the revised scope.
Physical offline PASS and fresh-machine installation are not claimed.
