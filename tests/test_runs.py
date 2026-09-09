"""Offline durable lifecycle tests; no provider SDK or live model calls."""
from datetime import timedelta
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from app.models import Base, Project
from app.run_models import ProjectRun, RunEvent, ModelAttempt, RunValidation
from app.migrations import migrate
from app import run_store as store
from app import project_workspace as ws
import hermes_repair as repair
from hermes_run_dispatcher import Dispatcher
from hermes_checkpoint_retention import retain
import test_projects
from test_projects import request


class DurableTests(unittest.TestCase):
    def make_engine(self):
        return create_engine('sqlite:///'+str(Path(self.temp.name)/'test.db'))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.engine=self.make_engine(); self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine,tables=[Base.metadata.tables['projects']])
        migrate(self.engine); migrate(self.engine)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.project='a'*32; self.run_id='b'*32; self.worker='c'*32
        for obj,key,value in [(ws,'ROOT',Path(self.temp.name)/'projects'),
                              (repair,'RUNTIME',Path(self.temp.name)/'runtime'/'repairs')]:
            p=patch.object(obj,key,value); p.start(); self.addCleanup(p.stop)
        ws.workspace(self.project,create=True)
        ws.write_file(self.project,'index.html','<!doctype html><title>Test</title>')
        ws.write_file(self.project,'script.js','broken')
        self.revision=ws.capture_revision(self.project)[0]
        with self.sessions.begin() as db:
            db.add(Project(id=self.project,name='Fixture',original_prompt='offline fixture'))

    def tx(self,callback):
        with self.sessions.begin() as db: return callback(db)

    def submit(self):
        return self.tx(lambda db:store.submit(db,self.project,self.run_id,self.revision))

    def get(self):
        with self.sessions() as db:return db.get(ProjectRun,self.run_id)

    def validate(self,p,r):
        passed=ws.read_file(p,'script.js')=='fixed'
        return dict(schema_version=1,profile='static-web-v1',revision=r,current_revision=r,passed=passed,
            status='passed' if passed else 'failed',cleanup_confirmed=True,duration_ms=10,
            errors=[] if passed else [dict(file='script.js',line=1,column=1,check='javascript-syntax',
                code='syntax_error',message='JavaScript syntax is invalid.')])

    def fix(self,p,r,errors,event,timeout):
        ws.write_file(p,'script.js','fixed')
        return {'success':True,'revision':ws.capture_revision(p)[0]}

    def dispatch(self,worker=None,validator=None):
        dispatcher=Dispatcher(self.sessions)
        with patch.object(repair,'repair_worker',side_effect=worker or self.fix), \
             patch.object(repair,'validate_project',side_effect=validator or self.validate):
            self.assertTrue(dispatcher.once())
        return self.get()

    def test_submission_idempotency_and_duplicate_project(self):
        self.submit(); self.submit()
        with self.assertRaises(IntegrityError):
            self.tx(lambda db:store.submit(db,self.project,'d'*32,self.revision))
        self.assertEqual(self.get().status,'queued')

    def test_claim_and_renew(self):
        self.submit()
        self.assertEqual(self.tx(lambda db:store.claim(db,self.worker)),self.run_id)
        self.assertIsNone(self.tx(lambda db:store.claim(db,'d'*32)))
        self.assertFalse(self.tx(lambda db:store.renew(db,self.run_id,self.worker)))
        with self.assertRaises(store.LeaseLost):self.tx(lambda db:store.renew(db,self.run_id,'d'*32))

    def test_expired_stages_are_interrupted_never_requeued(self):
        for stage in ['claimed','repairing','validating','baseline_validation']:
            self.run_id=__import__('uuid').uuid4().hex
            with self.sessions.begin() as db:
                for row in db.scalars(select(ProjectRun)): row.active_project=None
            self.submit(); self.tx(lambda db:store.claim(db,self.worker))
            def expire(db):
                row=db.get(ProjectRun,self.run_id); row.status=stage
                row.lease_expires_at=store.now(db)-timedelta(seconds=1)
            self.tx(expire)
            self.assertEqual(self.tx(store.recover),1)
            row=self.get(); self.assertEqual(row.status,'interrupted'); self.assertEqual(row.active_project,self.project)
            self.assertIsNone(self.tx(lambda db:store.claim(db,'d'*32)))
            with self.assertRaises(store.LeaseLost):self.tx(lambda db:store.renew(db,self.run_id,self.worker))

    def test_queued_survives_restart(self):
        self.submit(); self.assertEqual(self.tx(store.recover),0)
        row=self.dispatch(); self.assertEqual(row.status,'completed')

    def test_cancel_queued_persists_and_releases(self):
        self.submit(); self.tx(lambda db:store.request_cancel(db,self.project,self.run_id))
        self.assertEqual(self.get().status,'cancelled'); self.assertIsNone(self.get().active_project)
        self.assertIsNone(self.tx(lambda db:store.claim(db,self.worker)))

    def test_cancel_during_repair(self):
        self.submit()
        def worker(*args):
            self.tx(lambda db:store.request_cancel(db,self.project,self.run_id))
            return self.fix(*args)
        row=self.dispatch(worker=worker)
        self.assertEqual(row.status,'cancelled'); self.assertTrue(row.cleanup_confirmed)
        self.assertIsNone(row.active_project)

    def test_cancel_during_validation(self):
        self.submit()
        def validate(p,r):
            self.tx(lambda db:store.request_cancel(db,self.project,self.run_id))
            return self.validate(p,r)
        row=self.dispatch(validator=validate)
        self.assertEqual(row.status,'cancelled'); self.assertTrue(row.cleanup_confirmed)

    def test_success_attempts_validations_and_event_order(self):
        self.submit(); row=self.dispatch()
        self.assertEqual(row.status,'completed'); self.assertEqual(row.failure_class,'repaired_successfully')
        with self.sessions() as db:
            events=list(db.scalars(select(RunEvent).where(RunEvent.run_id==self.run_id).order_by(RunEvent.sequence)))
            self.assertEqual([e.sequence for e in events],list(range(1,len(events)+1)))
            self.assertEqual(events[-1].event_type,'run_completed')
            self.assertEqual(len(list(db.scalars(select(ModelAttempt)))),1)
            self.assertEqual(db.scalar(select(ModelAttempt)).changed_files,['script.js'])
            values=list(db.scalars(select(RunValidation).order_by(RunValidation.cycle)))
            self.assertEqual([v.passed for v in values],[False,True])

    def test_failed_run(self):
        self.submit(); row=self.dispatch(worker=lambda *a:{'success':False,'error':'provider_failure'})
        self.assertEqual(row.status,'failed'); self.assertEqual(row.failure_class,'provider_failure')

    def test_cleanup_failure_keeps_reservation(self):
        self.submit()
        def fail(*a):raise RuntimeError('private exception')
        row=self.dispatch(worker=fail)
        self.assertEqual(row.status,'failed'); self.assertFalse(row.cleanup_confirmed)
        self.assertEqual(row.active_project,self.project)

    def test_success_claim_without_validation_rejected(self):
        self.submit(); self.tx(lambda db:store.claim(db,self.worker))
        self.tx(lambda db:store.finish(db,self.run_id,self.worker,dict(status='repaired_successfully',
            worker_cleanup_confirmed=True,validations=[],final_revision=self.revision)))
        self.assertEqual(self.get().status,'failed')

    def test_uncertain_cleanup_cannot_be_final_cancelled(self):
        self.submit(); self.tx(lambda db:store.claim(db,self.worker))
        self.tx(lambda db:store.request_cancel(db,self.project,self.run_id))
        self.tx(lambda db:store.finish(db,self.run_id,self.worker,dict(status='cancelled',
            worker_cleanup_confirmed=True,validations=[{'cleanup_confirmed':False}],final_revision=self.revision)))
        self.assertEqual(self.get().status,'failed')
        self.assertEqual(self.get().active_project,self.project)

    def test_retention_keeps_baseline_and_latest(self):
        folder=repair.RUNTIME/self.run_id; folder.mkdir(parents=True)
        for index in range(4):
            (folder/str(index)).mkdir(); (folder/str(index)/'script.js').write_text(str(index))
            (folder/(str(index)+'.json')).write_text('{}')
        retain(self.run_id,{'checkpoints':[{'checkpoint':i,'revision':str(i)} for i in range(4)],
            'validations':[{'revision':'2','status':'passed','cleanup_confirmed':True}]})
        self.assertEqual({p.name for p in folder.iterdir() if p.is_dir()},{'0','2','3'})

    def test_heartbeat_observes_persistent_cancellation(self):
        self.submit()
        def worker(p,r,errors,event,timeout):
            self.tx(lambda db:store.request_cancel(db,self.project,self.run_id))
            self.assertTrue(event.wait(4),'Lease heartbeat did not observe cancellation')
            return {'success':False,'error':'provider_failure'}
        row=self.dispatch(worker=worker)
        self.assertEqual(row.status,'cancelled')

    def test_lost_lease_stops_worker_and_quarantines(self):
        self.submit()
        def worker(p,r,errors,event,timeout):
            def expire(db):db.get(ProjectRun,self.run_id).lease_expires_at=store.now(db)-timedelta(seconds=1)
            self.tx(expire)
            self.assertTrue(event.wait(4),'Lease loss did not stop worker')
            return {'success':False}
        self.dispatch(worker=worker)
        self.tx(store.recover)
        self.assertEqual(self.get().status,'interrupted')
        self.assertEqual(self.get().active_project,self.project)

    def test_operator_recovery_requires_confirmed_quiescence(self):
        from hermes_run_recovery import recover_run
        self.submit(); self.tx(lambda db:store.claim(db,self.worker))
        def expire(db):db.get(ProjectRun,self.run_id).lease_expires_at=store.now(db)-timedelta(seconds=1)
        self.tx(expire); self.tx(store.recover)
        with patch('hermes_run_recovery.confirm_quiescent',side_effect=ValueError('active')), self.assertRaises(ValueError):
            recover_run(self.sessions,self.run_id)
        self.assertEqual(self.get().active_project,self.project)
        with patch('hermes_run_recovery.confirm_quiescent'):
            recover_run(self.sessions,self.run_id)
        self.assertIsNone(self.get().active_project)
        self.assertEqual(self.get().status,'interrupted')


class RunApiTests(unittest.TestCase):
    def test_submit_retrieve_events_cancel(self):
        fixture=test_projects.ProjectTests(); fixture.setUp()
        try:
            p=fixture.create()['project_id']; r=ws.capture_revision(p)[0]
            root=f'/api/projects/{p}/runs'
            status,data=request('POST',root,{'run_id':'f'*32,'revision':r})
            self.assertEqual(status,202); self.assertEqual(data['status'],'queued')
            self.assertEqual(request('GET',root)[0],200)
            self.assertEqual(request('GET',root+'/'+'f'*32)[1]['status'],'queued')
            events=request('GET',root+'/'+'f'*32+'/events')[1]
            self.assertEqual(events[0]['event_type'],'run_created')
            self.assertEqual(request('POST',root+'/'+'f'*32+'/cancel')[1]['status'],'cancelled')
        finally:fixture.doCleanups()
