# Phase 6: safe static publication — offline readiness report

Implemented September 8, 2026. **No real GitHub, Pages or model request was made. Live publication is disabled by default.**

1. **Files created**

   - `app/publication_models.py`
   - `app/publication_export.py`
   - `app/publication_service.py`
   - `app/github_publication.py`
   - `app/routers/publications.py`
   - `app/migrations/v003_publications.py`
   - `hermes_publication_export.py`
   - `compose.publication.yaml`
   - `app/static/project-publications.js`
   - `tests/test_publications.py`
   - `tests/test_github_publication.py`
   - `tests/check_publications_postgres.py`
   - `tests/frontend_publications.test.cjs`
   - `docs/screenshots/phase6-publication-preview.png` (offline fixture)
   - This report.

2. **Files modified**

   `app/database.py`, `app/main.py`, `app/migrations/__init__.py`, `app/run_store.py`,
   `hermes_service.py`, `app/static/projects.js`, `app/static/projects.css`, `app/static/index.html`,
   `tests/check_run_monitor_browser.py`, `README.md`.
   The browser regression regenerated the two existing Phase 4 fixture screenshots.

3. **Publication architecture**

   Explicit preview captures the selected current revision through an authenticated host endpoint.
   Confirmation expires after ten minutes and repeats all validation and export checks.
   A unique repository reservation prevents duplicate concurrent confirmation.
   The backend background task receives the captured file mapping, never a command or arbitrary folder.
   GitHub operations consume that mapping; they do not edit the managed project.

   The host holds the existing exclusive repair filesystem lock during capture and rechecks the hash.
   A short project-row lock serializes snapshot admission with durable repair submission.
   This adds admission coordination only: the repair state machine, tool restrictions, validator,
   worker timeouts and installed Hermes are unchanged. Once the immutable copy exists, a later
   repair can proceed independently; publication still refers to the approved captured revision.

   Persisted transitions are:
   `preview → pending → exporting → repository_creating → uploading → deploying → completed`.
   Exceptions produce `failed` with a fixed safe category. Upload records the commit before updating
   the remote branch so ambiguous failures retain useful reconciliation metadata.

4. **Credential handling**

   The optional Compose override mounts an external credential file only into the backend at
   `/run/secrets/github_publish_token`. Do not put its contents in `.env`, project files or prompts.
   `GITHUB_PUBLISH_CREDENTIAL_FILE` contains only the external file path.
   Hermes and the host export endpoint never receive the credential or GitHub request headers.
   The adapter disables redirects, environment proxy/netrc use and automatic mutation retries.
   Exceptions, response bodies and authorization headers are not logged or persisted.

   Publication POSTs require the configured exact `PUBLICATION_ORIGIN` (default
   `http://localhost:8000`) as both browser Origin and request origin. The existing application is
   local, not a multi-user authenticated publishing service; do not expose it directly to the internet.
   The host handoff retains the existing shared-token authentication.

5. **Export allowlist**

   UTF-8 `.html`, `.css`, `.js`, `.json`, `.svg`, `.txt`; required `index.html`.
   Existing limits remain 100 files, 512,000 bytes per file and 5,000,000 total bytes.
   Reject traversal, absolute/drive paths, sensitive names, links/reparse points/hardlinks,
   unsupported files, runtime/checkpoint/log folders, package manifests and generated workflows.
   Known token/private-key patterns and suspicious credential assignments are rejected.
   Application-owned `.nojekyll` is added after validating the captured artifact hash.

6. **Validation gate**

   Managed completed project; requested SHA-256 revision; a persisted `static-web-v1` passed
   validation for that project/revision with confirmed cleanup; no retained active repair reservation;
   no filesystem repair lock; captured bytes match the selected revision twice.
   Old validation cannot authorize changed files. A model's success statement is never consulted.

