from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, func
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    done = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Note(Base):
    __tablename__ = 'notes'
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = 'projects'
    id = Column(String(32), primary_key=True)
    name = Column(String(100), nullable=False)
    original_prompt = Column(Text, nullable=False)
    task_type = Column(String(20), nullable=False, default='coding')
    model = Column(String(100))
    provider = Column(String(100))
    fallback_used = Column(Boolean, default=False)
    status = Column(String(20), nullable=False, default='building')
    message = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
