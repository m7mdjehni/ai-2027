"""Explicit approval, trusted export and persisted publication state transitions."""
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
import requests
from sqlalchemy import select
from app.models import Project
from app.run_models import ProjectRun, RunValidation
from app.publication_models import Publication
from app.publication_export import validate_artifact
from app.github_publication import GitHubPublisher, PublicationFailure
from app import project_workspace as ws

TERMINAL = {'completed', 'failed'}


def timestamp(): return datetime.now(timezone.utc)


def gate(db, project, revision, lock_admission=False):
    ws.validate_id(project)
    if not re.fullmatch('[a-f0-9]{64}', revision): raise PublicationFailure('invalid_revision')
    row = (db.scalar(select(Project).where(Project.id == project).with_for_update())
           if lock_admission else db.get(Project, project))
    if not row or row.status not in {'ready', 'completed'}: raise PublicationFailure('invalid_revision')
    if db.scalar(select(ProjectRun.id).where(ProjectRun.active_project == project).limit(1)):
        raise PublicationFailure('invalid_revision')
    valid = db.scalar(select(RunValidation.id).join(ProjectRun, RunValidation.run_id == ProjectRun.id).where(
        ProjectRun.project_id == project, RunValidation.revision == revision,
        RunValidation.passed.is_(True), RunValidation.status == 'passed',
        RunValidation.profile == 'static-web-v1', RunValidation.cleanup_confirmed.is_(True)).limit(1))
    if valid is None: raise PublicationFailure('validation_required')
    return row


def host_export(project, revision):
    token = os.getenv('PROJECT_BUILDER_TOKEN', '')
    if len(token) < 32: raise PublicationFailure('authentication_failure')
    try:
        response = requests.post(os.getenv('HERMES_ROUTER_URL', 'http://host.docker.internal:8765').rstrip('/') +
            '/projects/publication-export', json={'project_id': project, 'revision': revision},
            headers={'X-Project-Token': token}, timeout=15, allow_redirects=False)
        if response.status_code == 409: raise PublicationFailure('invalid_revision')
        if response.status_code != 200: raise PublicationFailure('security_violation')
        files = response.json()['files']
        return validate_artifact(project, revision, files)
    except (requests.RequestException, KeyError):
        raise PublicationFailure('network_failure') from None
    except ValueError:
        raise PublicationFailure('security_violation') from None


