"""Windows-host FastAPI bridge for the locally installed Hermes Agent."""

import logging
import os
from typing import List, Literal, Optional

from fastapi import FastAPI, Header, HTTPException
import hmac
import threading
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from hermes_router import route_prompt

from pathlib import Path

load_dotenv(Path(__file__).with_name('.env'))
logging.basicConfig(
    level=os.getenv("HERMES_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("hermes_service")
app = FastAPI(title="Hermes Multi-Model Router", version="1.0.0")
project_slots = threading.BoundedSemaphore(2)
validation_slots = threading.BoundedSemaphore(2)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    message: str = Field(..., min_length=1, max_length=100_000)


class RouteRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    history: Optional[List[HistoryMessage]] = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "hermes-router",
            "project_engine": os.getenv("PROJECT_AI_ENGINE", "ollama"),
            "chat_provider": os.getenv("AI_PROVIDER", "ollama")}


@app.post("/route")
def route(request: RouteRequest):
    history = [item.dict() for item in request.history] if request.history else None
    if os.getenv('AI_PROVIDER', 'ollama') == 'ollama':
        from app.ollama_client import chat_with_model
        from app.ai import build_messages
        response, model = chat_with_model(build_messages(history or [], request.prompt), host=True)
        return {'success': True, 'response': response, 'provider': 'ollama', 'model': model,
                'models_used': [model], 'task_type': 'general', 'fallback_used': False}
    result = route_prompt(request.prompt, history).to_dict()
    if result["success"]:
        return result
    logger.error("Routing request failed: %s", result["error"])
    return JSONResponse(status_code=502, content=result)


class ProjectRequest(BaseModel):
    project_id: str = Field(..., regex=r"^[a-f0-9]{32}$")
    prompt: str = Field(..., min_length=1, max_length=12_000)

    class Config:
        extra = "forbid"


@app.post("/projects/build")
def project_build(request: ProjectRequest, x_project_token: str = Header(default="")):
    from hermes_projects import build_project
    expected = os.getenv("PROJECT_BUILDER_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(503, "Configure PROJECT_BUILDER_TOKEN on both services (at least 32 characters)")
    if not hmac.compare_digest(expected, x_project_token):
        raise HTTPException(401, "Project service authentication failed")
    if not request.prompt.strip():
        raise HTTPException(422, "Prompt is required")
    if not project_slots.acquire(blocking=False):
        raise HTTPException(429, "Two projects are already building; try again shortly")
    try:
        return build_project(request.project_id, request.prompt)
    except FileExistsError:
        raise HTTPException(409, "Project workspace already exists")
    except (ValueError, OSError):
        raise HTTPException(503, "Project workspace or AI runtime is unavailable; check host configuration")
    finally:
        project_slots.release()


from app.validation_contracts import HostValidateProject


@app.post("/projects/validate")
def project_validate(request: HostValidateProject, x_project_token: str = Header(default="")):
    expected = os.getenv("PROJECT_BUILDER_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(503, "Project service authentication is not configured")
    if not hmac.compare_digest(expected.encode(), x_project_token.encode()):
        raise HTTPException(401, "Project service authentication failed")
    if not validation_slots.acquire(blocking=False):
        raise HTTPException(429, "Validation capacity reached")
    try:
        from hermes_validation import validate_project
        return validate_project(request.project_id, request.revision)
    except FileNotFoundError:
        raise HTTPException(404, "Project workspace not found")
    except (ValueError, OSError):
        raise HTTPException(400, "Project snapshot failed validation")
    finally:
        validation_slots.release()


from app.repair_contracts import HostRepairProject


@app.post('/projects/publication-export')
def publication_export(request: HostValidateProject, x_project_token: str = Header(default='')):
    repair_auth(x_project_token)
    from hermes_run_dispatcher import host_sessions
    from app.publication_service import gate
    from app.github_publication import PublicationFailure
    from hermes_publication_export import export_project
    try:
        with host_sessions()() as db:
            gate(db, request.project_id, request.revision, lock_admission=True)
            return {'files': export_project(request.project_id, request.revision)}
    except (PublicationFailure, FileExistsError, FileNotFoundError):
        raise HTTPException(409, 'Selected revision unavailable or project reserved')
    except ValueError as error:
        if str(error) == 'invalid_revision':
            raise HTTPException(409, 'Selected revision unavailable')
        raise HTTPException(422, 'Export security boundary rejected workspace')
    except OSError:
        raise HTTPException(422, 'Export security boundary rejected workspace')

repair_slots = threading.BoundedSemaphore(2)


def repair_auth(token):
    expected = os.getenv('PROJECT_BUILDER_TOKEN', '')
    if len(expected) < 32:
        raise HTTPException(503, 'Project service authentication is not configured')
    if not hmac.compare_digest(expected.encode(), token.encode()):
        raise HTTPException(401, 'Project service authentication failed')


@app.post('/projects/repair')
def project_repair(request: HostRepairProject, x_project_token: str = Header(default='')):
    repair_auth(x_project_token)
    # Legacy synchronous repairs must also respect durable quarantine/reservations.
    from hermes_run_dispatcher import host_sessions
    from app.run_models import ProjectRun
    from sqlalchemy import select
    sessions=getattr(getattr(app.state,'run_dispatcher',None),'sessions',None) or host_sessions()
    try:
        with sessions() as db:
            reserved=db.scalar(select(ProjectRun.id).where(ProjectRun.active_project==request.project_id))
    except Exception:
        raise HTTPException(503,'Durable project protection unavailable')
    if reserved:
        raise HTTPException(409,'Project has a durable run reservation')
    if not repair_slots.acquire(blocking=False):
        raise HTTPException(429, 'Repair capacity reached')
    from hermes_repair import repair_project, RepairConflict
    try:
        return repair_project(request.project_id, request.revision, request.run_id)
    except RepairConflict:
        raise HTTPException(409, 'Stale revision, active repair, or reused run ID')
    finally:
        repair_slots.release()


@app.post('/projects/{project_id}/repair/{run_id}/cancel')
def project_repair_cancel(project_id: str, run_id: str, x_project_token: str = Header(default='')):
    repair_auth(x_project_token)
    from hermes_repair import cancel
    try:
        accepted = cancel(project_id, run_id)
    except ValueError:
        raise HTTPException(422, 'Invalid managed identity')
    if not accepted:
        raise HTTPException(404, 'Matching active repair not found')
    return {'cancellation_requested': True, 'run_id': run_id}


@app.on_event('startup')
def start_run_dispatcher():
    from hermes_run_dispatcher import Dispatcher, host_sessions
    app.state.run_dispatcher=Dispatcher(host_sessions())
    app.state.run_thread=threading.Thread(target=app.state.run_dispatcher.serve,daemon=True)
    app.state.run_thread.start()


@app.on_event('shutdown')
def stop_run_dispatcher():
    dispatcher=getattr(app.state,'run_dispatcher',None)
    if dispatcher:
        dispatcher.stop()
        app.state.run_thread.join(timeout=60)


if __name__ == "__main__":
    import uvicorn

    # Docker Desktop must be able to reach this Windows-host process.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("HERMES_ROUTER_PORT", "8765")))
