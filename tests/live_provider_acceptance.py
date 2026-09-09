"""Phase 5 ONLY: one authorized production-API submission, never retried.

prepare performs no provider work. submit has a persistent one-shot guard.
observe is read-only. Preserves the fixture, checkpoints and durable history.
"""
import argparse
import inspect
import hashlib
import json
from pathlib import Path
import sys
import uuid
import subprocess
import requests
from dotenv import load_dotenv
from sqlalchemy import select, text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
load_dotenv(ROOT/'.env')
from app import project_workspace as ws
from app.models import Project
from app.run_models import ProjectRun
from hermes_run_dispatcher import host_sessions
from hermes_router import PROJECT_MODELS
import hermes_repair as repair
from hermes_validation import docker_call
from validation.profiles import WEB
from hermes_diagnostics import safe_diagnostics, DIAGNOSTIC_ROOT

DIRECTORY=ROOT/'runtime'/'phase5'
STATE=DIRECTORY/'acceptance.json'
BASE='http://localhost:8000'


def save(data):
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    temporary=STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,indent=2,sort_keys=True),encoding='utf-8')
    temporary.replace(STATE)


def get(path):
    response=requests.get(BASE+path,timeout=10); response.raise_for_status(); return response.json()


def prepare():
    if STATE.exists(): raise ValueError('Acceptance fixture already prepared; do not create another')
    assert PROJECT_MODELS['coding']['primary']=={'model':'gpt-5.6-sol','provider':'openai-codex'}
    assert PROJECT_MODELS['coding']['fallback'] is None
    assert (repair.MAX_VALIDATIONS,repair.MAX_REPAIRS,repair.RUN_SECONDS,repair.REPAIR_SECONDS)==(4,3,600,120)
    assert WEB.timeout_seconds==20
    assert requests.get(BASE+'/api/projects?limit=1',timeout=10).status_code==200
    assert requests.get('http://localhost:8765/health',timeout=10).status_code==200
    code,version=docker_call(['docker','info','--format','{{.ServerVersion}}']); assert code==0
    code,image=docker_call(['docker','image','inspect','--format','{{.Id}}',WEB.image]); assert code==0
    sessions=host_sessions()
    with sessions() as db:
        migrations=list(db.execute(text('SELECT version FROM schema_migrations ORDER BY version')).scalars())
        assert {1,2}<=set(migrations)
        assert db.scalar(select(ProjectRun.id).where(ProjectRun.active_project.is_not(None)).limit(1)) is None
    project=uuid.uuid4().hex; run=uuid.uuid4().hex
    ws.workspace(project,create=True)
    ws.write_file(project,'index.html','<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Phase 5 acceptance</title></head><body><h1>Acceptance fixture</h1><script src="script.js"></script></body></html>')
    ws.write_file(project,'script.js','function greet() {\n  console.log("hello"\n}\n')
    with sessions.begin() as db:
        db.add(Project(id=project,name='Phase 5 live acceptance — disposable',
            original_prompt='Repair the one JavaScript syntax error in this disposable acceptance fixture.',
            task_type='coding',model='gpt-5.6-sol',provider='openai-codex',fallback_used=False,
            status='ready',message='Small acceptance fixture; inspect the durable run for its validated outcome.'))
    revision=get(f'/api/projects/{project}/revision')['revision']
    response=requests.post(BASE+f'/api/projects/{project}/validate',json={'revision':revision},timeout=80)
    response.raise_for_status(); baseline=response.json()
    assert baseline['status']=='failed' and baseline['passed'] is False and baseline['cleanup_confirmed'] is True
    assert len(baseline['errors'])==1
    assert baseline['errors'][0]['file']=='script.js' and baseline['errors'][0]['check']=='javascript-syntax'
    from hermes_project_worker import _configure_project_codex_timeout
    from hermes_projects import _project_worker_timeout
    hashes={f.__name__:hashlib.sha256(inspect.getsource(f).encode()).hexdigest()
            for f in (_configure_project_codex_timeout,_project_worker_timeout)}
    result={'project_id':project,'run_id':run,'requested_revision':revision,'baseline':baseline,
        'prechecks':{'backend_health':200,'host_health':200,'postgresql':True,'migrations':migrations,
            'docker_version':version.strip(),'validator_image':image.strip(),'no_active_reservations':True,
            'model':'gpt-5.6-sol','provider':'openai-codex','maximum_validations':4,'maximum_repairs':3,
            'run_seconds':600,'repair_seconds':120,'validator_seconds':20,'timeout_source_hashes':hashes},
        'submitted':False}
    save(result)
    print(json.dumps({'project_id':project,'run_id':run,'prechecks':result['prechecks'],
                      'baseline_errors':baseline['errors']},indent=2))


