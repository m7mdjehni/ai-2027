# Offline conversion audit

Historical acceptance and model details below describe the run on 28 September
2026; the current local model pool randomly selects Qwen 2.5 or Llama 3.2.

## Dependency map before edits (28 September 2026)

- Browser assets: `app/static/`; relative API requests, no required CDN found.
- Chat: `conversation.py` -> `app.ai.call_assistant`; default `AI_PROVIDER=hermes`
  -> host `/route` -> Hermes router / Codex. Ollama was an explicit alternative.
- Builder: backend `/api/projects` -> authenticated host `/projects/build`
  -> `hermes_projects.build_project`; default `PROJECT_AI_ENGINE=codex`.
- Validation: authenticated host -> `hermes_validation` -> cached Node parser
  image with network disabled; no AI inference or package installation at runtime.
- Repair: database queue -> host dispatcher -> bounded `hermes_repair` loop
  -> Codex by default, Hermes otherwise. Attempts incorrectly hard-coded Codex metadata.
- Generated files: host writes; backend mounts read-only and exposes text only.
- PostgreSQL: Compose named volume `pgdata`; must be preserved.
- Hermes Compose service: no profile, started unnecessarily by default.
- Coffee demo `fd28085590c6494093c5a5ce20992d66`: Google Fonts CSS import.
- Publication is an optional online GitHub operation, outside the core demo.

## Implementation decision

Retain the authenticated Windows host helper for workspace writes, durable repair,
and Docker validation. In offline mode it uses project Python and Ollama HTTP;
it does not need the Hermes package, Hermes CLI, Codex CLI, or cloud credentials.
Keep the existing online adapters. Use a separate offline helper port (8766),
so an existing online helper on 8765 does not silently select the wrong provider.
Backend chat calls Windows Ollama directly through Docker Desktop networking.

Physical Wi-Fi-disconnection verification: **SKIPPED IN FINAL REGRESSION PASS**
by user request. Local inference is not a physical offline PASS claim.

## Current acceptance: 28 September 2026

The previous implementation was reviewed and preserved. The final review corrected
one remaining Hermes-specific connection-error label and preserved Ollama HTTP
status codes so a missing model produces the correct user-facing error. A new
regression test confirms this failure does not call the online router.

| Check | Result | Evidence |
|---|---|---|
| Python regression suite | PASS | 189 run, 188 passed, one skipped |
| Frontend regression suite | PASS | Three scripts, including 17 mocked Run Monitor scenarios |
| Offline startup | PASS | Existing images; no pulls/builds; all readiness checks passed |
| Compose configuration | PASS | `docker compose config --quiet`; private values withheld |
| Docker services | PASS | Backend running; PostgreSQL healthy |
| Ollama | PASS | CLI 0.34.4; llama3.2:3b, a80c4f17acd5, 2.0 GB |
| Backend to Ollama | PASS | Real inference from inside backend container |
| Chat and persistence | PASS | Local API reply; tasks/notes created and retrieved |
| Local generation | PASS | Ollama / llama3.2:3b; fallback false; three real files |
| Controlled repair | PASS | Clean PASS, intentional JS FAIL, one Ollama repair, final PASS |
| Water/Coffee/preserved portfolio | PASS | Local validator and desktop Edge load checks |
| Main UI and generated project | PASS | Edge with external page requests blocked |
| Physical Internet disconnection | SKIPPED | By user request in final regression pass |
| Optional Hermes/Codex online inference | NOT TESTED | Not required or invoked in this acceptance |

New project: `25491a9c18d842d3b8b274172fe129cb`.
Durable repair run: `2099c0b6d92642edaa8441ab4d636577`.
The recorded attempt identifies `ollama` / `llama3.2:3b`, changing `script.js`.
Evidence: ignored `runtime/offline/local-connected.json` and browser screenshots.
Earlier repair records remain in PostgreSQL and checkpoint directories.

Water Shop `0b59fd2ac45b45aabe3febf97060f464`, Coffee Shop
`fd28085590c6494093c5a5ce20992d66`, and portfolio
`024b7d588ddd4c579bac7ee7351b07a2` were not edited during this final review.
Screenshots at a 1440-pixel desktop viewport were inspected. Content is readable;
Water Shop and the 3B portfolio use simple layouts and placeholder content.
The five browser loads had no external requests, failed resources, JavaScript
exceptions, or console errors. Coffee's validator warnings refer to `tel:` and
`mailto:` links, not resources needed to display the page.

Docker Desktop was initially stopped and was started from its existing installation.
PowerShell's saved execution policy blocked direct script execution. The tested
fallback is `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_offline.ps1`;
this changes policy only for that PowerShell process. No saved policy was changed.

The earlier malformed multi-file generation and no-progress repairs remain factual
limitations. Per-file generation and focused line repair improved the tested case;
they do not guarantee that every model output or repair will succeed.

## Historical physical-disconnection attempt (before final scope change)

After the user reported Wi-Fi off, offline startup passed again, including local
inference and healthy core services. The disconnected acceptance test then stopped
at its preflight: a public TCP connection to `1.1.1.1:443` succeeded. Windows also
reported Wi-Fi IPv4 connectivity as `Internet`. Physical isolation therefore
failed verification; no full offline PASS is claimed. Network settings were not
changed. Evidence is retained in `runtime/offline/disconnected.json`. The user
subsequently excluded physical testing from finalization. No further disconnection
checks are required for this submission; the final status is SKIPPED BY USER REQUEST.

## Submission scan results

Reviewed tracked and untracked nonignored files, ignore rules, and diffs. A heuristic
scan of 132 text files found only a private-key header in the deliberate rejection
fixture in `tests/test_publications.py`; no key material is present. No files over
10 MB or private environment/runtime files were selected by Git's file listing.
The staged file list is empty. No commit or push was performed.

## Final local regression under revised scope

Physical testing was skipped by user request. Python tests were rerun (189 run,
188 passed, one skipped), and all three frontend scripts passed again. Final
startup passed using cached images and existing services. No expensive generation
or repair was repeated. The existing demos and successful repair history remain
available through the local HTTP services.

Both current and example Compose configurations passed, with default services
limited to `db` and `backend`; the `online` profile includes Hermes. Expanded
configuration was held in memory and never printed. All 81 Python source files,
the frontend JavaScript files, and PowerShell startup passed syntax checks.
Documentation links resolved. README credential generation was tested on a
temporary copy of `.env.example`, including preservation of existing values.
The temporary directory was removed automatically; the real `.env` was unchanged.

Final local HTTP/history evidence: `runtime/offline/final-local-regression.json`.
The security scan again covered 132 text files with only the known rejection-test
fixture match. `git diff --check` passed, and no files were staged or committed.