def preview(db, project, revision, owner, exporter=host_export):
    gate(db, project, revision)
    if not re.fullmatch('[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        raise PublicationFailure('repository_failure')
    files = exporter(project, revision)
    row = Publication(id=uuid.uuid4().hex, project_id=project, revision=revision,
        repository=owner.lower() + '/managed-' + project, status='preview',
        files=sorted(files), created_at=timestamp(), events=[], safe_summary='Validated revision; awaiting confirmation')
    db.add(row); db.flush()
    return row


def confirm(db, project, publication_id, exporter=host_export):
    row = db.scalar(select(Publication).where(Publication.id == publication_id,
        Publication.project_id == project).with_for_update())
    if not row: raise PublicationFailure('invalid_revision')
    if row.status != 'preview': raise PublicationFailure('repository_failure')
    created = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
    if timestamp() - created > timedelta(minutes=10): raise PublicationFailure('invalid_revision')
    gate(db, project, row.revision)
    # Reserve repository before copying; unique constraint also protects simultaneous confirmations.
    row.active_repository = row.repository
    db.flush()
    artifact = exporter(project, row.revision)
    if sorted(artifact) != row.files: raise PublicationFailure('invalid_revision')
    transition(row, 'pending')
    return row, artifact


def transition(row, state, **metadata):
    row.status = state
    row.safe_summary = state.replace('_', ' ')
    for key, value in metadata.items(): setattr(row, key, value)
    row.events = [*row.events, {'state': state, 'at': timestamp().isoformat()}]
    if state in TERMINAL:
        row.finished_at = timestamp(); row.active_repository = None


def execute(publication_id, artifact, sessions, factory=GitHubPublisher):
    adapter = None
    def emit(state, **metadata):
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            transition(row, state, **metadata)
    try:
        with sessions.begin() as db:
            row = db.scalar(select(Publication).where(Publication.id == publication_id).with_for_update())
            if not row or row.status != 'pending': return
            transition(row, 'exporting')
            repository, project, revision = row.repository, row.project_id, row.revision
            previous = db.scalar(select(Publication.commit_sha).where(Publication.repository == repository,
                Publication.project_id == project, Publication.status == 'completed')
                .order_by(Publication.finished_at.desc()).limit(1))
        # Validate the captured bytes again; never reread them for upload.
        validate_artifact(project, revision, artifact)
        adapter = factory()
        adapter.publish(repository, artifact, previous, emit)
    except PublicationFailure as error:
        emit('failed', failure_class=error.category, safe_summary=error.category.replace('_', ' '))
    except ValueError:
        emit('failed', failure_class='security_violation', safe_summary='Export rejected')
    except Exception:
        # Do not persist/log exception strings, response bodies or headers.
        emit('failed', failure_class='deployment_failure', safe_summary='Publication stopped; inspect remote state before retry')
    finally:
        if adapter is not None: adapter.close()


def reconcile(db, project, publication_id):
    row = db.scalar(select(Publication).where(Publication.id == publication_id,
        Publication.project_id == project).with_for_update())
    if not row or row.status != 'failed' or row.failure_class != 'deployment_failure':
        raise PublicationFailure('reconciliation_failure')
    gate(db, project, row.revision)
    if not row.commit_sha:
        raise PublicationFailure('reconciliation_failure')
    row.reconciliation_status = 'pending'
    row.reconciliation_started_at = timestamp()
    row.events = [*row.events, {'state': 'reconciliation_pending', 'at': timestamp().isoformat()}]
    db.flush()
    return row


def execute_reconciliation(publication_id, sessions, factory=GitHubPublisher):
    adapter = None
    def emit(state, **metadata):
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            row.events = [*row.events, {'state': state, 'at': timestamp().isoformat()}]
            if state == 'reconciliation_completed':
                row.reconciliation_status = 'completed'
                row.public_url = metadata['public_url']
                row.reconciliation_finished_at = timestamp()
            elif state == 'reconciliation_requested':
                row.reconciliation_status = 'requested'
    try:
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            if not row or row.reconciliation_status != 'pending': return
            repository, commit, public_url = row.repository, row.commit_sha, row.public_url
            if not public_url:
                owner, name = repository.split('/')
                public_url = f'https://{owner.lower()}.github.io/{name}/'
        adapter = factory()
        adapter.reconcile(repository, commit, emit, public_url)
    except PublicationFailure as error:
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            row.reconciliation_status = 'failed'
            row.reconciliation_finished_at = timestamp()
            row.events = [*row.events, {'state': 'reconciliation_failed', 'at': timestamp().isoformat(),
                'category': error.category}]
    finally:
        if adapter is not None: adapter.close()


def actions_migrate(db, project, publication_id):
    row = db.scalar(select(Publication).where(Publication.id == publication_id,
        Publication.project_id == project).with_for_update())
    if not row or row.status != 'failed' or row.actions_status in {'pending', 'running', 'completed'}:
        raise PublicationFailure('actions_migration_failure')
    gate(db, project, row.revision)
    if not row.commit_sha:
        raise PublicationFailure('actions_migration_failure')
    row.actions_status = 'pending'
    row.actions_started_at = timestamp()
    row.events = [*row.events, {'state': 'actions_migration_pending', 'at': timestamp().isoformat()}]
    db.flush()
    return row


def execute_actions_migration(publication_id, sessions, factory=GitHubPublisher):
    adapter = None
    def emit(state, **metadata):
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            row.events = [*row.events, {'state': state, 'at': timestamp().isoformat()}]
            if 'workflow_commit' in metadata: row.actions_workflow_commit = metadata['workflow_commit']
            if 'run_id' in metadata: row.actions_run_id = metadata['run_id']
            if state == 'actions_workflow_ready': row.actions_status = 'running'
            if state == 'actions_workflow_dispatched': row.actions_status = 'running'
            if state == 'actions_migration_completed':
                row.actions_status = 'completed'; row.actions_finished_at = timestamp()
    try:
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            if not row or row.actions_status != 'pending': return
            repository, commit, public_url = row.repository, row.commit_sha, row.public_url
            if not public_url:
                owner, name = repository.split('/')
                public_url = f'https://{owner.lower()}.github.io/{name}/'
        adapter = factory()
        adapter.migrate_actions(repository, commit, emit, public_url)
    except PublicationFailure as error:
        with sessions.begin() as db:
            row = db.get(Publication, publication_id)
            row.actions_status = 'failed'
            row.actions_finished_at = timestamp()
            row.events = [*row.events, {'state': 'actions_migration_failed', 'at': timestamp().isoformat(),
                'category': error.category}]
    finally:
        if adapter is not None: adapter.close()
