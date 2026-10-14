# Kiln Demo Plan (5-10 minutes)

1. Start Docker Desktop and Ollama; confirm `ollama list` shows both
   `qwen2.5:1.5b` and `llama3.2:3b`.
2. Run `start_offline.ps1` from the repository directory.
3. Open `http://localhost:8000/static/builder.html`.
4. Enter a specific brief, such as a responsive coffee shop with Home, Menu,
   and Contact sections, then generate the project.
5. Inspect the saved files and open the separate local website preview.
6. Run **Validate** and show the persisted result in the project run monitor.
7. If diagnostics exist, run **Fix Project** and inspect changed files and
   revalidation status.
8. Explain that PostgreSQL stores project/run history, Ollama stays local, and
   the preview blocks outbound requests.

Do not claim a physical no-Internet result unless the user has disconnected all
network uplinks and run the separate manual acceptance procedure.