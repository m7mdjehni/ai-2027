from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.repair_contracts import RepairProject
from app.routers.projects import project_or_404
from app.project_workspace import validate_id, capture_revision
from app.run_models import ProjectRun, RunEvent, RunValidation, ModelAttempt
from app import run_store as store

router=APIRouter()


def serialize(row):
    return {column.name:getattr(row,column.name) for column in row.__table__.columns
            if column.name not in {'active_project'}}


def lookup(db,project,run_id):
    project_or_404(db,project)
    try: validate_id(run_id)
    except ValueError: raise HTTPException(422,'Invalid run ID')
    row=db.get(ProjectRun,run_id)
    if not row or row.project_id!=project: raise HTTPException(404,'Run not found')
    return row


@router.post('/projects/{project_id}/runs',status_code=202)
def submit(project_id:str,payload:RepairProject,db:Session=Depends(get_db)):
    project_or_404(db,project_id)
    try:
        existing=db.get(ProjectRun,payload.run_id)
        if not existing and capture_revision(project_id)[0]!=payload.revision:
            raise HTTPException(409,'Stale project revision')
        run=store.submit(db,project_id,payload.run_id,payload.revision)
        db.commit(); db.refresh(run)
        return serialize(run)
    except (ValueError,IntegrityError):
        db.rollback(); raise HTTPException(409,'Run ID conflict or project already reserved')
    except OSError:
        db.rollback(); raise HTTPException(409,'Managed workspace unavailable')


@router.get('/projects/{project_id}/runs')
def runs(project_id:str,limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db)):
    project_or_404(db,project_id)
    return [serialize(r) for r in db.scalars(select(ProjectRun).where(ProjectRun.project_id==project_id)
        .order_by(ProjectRun.created_at.desc()).limit(limit))]


@router.get('/projects/{project_id}/runs/{run_id}')
def detail(project_id:str,run_id:str,db:Session=Depends(get_db)):
    row=lookup(db,project_id,run_id)
    return {**serialize(row), 'attempts':[serialize(r) for r in db.scalars(select(ModelAttempt)
        .where(ModelAttempt.run_id==run_id).order_by(ModelAttempt.repair_cycle))],
        'validations':[serialize(r) for r in db.scalars(select(RunValidation)
        .where(RunValidation.run_id==run_id).order_by(RunValidation.cycle))]}


@router.get('/projects/{project_id}/runs/{run_id}/events')
def events(project_id:str,run_id:str,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200),db:Session=Depends(get_db)):
    lookup(db,project_id,run_id)
    return [serialize(r) for r in db.scalars(select(RunEvent).where(RunEvent.run_id==run_id,
        RunEvent.sequence>after).order_by(RunEvent.sequence).limit(limit))]


@router.post('/projects/{project_id}/runs/{run_id}/cancel')
def cancel(project_id:str,run_id:str,db:Session=Depends(get_db)):
    lookup(db,project_id,run_id)
    row=store.request_cancel(db,project_id,run_id); db.commit(); db.refresh(row)
    return serialize(row)
