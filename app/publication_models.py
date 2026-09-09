"""Publication metadata only: never credentials or generated file contents."""
from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from app.models import Base


class Publication(Base):
    __tablename__ = 'project_publications'
    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), ForeignKey('projects.id'), nullable=False, index=True)
    revision = Column(String(64), nullable=False)
    provider = Column(String(30), nullable=False, default='github-pages')
    repository = Column(String(160), nullable=False)
    active_repository = Column(String(160), unique=True)
    commit_sha = Column(String(40))
    status = Column(String(30), nullable=False)
    public_url = Column(String(255))
    created_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True))
    failure_class = Column(String(40))
    safe_summary = Column(String(200))
    reconciliation_status = Column(String(30))
    reconciliation_started_at = Column(DateTime(timezone=True))
    reconciliation_finished_at = Column(DateTime(timezone=True))
    actions_status = Column(String(30))
    actions_workflow_commit = Column(String(40))
    actions_run_id = Column(String(30))
    actions_deployment_id = Column(String(40))
    actions_started_at = Column(DateTime(timezone=True))
    actions_finished_at = Column(DateTime(timezone=True))
    files = Column(JSON, nullable=False)
    events = Column(JSON, nullable=False, default=list)

