"""Offline operator recovery. Stop the host bridge before using this command."""
import os
import subprocess
from sqlalchemy import select
from app.project_workspace import validate_id, reject_link
from app.run_models import ProjectRun
from app import run_store as store
from hermes_validation import docker_call


def confirm_quiescent():
    if os.name!='nt': raise ValueError('Windows recovery only')
    # Fixed trusted inspection; no user/model text is interpolated into PowerShell.
    command=("$ErrorActionPreference='Stop'; $p=@(Get-CimInstance Win32_Process "
        "-Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -match "
        "'hermes_service.py|hermes_project_worker.py'}); Write-Output $p.Count")
    process=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        capture_output=True,text=True,timeout=10,shell=False)
    if process.returncode or process.stdout.strip()!='0': raise ValueError('Host or workers not confirmed stopped')
    code,containers=docker_call(['docker','ps','--all','--quiet','--filter','label=hermes.validation=phase1'])
    if code or containers.strip(): raise ValueError('Validator cleanup not confirmed')


def recover_run(sessions,run_id):
    from hermes_repair import RUNTIME
    validate_id(run_id)
    confirm_quiescent()
    with sessions.begin() as db:
        store.recover(db)
        run=db.scalar(select(ProjectRun).where(ProjectRun.id==run_id).with_for_update())
        if not run or run.status not in {'interrupted','failed'} or not run.active_project:
            raise ValueError('Run does not require recovery')
        validate_id(run.project_id)
        reject_link(RUNTIME.parent); reject_link(RUNTIME)
        lock=RUNTIME/(run.project_id+'.lock'); reject_link(lock)
        if lock.exists():
            if lock.read_text().strip()!=run_id: raise ValueError('Lock belongs to another run')
            lock.unlink()
        run.active_project=None; run.worker_id=None; run.lease_expires_at=None; run.cleanup_confirmed=True
        run.safe_summary='Cleanup confirmed; submit a new run with current revision'
        store.event(db,run,'recovery_confirmed')


if __name__=='__main__':
    import argparse
    from dotenv import load_dotenv
    from hermes_run_dispatcher import host_sessions
    parser=argparse.ArgumentParser(description='Confirm cleanup; never resumes model work')
    parser.add_argument('run_id'); args=parser.parse_args(); load_dotenv()
    try:
        recover_run(host_sessions(),args.run_id)
        print('Cleanup confirmed. The previous run remains unsuccessful; submit a fresh revision explicitly.')
    except Exception:
        print('Recovery refused. Stop the host bridge and workers, confirm Docker cleanup, and inspect the run lock.')
        raise SystemExit(1)
