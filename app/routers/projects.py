"""Project history and text viewer. Docker never executes Hermes or project code."""
import os
import re
import uuid
import logging

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.models import Project
from app.project_workspace import validate_id, list_files, read_file
from app.project_workspace import capture_revision
from app.validation_contracts import ValidateProject, check_trusted_result
from app import run_store
from app.repair_contracts import RepairProject

router = APIRouter()
logger = logging.getLogger(__name__)


class ProjectCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=12_000)

    @validator("prompt")
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Prompt is required")
        return value.strip()

    class Config:
        extra = "forbid"


def project_or_404(db, project_id):
    try:
        validate_id(project_id)
    except ValueError:
        raise HTTPException(400, "Invalid project ID")
    row = db.get(Project, project_id)
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


def serialize(row, files=None):
    return {"success": row.status == "completed", "project_id": row.id,
            "project_name": row.name, "original_prompt": row.original_prompt,
            "task_type": row.task_type, "model": row.model, "provider": row.provider,
            "fallback_used": row.fallback_used, "status": row.status,
            "created_at": row.created_at, "message": row.message,
            **({"files": files, "preview_available": False,
                "preview_pending": "index.html" in files} if files is not None else {})}


def actual_files(project_id):
    try:
        return list_files(project_id)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise HTTPException(409, "Project workspace failed validation")


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    token = os.getenv("PROJECT_BUILDER_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "Configure PROJECT_BUILDER_TOKEN on both services (at least 32 characters)")
    project_id = uuid.uuid4().hex
    name = re.sub(r"[^a-z0-9]+", "-", payload.prompt.lower()).strip("-")[:70].rstrip("-") or "project"
    row = Project(id=project_id, name=name, original_prompt=payload.prompt, status="building",
                  message="AI is building your project")
    db.add(row)
    db.commit()
    db.refresh(row)
    files = []
    try:
        url = os.getenv("HERMES_ROUTER_URL", "http://host.docker.internal:8765").rstrip("/")
        try:
            timeout = max(620, int(os.getenv("HERMES_ROUTER_TIMEOUT", "620")))
        except ValueError:
            timeout = 620
        response = requests.post(url + "/projects/build", json={"project_id": project_id, "prompt": payload.prompt},
                                 headers={"X-Project-Token": token}, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
            raise ValueError("Invalid host response")
        for field in ("model", "provider"):
            value = result.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 100):
                raise ValueError("Invalid routing metadata")
            setattr(row, field, value)
        row.fallback_used = result.get("fallback_used") is True
        # Detect on disk, never trust filenames claimed by an AI/HTTP response.
        files = actual_files(project_id)
        row.status = "completed" if result["success"] and files else "failed"
        row.message = "Project created successfully" if row.status == "completed" else "AI did not complete the project; inspect any partial files and check the host CLI"
    except requests.Timeout:
        row.status = "unknown"
        row.message = "Host request timed out; execution may still be running. Check host logs before retrying"
    except (requests.RequestException, ValueError, HTTPException):
        row.status = "failed"
        row.message = "Project build failed. Check host service, shared workspace, and configuration"
        logger.warning("Project build failed: project_id=%s", project_id)
    db.commit()
    db.refresh(row)
    return serialize(row, files)


@router.get("/projects")
def projects(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return [serialize(row) for row in db.query(Project).order_by(Project.created_at.desc()).offset(offset).limit(limit).all()]


@router.get("/projects/{project_id}")
def project_detail(project_id: str, db: Session = Depends(get_db)):
    row = project_or_404(db, project_id)
    return serialize(row, actual_files(project_id))


@router.get("/projects/{project_id}/files")
def project_files(project_id: str, db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    return {"files": actual_files(project_id)}


@router.get("/projects/{project_id}/revision")
def project_revision(project_id: str, db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    try:
        revision, files = capture_revision(project_id)
        return {"project_id": project_id, "revision": revision, "files": sorted(files)}
    except FileNotFoundError:
        raise HTTPException(404, "Project workspace not found")
    except (ValueError, OSError):
        raise HTTPException(409, "Project workspace failed validation")


@router.post("/projects/{project_id}/validate")
def validate_existing_project(project_id: str, payload: ValidateProject, db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    token = os.getenv("PROJECT_BUILDER_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "Project service authentication is not configured")
    url = os.getenv("HERMES_ROUTER_URL", "http://host.docker.internal:8765").rstrip('/')
    try:
        if capture_revision(project_id)[0] != payload.revision:
            raise HTTPException(409, 'Stale project revision')
    except (ValueError, OSError):
        raise HTTPException(409, 'Managed workspace unavailable')
    started_at = run_store.now(db)
    try:
        response = requests.post(url + '/projects/validate', json={"project_id": project_id, "revision": payload.revision},
                                 headers={"X-Project-Token": token}, timeout=75, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Unexpected validation response')
        result = response.json()
        check_trusted_result(result, project_id, payload.revision)
    except requests.Timeout:
        raise HTTPException(504, "Validation service did not respond; its independent sandbox deadline still applies")
    except (requests.RequestException, ValueError):
        raise HTTPException(503, "Validation service unavailable")
    try:
        if capture_revision(project_id)[0] != payload.revision:
            raise HTTPException(409, 'Project changed during validation; validate the current revision')
        run = run_store.record_standalone_validation(db, project_id, payload.revision, result, started_at)
        run_id = run.id
        db.commit()
        return {**result, 'run_id': run_id}
    except HTTPException:
        db.rollback(); raise
    except (ValueError, OSError):
        db.rollback(); raise HTTPException(409, 'Managed workspace changed or unavailable')
    except SQLAlchemyError:
        db.rollback(); raise HTTPException(503, 'Validation result could not be saved; retry validation')


@router.get("/projects/{project_id}/file")
def project_file(project_id: str, path: str = Query(..., min_length=1, max_length=240), db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    try:
        content = read_file(project_id, path)
    except FileNotFoundError:
        raise HTTPException(404, "Project file not found")
    except (OSError, ValueError):
        raise HTTPException(400, "Unsafe path or unsupported text file")
    return {"path": path, "content": content}


def repair_host_call(path, payload, timeout):
    token = os.getenv('PROJECT_BUILDER_TOKEN', '')
    if len(token) < 32:
        raise HTTPException(503, 'Project service authentication is not configured')
    url = os.getenv('HERMES_ROUTER_URL', 'http://host.docker.internal:8765').rstrip('/')
    try:
        response = requests.post(url + path, json=payload, headers={'X-Project-Token': token}, timeout=timeout)
        if response.status_code in {404, 409, 429}:
            raise HTTPException(response.status_code, 'Repair unavailable, stale, or not active')
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        raise HTTPException(504, 'Repair response timed out; host deadline still applies')
    except (requests.RequestException, ValueError):
        raise HTTPException(503, 'Repair service unavailable')


@router.post('/projects/{project_id}/repair')
def repair_existing_project(project_id: str, payload: RepairProject, db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    return repair_host_call('/projects/repair', {'project_id': project_id, **payload.dict()}, 720)


@router.post('/projects/{project_id}/repair/{run_id}/cancel')
def cancel_existing_repair(project_id: str, run_id: str, db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    try:
        validate_id(run_id)
    except ValueError:
        raise HTTPException(422, 'Invalid run ID')
    return repair_host_call('/projects/' + project_id + '/repair/' + run_id + '/cancel', {}, 15)
