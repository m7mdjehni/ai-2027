from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app import crud
from app.database import get_db
from sqlalchemy.orm import Session

router = APIRouter()

@router.post('/notes')
def create_note(payload: dict, db: Session = Depends(get_db)):
    title = payload.get('title')
    content = payload.get('content')
    if not title:
        raise HTTPException(status_code=400, detail='title required')
    note = crud.create_note(db, title=title, content=content)
    return {"note": {"id": note.id, "title": note.title}}

@router.get('/notes')
def list_notes(db: Session = Depends(get_db)):
    notes = crud.list_notes(db)
    return [{"id": n.id, "title": n.title, "content": n.content} for n in notes]

@router.delete('/notes/{note_id}')
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = crud.delete_note(db, note_id)
    if not note:
        raise HTTPException(status_code=404, detail='note not found')
    return {"id": note_id, "deleted": True}
