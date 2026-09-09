# Troubleshooting Log

## Project Builder Phase 1 checks (2026-09-07)

- The first `docker compose up --build -d backend` failed when pip reported no
  available versions for `SQLAlchemy==2.0.29`. Retrying `docker compose build backend`
  downloaded and installed the same pin successfully; no dependency change was needed.
  `docker compose up -d backend` then started the updated backend and created `projects`.
- The installed-Hermes offline smoke test initially passed its assertions but could
  not delete a temporary Windows log lock. Calling `logging.shutdown()` before
  cleanup resolved it. Subsequent smoke tests passed without provider calls.
- The Windows account cannot create symlinks in the offline test environment, so
  that test is skipped. Hard-link rejection and mocked reparse-point checks pass.

Document real errors encountered during development here. Do NOT invent errors.

Format:
- Error:
- When it happened:
- Cause:
- Solution:
- Command used:
- Result:

Add entries progressively as you run and test the project.

## Dependency Resolution Failure

- Error: `ERROR: Could not find a version that satisfies the requirement SQLAlchemy==2.1.0` and `ERROR: No matching distribution found for SQLAlchemy==2.1.0`.
- When it happened: During `docker compose build`, at the Dockerfile step `RUN pip install --no-cache-dir -r requirements.txt`.
- Cause: `SQLAlchemy==2.1.0` was an unavailable stable release pin. The project uses Python 3.11 in `python:3.11-slim`, and SQLAlchemy 2.0.29 is the compatible stable 2.0.x release selected for this project.
- Solution: Changed the requirement to `SQLAlchemy==2.0.29`. Reviewed all direct pins; the selected versions resolve from PyPI. Updated the OpenAI adapter to use the supported `openai==1.29.0` client API.
- Command used: `docker compose build`
- Result: The dependency resolution check completed successfully for all pinned direct dependencies. A local Python compilation check also completed successfully. Re-run the Docker build to validate the image in Docker Desktop.

## PostgreSQL Readiness Failure

- Error: `sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "db", port 5432 failed: Connection refused`; the backend exited with code 3 during `docker compose up`.
- When it happened: During backend startup after the Docker image had built successfully.
- Cause: `depends_on` previously controlled only container start order. PostgreSQL's container could be running while the database server was still initializing, and FastAPI called `Base.metadata.create_all()` immediately without retry logic.
- Solution: Added a PostgreSQL `pg_isready` healthcheck and changed the backend dependency to `condition: service_healthy`. Added bounded retries around SQLAlchemy table creation. The backend continues to use `POSTGRES_HOST=db` and `POSTGRES_PORT=5432`; database name, user, and password are passed from the same `.env` values to both services.
- Command used: `docker compose down --volumes --remove-orphans`, followed by `docker compose up --build -d`.
- Result: Verified on 2026-08-26. `docker compose ps` showed the database `Up (healthy)` and the backend `Up`; the database health status was `healthy`; backend logs showed `Application startup complete`; `http://localhost:8000/` and `/static/index.html` returned HTTP 200; PostgreSQL contained the `conversations`, `notes`, and `tasks` tables.

## Chat Response Displayed as `undefined`

- Error: The UI displayed `Assistant: undefined` after submitting a chat message.
- When it happened: During browser testing of the chat form on 2026-08-26.
- Cause: Inspection showed that the current API already returns `{"reply": "..."}` and the current frontend already reads `data.reply`; there was no field-name mismatch in the inspected source. The frontend did not check failed HTTP responses or missing/empty JSON fields, so an error-shaped response could be rendered as though it were a reply. A stale browser/container asset could also make the symptom appear when the source contract already matched.
- Solution: Kept `reply` as the single response field, added frontend `res.ok` and response-content validation, and display the backend `detail` message for API errors. The backend now returns HTTP 502 with a clear message when the OpenAI request fails or returns no content.
- Command used: `docker compose up --build -d`; then `Invoke-WebRequest -Method Post -Uri http://localhost:8000/api/converse -ContentType 'application/json' -Body '{"message":"Hello"}'`.
- Result: After `docker compose up --build -d`, the direct POST returned HTTP 502 when the OpenAI request failed, while the backend remained running and the root API returned HTTP 200. The frontend now displays the API error detail or a fallback error message and rejects an absent/empty `reply`, so it cannot display an absent reply as `undefined`.

