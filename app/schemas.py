from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ConversationCreate(BaseModel):
    role: str
    message: str

class ConversationRead(ConversationCreate):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None

class TaskRead(TaskCreate):
    id: int
    done: bool
    created_at: Optional[datetime]

    class Config:
        orm_mode = True

class NoteCreate(BaseModel):
    title: str
    content: Optional[str] = None

class NoteRead(NoteCreate):
    id: int
    created_at: Optional[datetime]

    class Config:
        orm_mode = True
