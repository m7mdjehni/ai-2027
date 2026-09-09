"""Durable execution records; no prompts, credentials or raw provider output."""
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from app.models import Base


class ProjectRun(Base):
    __tablename__ = 'project_runs'
    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), ForeignKey('projects.id'), nullable=False, index=True)
    active_project = Column(String(32), unique=True)  # retained when cleanup is uncertain
    operation = Column(String(20), nullable=False, default='repair')
    task = Column(String(100), nullable=False, default='Repair managed static website')
    status = Column(String(30), nullable=False)
    requested_revision = Column(String(64), nullable=False)
    current_revision = Column(String(64))
    max_repairs = Column(Integer, nullable=False, default=3)
    repair_count = Column(Integer, nullable=False, default=0)
    validation_count = Column(Integer, nullable=False, default=0)
    automatic_repair = Column(Boolean, nullable=False, default=False)
    cancellation_requested = Column(Boolean, nullable=False, default=False)
    worker_id = Column(String(32))
    lease_expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    failure_class = Column(String(50))
    safe_summary = Column(String(200))
    event_sequence = Column(Integer, nullable=False, default=0)
    cleanup_confirmed = Column(Boolean, nullable=False, default=False)


class RunEvent(Base):
    __tablename__ = 'project_run_events'
    id = Column(Integer, primary_key=True)
    run_id = Column(String(32), ForeignKey('project_runs.id'), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(40), nullable=False)
    stage = Column(String(30), nullable=False)
    safe_summary = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint('run_id','sequence'),)


class ModelAttempt(Base):
    __tablename__ = 'project_model_attempts'
    id = Column(Integer, primary_key=True)
    run_id = Column(String(32), ForeignKey('project_runs.id'), nullable=False, index=True)
    repair_cycle = Column(Integer, nullable=False)
    model = Column(String(100), nullable=False)
    provider = Column(String(100), nullable=False)
    attempt_result = Column(String(30), nullable=False)
    safe_error_category = Column(String(50))
    duration_ms = Column(Integer)
    changed_files = Column(JSON, nullable=False, default=list, server_default='[]')
    __table_args__ = (UniqueConstraint('run_id','repair_cycle'),)


class RunValidation(Base):
    __tablename__ = 'project_validations'
    id = Column(Integer, primary_key=True)
    run_id = Column(String(32), ForeignKey('project_runs.id'), nullable=False, index=True)
    cycle = Column(Integer, nullable=False)
    revision = Column(String(64), nullable=False)
    profile = Column(String(30), nullable=False)
    passed = Column(Boolean, nullable=False)
    status = Column(String(30), nullable=False)
    errors = Column(JSON, nullable=False)
    duration_ms = Column(Integer)
    cleanup_confirmed = Column(Boolean, nullable=False)
    __table_args__ = (UniqueConstraint('run_id','cycle'),)
