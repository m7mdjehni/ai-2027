"""Transactional PostgreSQL admission, leases, cancellation and safe event storage."""
from datetime import timedelta
import uuid
from sqlalchemy import select, func
from app.run_models import ProjectRun, RunEvent, ModelAttempt, RunValidation

LEASE_SECONDS = 15
ACTIVE_STATES = {'claimed','baseline_validation','repairing','validating','cancelling'}
FINAL_CLASSES = {'repaired_successfully','unresolved_validation_errors','provider_failure',
    'validation_infrastructure_failure','security_violation','cancelled','deadline_exceeded','no_progress'}


class LeaseLost(Exception): pass


def now(db):
    clock=func.clock_timestamp() if db.get_bind().dialect.name=='postgresql' else func.current_timestamp()
    return db.scalar(select(clock))


def event(db, run, kind):
    run.event_sequence += 1
    db.add(RunEvent(run_id=run.id, sequence=run.event_sequence, event_type=kind,
        stage=run.status, safe_summary=kind.replace('_',' '), created_at=now(db)))


def submit(db, project, run_id, revision):
    # Serialize admission with the short publication snapshot handoff, not its network upload.
    from app.models import Project
    db.scalar(select(Project.id).where(Project.id == project).with_for_update())
    existing = db.get(ProjectRun, run_id)
    if existing:
        if existing.project_id != project or existing.requested_revision != revision:
            raise ValueError('Run ID conflict')
        return existing
    run = ProjectRun(id=run_id, project_id=project, active_project=project, requested_revision=revision,
        status='queued', created_at=now(db), event_sequence=0, cancellation_requested=False)
    db.add(run); db.flush()
    event(db,run,'run_created')
    return run


def record_standalone_validation(db, project, revision, result, started_at):
    """Append a terminal read-only validation run, never queue an autonomous repair.

    Called only after authenticated host delivery. Each invocation is a distinct
    observation; repeats cannot overwrite earlier validations or repair history.
    Caller owns the transaction containing the run, result and timeline.
    """
    from app.validation_contracts import check_trusted_result
    check_trusted_result(result, project, revision)
    clean = result['cleanup_confirmed']
    success = result['passed'] and clean
    classification = ('validation_passed' if success else 'validation_failed'
                      if result['status'] == 'failed' and clean else 'validation_infrastructure_failure')
    run = ProjectRun(id=uuid.uuid4().hex, project_id=project, operation='validation',
        task='Validate managed static website', requested_revision=revision, current_revision=revision,
        status='validating', created_at=started_at, started_at=started_at,
        max_repairs=0, repair_count=0, validation_count=1, automatic_repair=False,
        cleanup_confirmed=clean, event_sequence=0, cancellation_requested=False)
    db.add(run); db.flush()
    run.event_sequence = 1
    db.add(RunEvent(run_id=run.id, sequence=1, event_type='validation_started', stage='validating',
        safe_summary='validation started', created_at=started_at))
    errors = [{**{key: error[key] for key in ('file','line','column','check','code')},
               'message':'Static validation check failed.'} for error in result['errors'][:30]]
    db.add(RunValidation(run_id=run.id, cycle=1, revision=revision, profile=result['profile'],
        passed=result['passed'], status=result['status'], errors=errors,
        duration_ms=result['duration_ms'], cleanup_confirmed=clean))
    run.status='completed' if success else 'failed'
    run.failure_class=classification; run.safe_summary=classification.replace('_',' ')
    run.finished_at=now(db)
    event(db, run, classification)
    db.flush()
    return run


def claim(db, worker):
    run = db.scalar(select(ProjectRun).where(ProjectRun.status == 'queued').order_by(ProjectRun.created_at)
                    .with_for_update(skip_locked=True).limit(1))
    if run is None: return None
    run.status='claimed'; run.worker_id=worker; run.started_at=now(db)
    run.lease_expires_at=now(db)+timedelta(seconds=LEASE_SECONDS)
    event(db,run,'run_claimed')
    return run.id


def owned(db, run_id, worker):
    run = db.scalar(select(ProjectRun).where(ProjectRun.id == run_id).with_for_update())
    if (not run or run.worker_id != worker or run.status not in ACTIVE_STATES
            or not run.lease_expires_at or run.lease_expires_at <= now(db)):
        raise LeaseLost('Lease unavailable')
    return run


def renew(db, run_id, worker):
    run=owned(db,run_id,worker)
    run.lease_expires_at=now(db)+timedelta(seconds=LEASE_SECONDS)
    return run.cancellation_requested


def request_cancel(db, project, run_id):
    run=db.scalar(select(ProjectRun).where(ProjectRun.id==run_id,ProjectRun.project_id==project).with_for_update())
    if not run: raise LookupError('Run not found')
    if run.status in {'completed','failed','cancelled','interrupted'}: return run
    run.cancellation_requested=True
    if run.status=='queued':
        run.status='cancelled'; run.failure_class='cancelled'; run.cleanup_confirmed=True
        run.active_project=None; run.finished_at=now(db)
        event(db,run,'run_cancelled')
    else:
        run.status='cancelling'; event(db,run,'cancellation_requested')
    return run


