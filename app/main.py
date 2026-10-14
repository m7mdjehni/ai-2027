import os
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.ai import call_assistant
from app.routers import projects
from app.routers import runs
from app.database import init_db

app = FastAPI(title="Kiln Autonomous Project Builder")

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def offline_content_policy(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return response


app.include_router(projects.router, prefix="/api")
app.include_router(runs.router, prefix="/api")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def root():
    return {"message": "Kiln Autonomous Project Builder. Open /static/builder.html for the UI."}


@app.post("/api/chat")
def chat_endpoint(payload: Dict[str, Any] | None = None):
    data = payload or {}
    prompt = data.get("prompt") or data.get("message") or data.get("text")
    history = data.get("history") or []
    if history and not isinstance(history, list):
        raise HTTPException(status_code=400, detail="History must be a list when provided.")

    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="A prompt is required.")

    normalized_history: List[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user"))
        message = item.get("message")
        if message is None:
            message = item.get("content")
        if message is not None:
            normalized_history.append({"role": role, "message": str(message)})

    response = call_assistant(normalized_history, prompt.strip())
    return {
        "response": response.get("response", ""),
        "provider": response.get("provider"),
        "model": response.get("model"),
        "models_used": response.get("models_used", []),
        "task_type": response.get("task_type"),
        "fallback_used": response.get("fallback_used", False),
    }
