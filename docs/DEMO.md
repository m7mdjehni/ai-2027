# Demo Plan (5-10 minutes)

1. Show laptop/environment screenshots (30s)
2. Show project structure in VS Code (30s)
3. Start Docker Compose (30s)
   - `docker compose up --build -d`
4. Show running containers: `docker compose ps` (10s)
5. Open UI at `http://localhost:8000/static/index.html` (30s)
6. Type a question and show assistant reply (1-2 min)
7. Create a task and a note via UI (30s)
8. Show database rows via `psql` or a GUI (30s)
9. Show logs: `docker compose logs backend --tail=50` (20s)
10. Stop services: `docker compose down` (10s)

Keep timings tight; explain architecture while actions run.