7. **GitHub adapter**

   Personal account repositories only, fixed name `OWNER/managed-PROJECT_UUID`.
   The authenticated GitHub login must match OWNER. First publication refuses any existing repository.
   New repositories are public and bootstrapped with a fixed empty `.nojekyll` file.
   Updates select only a repository with this project's previously completed publication record,
   and require its `gh-pages` head to match the previously recorded commit.
   A complete replacement Git tree contains only the approved artifact plus `.nojekyll`.
   Commit creation and non-forced reference updates use fixed REST operations, never git/shell commands.
   No repository deletion, force push or automatic mutation retry is implemented.

8. **Pages deployment**

   The application configures branch `gh-pages`, root `/`, build type `legacy` via the Pages API.
   Repository Actions are disabled; `.nojekyll` bypasses Jekyll and publishes static content directly.
   No generated workflows, installation steps or package scripts run.
   Completion requires a built Pages result for the exact uploaded commit, the expected Pages source,
   the expected HTTPS URL and no custom domain. A build for another commit cannot count as success.
   Polling is limited to 24 reads, with a 240-second adapter budget and bounded HTTP timeouts.

   GitHub documents this static deployment path in
   [the Pages worker announcement](https://github.blog/changelog/2024-07-08-pages-legacy-worker-sunset/),
   [Pages REST API](https://docs.github.com/en/rest/pages/pages), and
   [Git references API](https://docs.github.com/en/rest/git/refs).

9. **Database migration**

   Additive migrations 3 through 5 create publication and reconciliation metadata; migrations 1 and 2
   and repair records are preserved.
   Fields include id, project id, revision, provider, repository, unique active repository reservation,
   commit SHA, status, public URL, created/finished timestamps, failure category, safe summary,
   approved file names and timestamped state events. No credential or generated file contents are stored.
   Migration repeatability and concurrent admission were exercised in disposable PostgreSQL schemas.

10. **Frontend**

    Projects now includes Publish to Cloud, GitHub account input, preview and a separate confirmation.
    Preview shows project name, exact revision, validated/cleanup status, files and public repository.
    History shows persisted state progression, repository, commit, safe failure and verified URL.
    No automatic publication after repair. Text stays inert, URL schemes/hosts are constrained,
    duplicate clicks are guarded, polling stops at terminal state or disposal and pauses after five failures.
    Retrying requires a new preview/confirmation and creates a fresh publication record.

11. **Offline verification**

    - Python discovery: **100 tests, 99 passed, one existing Windows symlink-creation skip**.
    - PostgreSQL durable repair tests: **18 passed**.
    - PostgreSQL publication/migration/concurrency tests: **16 passed**.
    - Real Docker validator: **7 passed**, including network denial, read-only snapshot/root,
      malformed JS diagnostics, valid static website, timeout and cleanup.
    - Real Docker validator plus mocked Hermes repair: **4 passed**.
    - Synthetic worker cancellation/deadline process cleanup: **2 passed**.
    - Existing Projects JavaScript harness and **15 run-monitor scenarios passed**.
    - Publication JavaScript harness passed confirmation, duplicate, text, URL and polling controls.
    - Real Edge with all requests intercepted passed monitor regression and publication preview/confirmation.
    - Installed Hermes offline constructor/restricted repair checks and four Codex path checks passed.

    The first Docker test attempt lacked sandbox access to the engine and reported an unavailable image.
    Rerunning with local Docker access passed all seven tests; no validator changes were made.
    Phase 5's live acceptance record and generated project were not changed or rerun.

    Example mocked successful history:

    ```json
    {
      "provider": "github-pages",
      "status": "completed",
      "repository": "example/managed-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "commit_sha": "cccccccccccccccccccccccccccccccccccccccc",
      "public_url": "https://example.github.io/managed-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/",
      "failure_class": null,
      "states": ["pending", "exporting", "repository_creating", "uploading", "deploying", "completed"]
    }
    ```

    Failure categories: `authentication_failure`, `repository_failure`, `upload_failure`,
   `deployment_failure`, `reconciliation_failure`, `invalid_revision`, `validation_required`,
   `security_violation`, `network_failure`.

14. **Controlled reconciliation**

   A failed publication may be reconciled only when its persisted repository, approved validated
   revision, recorded commit, `gh-pages` head and Pages source still match. The operation performs
   no export, upload, tree creation, commit creation, ref update or repository creation. It requests
   at most one `POST /repos/{owner}/{repo}/pages/builds`, then polls the exact commit, Pages state
   and public URL within the existing bounded adapter budget. A missing build remains pending until
   the deadline; a successful deployment record alone cannot complete reconciliation. The original
   `deployment_failure` publication status remains unchanged, while reconciliation status and events
   are stored separately.

   GitHub documents this endpoint as building the latest revision on the repository default branch.
   Because this project serves `gh-pages`, exact-commit verification is intentionally retained and
   reconciliation fails safely if GitHub reports a different revision.

15. **Actions Pages migration design**

   The offline migration path is disabled unless `GITHUB_ACTIONS_MIGRATION_ENABLED=1`. It reuses the
   existing repository and publication row, validates the approved revision, cleanup proof, repair
   reservation, repository identity, `gh-pages` head and current Pages source before any future mutation.

   The only workflow accepted by the adapter is the application-owned constant at
   `.github/workflows/hermes-pages.yml`. It is never taken from generated project files. The fixed
   workflow accepts only an application-supplied 40-character artifact SHA, checks out that SHA,
   verifies `git rev-parse HEAD`, uploads the checked-out static tree with `actions/upload-pages-artifact`,
   and deploys it with `actions/deploy-pages`. It executes no generated build script or package command.

   The future remote sequence is bounded and non-destructive: install the fixed workflow on the default
   branch with one normal contents commit, configure Pages to `workflow`, dispatch with the approved
   artifact SHA, then verify the workflow run, Pages deployment, exact workflow/deployment SHA and public
   URL. No force push, artifact commit rewrite, generated workflow, repository creation or second
   publication record is used. The original publication and reconciliation events remain preserved;
   Actions migration metadata is stored separately.

   Official Actions requirements are `contents: read`, `pages: write` and `id-token: write` in the
   deployment job, with the `github-pages` environment. The backend credential needs repository content
   write for the application-owned workflow commit, Actions workflow dispatch access, Pages settings
   administration, and read access to workflow runs and Pages deployment status. Credentials remain
   backend-only. No live Actions migration, workflow upload, Pages setting change or dispatch has run.

12. **Limitations**

    - Current managed revision only; historical checkpoint selection/export is not implemented.
    - Validation must be recorded by a durable run. Standalone Validate results are not persisted as publication proofs.
    - Binary images remain unsupported because the unchanged Phase 1 workspace/validator supports text files.
    - Secret pattern checks cannot recognize every arbitrary secret; review the publication preview and source before approval.
    - Personal GitHub accounts only, public dedicated repositories, no custom domains or unmanaged repo adoption.
    - The task uses a bounded in-process background worker, not a new durable queue. If the backend dies,
      the last state and repository reservation remain; there is no automatic replay. Stop the backend,
      reconcile the remote repository/commit/Pages state, then explicitly resolve the reservation before retry.
    - A failed first publication can leave a repository; a failed update can leave an uploaded commit.
      Retry refuses ambiguous/unmanaged remote state instead of deleting or force-overwriting it.
    - HTTP timeouts bound connection/read inactivity; they are not an OS-level process kill deadline.
    - Production services have not been restarted for this phase. Migration 3 runs on backend startup;
      restart the host too so the authenticated export endpoint is available.
    - Real GitHub permissions, repository creation and Pages availability remain the acceptance gate.

13. **Readiness and next authorized step**

    Offline implementation is ready for one controlled cloud acceptance test **after explicit approval**.
    No token was requested, installed or exposed during implementation. No live switch was enabled.

    After approval: provision the backend-only credential with the necessary repository/content/Pages/
    Actions-settings permissions; confirm the GitHub personal account; reload host/backend with migration 3
    and the optional secret mount; enable `GITHUB_PUBLICATION_ENABLED=1`; select one validated disposable
    managed revision; review the fixed public repository and files; confirm once; observe the recorded
    commit and matching Pages build/URL; verify the managed revision is unchanged. Stop on failure without
    destructive retries. Do not start another phase or publish additional projects automatically.