## OpenAI SDK and HTTP Client Incompatibility

- Error: `Client.__init__() got an unexpected keyword argument 'proxies'`.
- When it happened: During an OpenAI request from the backend on 2026-08-26.
- Cause: The image contained `openai==1.29.0` with `httpx==0.28.1`. That older OpenAI SDK passed the removed `proxies` argument to the newer HTTP client.
- Solution: Upgraded the OpenAI SDK to `openai==3.3.1`, updated `app/ai.py` to use `OpenAI(...).responses.create(...)`, and selected `gpt-4o-mini`. The clean Docker build also required changing `pydantic==1.10.11` to `pydantic==1.10.15`, because OpenAI 3.3.1 requires Pydantic 1.10.13 or newer. The new image uses the SDK's compatible `httpx2==2.12.0` transport.
- Dependency versions before: `openai==1.29.0`, `httpx==0.28.1`, `pydantic==1.10.11`.
- Dependency versions after: `openai==3.3.1`, `httpx2==2.12.0` (provided by the OpenAI SDK), `pydantic==1.10.15`.
- Command used: `docker compose build --no-cache`; then `docker compose up -d --force-recreate`.
- Result: Docker pip installation completed successfully. The rebuilt container reported `openai=3.3.1` and `httpx2=2.12.0`; an OpenAI model lookup succeeded and returned `gpt-4o-mini`. A final chat-completion response must still be recorded after a successful `/api/converse` request; no secret was printed.

## OpenAI Error Classification Logging

- Change: The backend previously returned only a generic HTTP 502 for every OpenAI exception.
- Solution: `app/ai.py` now logs a safe category, exact exception class, HTTP status, provider error code, and sanitized message. Categories include `authentication_error`, `rate_limit_or_quota_error`, `model_not_found`, `invalid_request`, `network_error`, `sdk_incompatibility`, and `openai_api_error`. The API detail contains the same category and sanitized message. API keys are redacted and never logged.
- Reproduction command: `docker compose exec backend python -c "import os; print(bool(os.getenv('OPENAI_API_KEY')))"`, followed by a POST to `/api/converse` and `docker compose logs backend --tail=100`.
- Result: Verified after rebuilding on 2026-08-26. The request produced `category=rate_limit_or_quota_error`, `exception_type=RateLimitError`, `status_code=429`, and provider code `insufficient_quota`. The safe message stated that the current quota was exceeded and the plan/billing details should be checked. This is a quota/billing problem, not a 401 authentication error, model-not-found error, invalid request, SDK incompatibility, or network error. The API now returned HTTP 429 with the user-friendly detail `OpenAI API quota is unavailable. Please check billing or credits.`

## OpenAI and Pydantic Dependency Conflict

- Error: Docker dependency installation failed with `ERROR: Cannot install ... because these package versions have conflicting dependencies`.
- When it happened: During the clean Docker rebuild after changing `openai` from `1.29.0` to `3.3.1`.
- Cause: `openai==3.3.1` requires `pydantic>=1.10.13`, but `requirements.txt` still pinned `pydantic==1.10.11`.
- Solution: Changed the Pydantic pin to `pydantic==1.10.15`. This satisfies the OpenAI requirement while remaining within FastAPI 0.100.0's supported Pydantic 1.x range.
- Command used: `docker compose build --no-cache`.
- Result: The first build correctly rejected the incompatible set. Re-run the clean build after this correction; record the successful pip installation and subsequent API result here.