def recover(db):
    rows=db.scalars(select(ProjectRun).where(ProjectRun.status.in_(ACTIVE_STATES),
        ProjectRun.lease_expires_at <= now(db)).with_for_update(skip_locked=True)).all()
    for run in rows:
        run.status='interrupted'; run.failure_class='interrupted'; run.finished_at=now(db)
        run.safe_summary='Lease expired; cleanup requires operator confirmation'
        # Retain active_project and worker ownership: never assume a vanished process is dead.
        event(db,run,'run_interrupted')
        for attempt in db.scalars(select(ModelAttempt).where(ModelAttempt.run_id==run.id,
                                                            ModelAttempt.attempt_result=='started')):
            attempt.attempt_result='interrupted'; attempt.safe_error_category='lease_expired'
    return len(rows)


def observe(db, run_id, worker, kind, data):
    run=owned(db,run_id,worker)
    if run.cancellation_requested and kind in {'repair_started','baseline_started','validation_started','repair_retry'}:
        raise LeaseLost('Cancellation pending')
    if kind in {'baseline_started','validation_started'}:
        run.status='baseline_validation' if kind=='baseline_started' else 'validating'
    elif kind=='repair_started':
        run.status='repairing'
        if data['cycle'] > 3: raise ValueError('Repair limit')
        db.add(ModelAttempt(run_id=run.id,repair_cycle=data['cycle'],model=data.get('model', 'gpt-5.6-sol'),
            provider=data.get('provider', 'openai-codex'),attempt_result='started'))
    elif kind=='repair_finished':
        attempt=db.scalar(select(ModelAttempt).where(ModelAttempt.run_id==run.id,
            ModelAttempt.repair_cycle==data['cycle']))
        attempt.attempt_result='completed' if data['success'] else 'failed'
        attempt.safe_error_category=None if data['success'] else 'worker_failure'
        attempt.duration_ms=min(600000,max(0,data['duration_ms']))
        from app.project_workspace import file_path
        paths=data.get('changed_files',[])
        if not isinstance(paths,list) or len(paths)>100: raise ValueError('Invalid changed files')
        for path in paths: file_path(run.project_id,path)
        attempt.changed_files=sorted(set(paths))
        run.repair_count=data['cycle']; run.automatic_repair=True; run.current_revision=data['revision']
        kind='files_changed' if data['changed'] else 'repair_unchanged'
    elif kind=='validation_finished':
        result=data['result']
        errors=[]
        for error in result.get('errors',[])[:30]:
            errors.append({key: (max(1,min(1000000,int(error.get(key,1)))) if key in {'line','column'}
                else str(error.get(key,''))[:200]) for key in ('file','line','column','check','code')})
            errors[-1]['message']='Static validation check failed.'
        run.validation_count+=1
        if run.validation_count > 4: raise ValueError('Validation limit')
        run.current_revision=result['revision']
        db.add(RunValidation(run_id=run.id,cycle=run.validation_count,revision=result['revision'],
            profile='static-web-v1',passed=result.get('passed') is True,status=result['status'][:30],
            errors=errors,duration_ms=min(600000,max(0,result.get('duration_ms',0))),
            cleanup_confirmed=result.get('cleanup_confirmed') is True))
        kind='validation_passed' if result.get('passed') else ('baseline_failed' if run.validation_count==1 else 'validation_failed')
    elif kind != 'repair_retry':
        raise ValueError('Unknown event')
    event(db,run,kind)


def finish(db, run_id, worker, result):
    run=owned(db,run_id,worker)
    clean = result.get('worker_cleanup_confirmed') is True and all(
        v.get('cleanup_confirmed') is True for v in result.get('validations',[]))
    classification=result.get('status')
    if classification not in FINAL_CLASSES: classification='security_violation'
    run.current_revision=result.get('final_revision')
    if classification=='repaired_successfully':
        final=db.scalar(select(RunValidation).where(RunValidation.run_id==run_id)
                        .order_by(RunValidation.cycle.desc()).limit(1))
        if not (clean and final and final.passed and final.status=='passed'
                and final.revision==run.current_revision and final.cleanup_confirmed):
            classification='security_violation'
    if classification=='cancelled' and not clean: classification='security_violation'
    if run.cancellation_requested and clean and classification!='security_violation': classification='cancelled'
    run.status='completed' if classification=='repaired_successfully' else ('cancelled' if classification=='cancelled' else 'failed')
    run.failure_class=classification; run.safe_summary=classification.replace('_',' ')
    run.cleanup_confirmed=clean; run.finished_at=now(db)
    if clean:
        run.active_project=None; run.lease_expires_at=None; run.worker_id=None
    event(db,run,{'completed':'run_completed','cancelled':'run_cancelled'}.get(run.status,'run_failed'))
