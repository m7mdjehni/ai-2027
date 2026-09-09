import os
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import get_db, SessionLocal
from app.routers.projects import project_or_404
from app.publication_models import Publication
from app.github_publication import PublicationFailure
from app import publication_service as service
from app.project_workspace import capture_revision

router = APIRouter()


class Preview(BaseModel):
    revision: str = Field(..., regex='^[a-f0-9]{64}$')
    owner: str = Field(..., min_length=1, max_length=39)
    class Config: extra = 'forbid'


def same_origin(request: Request):
    # Publishing is local-UI only. Reject cross-site requests and missing browser origin.
    allowed = os.getenv('PUBLICATION_ORIGIN', 'http://localhost:8000')
    if request.headers.get('origin') != allowed or str(request.base_url).rstrip('/') != allowed:
        raise HTTPException(403, 'Publication requires the local application confirmation flow')


def serialize(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != 'active_repository'}


@router.get('/projects/{project_id}/publications/eligibility')
def eligibility(project_id: str, db=Depends(get_db)):
    """Read-only gate status; preview/confirmation still perform all their checks."""
    project_or_404(db, project_id)
    revision = None
    try:
        revision = capture_revision(project_id)[0]
        service.gate(db, project_id, revision)
        return {'revision': revision, 'eligible': True, 'reason': None}
    except PublicationFailure as error:
        return {'revision': revision, 'eligible': False, 'reason': error.category}
    except (ValueError, OSError):
        return {'revision': revision, 'eligible': False, 'reason': 'invalid_revision'}


@router.post('/projects/{project_id}/publications/preview', dependencies=[Depends(same_origin)])
def preview(project_id: str, payload: Preview, db=Depends(get_db)):
    project_or_404(db, project_id)
    try:
        row = service.preview(db, project_id, payload.revision, payload.owner)
        db.commit(); db.refresh(row)
        return serialize(row)
    except PublicationFailure as error:
        db.rollback(); raise HTTPException(409, error.category)


@router.post('/projects/{project_id}/publications/{publication_id}/confirm', status_code=202,
    dependencies=[Depends(same_origin)])
def confirm(project_id: str, publication_id: str, background: BackgroundTasks, db=Depends(get_db)):
    project_or_404(db, project_id)
    if os.getenv('GITHUB_PUBLICATION_ENABLED') != '1':
        raise HTTPException(503, 'Live publication is disabled pending cloud acceptance approval')
    try:
        row, artifact = service.confirm(db, project_id, publication_id)
        db.commit(); db.refresh(row)
        background.add_task(service.execute, row.id, artifact, SessionLocal)
        return serialize(row)
    except (PublicationFailure, IntegrityError) as error:
        db.rollback()
        raise HTTPException(409, error.category if isinstance(error, PublicationFailure) else 'Publication already active')


@router.get('/projects/{project_id}/publications')
def history(project_id: str, db=Depends(get_db)):
    project_or_404(db, project_id)
    return [serialize(row) for row in db.scalars(select(Publication).where(Publication.project_id == project_id)
        .order_by(Publication.created_at.desc()).limit(50))]


@router.post('/projects/{project_id}/publications/{publication_id}/reconcile', status_code=202,
    dependencies=[Depends(same_origin)])
def reconcile(project_id: str, publication_id: str, background: BackgroundTasks, db=Depends(get_db)):
    project_or_404(db, project_id)
    if os.getenv('GITHUB_PUBLICATION_ENABLED') != '1':
        raise HTTPException(503, 'Live publication is disabled pending cloud acceptance approval')
    try:
        row = service.reconcile(db, project_id, publication_id)
        db.commit(); db.refresh(row)
        background.add_task(service.execute_reconciliation, row.id, SessionLocal)
        return serialize(row)
    except PublicationFailure as error:
        db.rollback(); raise HTTPException(409, error.category)


@router.post('/projects/{project_id}/publications/{publication_id}/actions-migrate', status_code=202,
    dependencies=[Depends(same_origin)])
def actions_migrate(project_id: str, publication_id: str, background: BackgroundTasks, db=Depends(get_db)):
    project_or_404(db, project_id)
    if os.getenv('GITHUB_ACTIONS_MIGRATION_ENABLED') != '1':
        raise HTTPException(503, 'Actions migration is disabled pending explicit approval')
    try:
        row = service.actions_migrate(db, project_id, publication_id)
        db.commit(); db.refresh(row)
        background.add_task(service.execute_actions_migration, row.id, SessionLocal)
        return serialize(row)
    except PublicationFailure as error:
        db.rollback(); raise HTTPException(409, error.category)
