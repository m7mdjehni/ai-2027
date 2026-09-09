from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app import crud, schemas
from app.database import get_db
from sqlalchemy.orm import Session

router = APIRouter()

@router.post('/tasks')
def create_task(payload: dict, db: Session = Depends(get_db)):
    title = payload.get('title')
    description = payload.get('description')
    if not title:
        raise HTTPException(status_code=400, detail='title required')
    task = crud.create_task(db, title=title, description=description)
    return {"task": {"id": task.id, "title": task.title, "done": task.done}}

@router.get('/tasks')
def list_tasks(db: Session = Depends(get_db)):
    tasks = crud.list_tasks(db)
    return [{"id": t.id, "title": t.title, "description": t.description, "done": t.done} for t in tasks]

@router.post('/tasks/{task_id}/done')
def set_done(task_id: int, payload: dict, db: Session = Depends(get_db)):
    done = payload.get('done', True)
    task = crud.update_task_done(db, task_id, done)
    if not task:
        raise HTTPException(status_code=404, detail='task not found')
    return {"id": task.id, "done": task.done}

@router.delete('/tasks/{task_id}')
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = crud.delete_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='task not found')
    return {"id": task_id, "deleted": True}
