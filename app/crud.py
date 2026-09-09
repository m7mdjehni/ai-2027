from sqlalchemy.orm import Session
from app import models

# Conversation CRUD

def create_conversation(db: Session, role: str, message: str):
    conv = models.Conversation(role=role, message=message)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv

def list_conversations(db: Session, limit: int = 50):
    return db.query(models.Conversation).order_by(models.Conversation.created_at.asc()).limit(limit).all()

# Tasks CRUD

def create_task(db: Session, title: str, description: str = None):
    task = models.Task(title=title, description=description)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task

def list_tasks(db: Session):
    return db.query(models.Task).order_by(models.Task.created_at.desc()).all()

def update_task_done(db: Session, task_id: int, done: bool):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task:
        task.done = done
        db.commit()
        db.refresh(task)
    return task

def delete_task(db: Session, task_id: int):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task:
        db.delete(task)
        db.commit()
    return task

# Notes CRUD

def create_note(db: Session, title: str, content: str = None):
    note = models.Note(title=title, content=content)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note

def list_notes(db: Session):
    return db.query(models.Note).order_by(models.Note.created_at.desc()).all()

def delete_note(db: Session, note_id: int):
    note = db.query(models.Note).filter(models.Note.id == note_id).first()
    if note:
        db.delete(note)
        db.commit()
    return note