def submit():
    result=json.loads(STATE.read_text())
    assert result['submitted'] is False
    # Exclusive marker is written before the HTTP request. An uncertain response
    # must be inspected by GET; this script never repeats the POST.
    with (DIRECTORY/'submission.once').open('x') as marker: marker.write(result['run_id'])
    result['submitted']=True; save(result)
    response=requests.post(BASE+f'/api/projects/{result["project_id"]}/runs',
        json={'run_id':result['run_id'],'revision':result['requested_revision']},timeout=15)
    result['submission_http_status']=response.status_code; save(result)
    response.raise_for_status()
    print(json.dumps({'run_id':result['run_id'],'http_status':response.status_code}))


def observe():
    result=json.loads(STATE.read_text()); project=result['project_id']; run=result['run_id']
    detail=get(f'/api/projects/{project}/runs/{run}')
    events=get(f'/api/projects/{project}/runs/{run}/events?limit=200')
    allowed={'id','project_id','status','failure_class','safe_summary','requested_revision','current_revision',
        'repair_count','validation_count','cleanup_confirmed','created_at','started_at','finished_at',
        'cancellation_requested','attempts','validations'}
    result['run']={key:value for key,value in detail.items() if key in allowed}
    result['events']=[{key:event[key] for key in ('sequence','event_type','stage','created_at')} for event in events]
    path=DIAGNOSTIC_ROOT/(project+'.json')
    if path.exists():result['diagnostics']=safe_diagnostics(json.loads(path.read_text()))
    save(result)
    print(json.dumps({'run_id':run,'status':detail['status'],'failure_class':detail['failure_class'],
        'repair_count':detail['repair_count'],'validation_count':detail['validation_count'],
        'events':[e['event_type'] for e in events],'diagnostics':result.get('diagnostics',{})},indent=2))


def finalize():
    result=json.loads(STATE.read_text()); project=result['project_id']; run_id=result['run_id']
    detail=get(f'/api/projects/{project}/runs/{run_id}')
    assert detail['status']=='completed' and detail['failure_class']=='repaired_successfully'
    assert detail['repair_count']==1 and detail['validation_count']==2
    assert len(detail['attempts'])==1 and detail['attempts'][0]['changed_files']==['script.js']
    assert detail['validations'][0]['passed'] is False and detail['validations'][-1]['passed'] is True
    assert detail['validations'][-1]['errors']==[]
    revision,files=ws.capture_revision(project)
    assert sorted(files)==['index.html','script.js'] and revision==detail['current_revision']
    baseline=repair.RUNTIME/run_id/'0'
    final=repair.RUNTIME/run_id/'1'
    assert baseline.is_dir() and final.is_dir()
    assert (baseline/'script.js').read_text()!=files['script.js']
    assert (baseline/'index.html').read_text()==files['index.html']
    assert result['diagnostics']['write_file_invoked'] is True and result['diagnostics']['stream_events']>0
    sessions=host_sessions()
    with sessions() as db:
        row=db.get(ProjectRun,run_id)
        assert row.active_project is None and row.worker_id is None and row.lease_expires_at is None and row.cleanup_confirmed
        count=db.execute(text('SELECT count(*) FROM project_runs WHERE project_id=:project'),{'project':project}).scalar()
        assert count==1
    command=("$ErrorActionPreference='Stop'; $p=@(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" "
             "| Where-Object {$_.CommandLine -match 'hermes_project_worker.py'}); Write-Output $p.Count")
    check=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        capture_output=True,text=True,timeout=10,shell=False)
    assert check.returncode==0 and check.stdout.strip()=='0'
    code,containers=docker_call(['docker','ps','--all','--quiet','--filter','label=hermes.validation=phase1'])
    assert code==0 and not containers.strip()
    from hermes_project_worker import _configure_project_codex_timeout
    from hermes_projects import _project_worker_timeout
    for function in (_configure_project_codex_timeout,_project_worker_timeout):
        assert result['prechecks']['timeout_source_hashes'][function.__name__]==hashlib.sha256(inspect.getsource(function).encode()).hexdigest()
    result['acceptance']='PASS'
    result['cleanup']={'orphan_workers':0,'validator_containers':0,'reservation_released':True,
        'lease_released':True,'checkpoints_preserved':True,'fixture_preserved':True,
        'single_durable_run_verified':True,'timeout_sources_unchanged':True,'index_html_unchanged':True}
    save(result); print(json.dumps({'acceptance':'PASS','cleanup':result['cleanup']},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','submit','observe','finalize'])
    action=parser.parse_args().action
    try: globals()[action]()
    except Exception as error:
        print(json.dumps({'failure':type(error).__name__,'action':action,'safe_message':'Acceptance step stopped; inspect safe local state. Never repeat a submitted run.'}))
        raise SystemExit(1)
