import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routers import conversation, tasks, notes, projects
from app.routers import runs
from app.routers import publications
from app.database import init_db

app = FastAPI(title="Hermes AI Project Builder")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(conversation.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(publications.router, prefix="/api")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def root():
    return {"message": "AI Personal Assistant backend. Visit /static/index.html for UI."}
