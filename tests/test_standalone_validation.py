"""Trusted standalone validation -> durable run -> unchanged publication gate."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from app.models import Base, Project
from app.run_models import ProjectRun, RunValidation, RunEvent, ModelAttempt
from app.publication_models import Publication
from app.migrations import migrate
from app import project_workspace as ws, publication_service as publication, run_store
from app.main import app
from app.database import get_db
from app.routers.projects import validate_existing_project
from app.validation_contracts import ValidateProject
from app.github_publication import PublicationFailure
from hermes_publication_export import export_project
import hermes_repair
from test_projects import request


class StandaloneValidationTests(unittest.TestCase):
    def make_engine(self):
        return create_engine('sqlite:///' + str(self.root/'db.sqlite'))

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for obj, key, value in [(ws,'ROOT',self.root/'projects'), (hermes_repair,'RUNTIME',self.root/'repairs')]:
            context=patch.object(obj,key,value); context.start(); self.addCleanup(context.stop)
        env=patch.dict(os.environ,{'PROJECT_BUILDER_TOKEN':'t'*40, 'HERMES_ROUTER_URL':'http://trusted-host.invalid',
                                   'PUBLICATION_ORIGIN':'http://test', 'GITHUB_PUBLICATION_ENABLED':'0'})
        env.start(); self.addCleanup(env.stop)
        self.engine=self.make_engine(); self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine, tables=[Project.__table__]); migrate(self.engine)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        self.project='a'*32
        ws.workspace(self.project,create=True)
        ws.write_file(self.project,'index.html','<!doctype html><title>Validation</title>')
        self.revision=ws.capture_revision(self.project)[0]
        with self.sessions.begin() as db:
            db.add(Project(id=self.project,name='Fixture',original_prompt='fixture',status='completed'))
        def database():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db]=database
        self.addCleanup(app.dependency_overrides.clear)
        self.base='/api/projects/'+self.project
        self.result={'schema_version':1,'project_id':self.project,'revision':self.revision,
            'current_revision':self.revision,'profile':'static-web-v1','passed':True,'status':'passed',
            'cleanup_confirmed':True,'duration_ms':10,'errors':[]}

    def validate(self, result=None):
        result=copy.deepcopy(self.result if result is None else result)
        def host(url, **kwargs):
            self.assertEqual(url,'http://trusted-host.invalid/projects/validate')
            self.assertEqual(kwargs['headers'],{'X-Project-Token':'t'*40})
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['json'],{'project_id':self.project,'revision':self.revision})
            return Mock(status_code=200,json=lambda:result)
        with patch('app.routers.projects.requests.post',side_effect=host):
            return request('POST',self.base+'/validate',{'revision':self.revision})

    def preview(self, revision=None):
        with self.sessions.begin() as db:
            return publication.preview(db,self.project,revision or ws.capture_revision(self.project)[0],
                                       'example',export_project)

    def no_eligibility(self):
        with self.assertRaisesRegex(PublicationFailure,'validation_required'): self.preview()
        self.assertFalse(request('GET',self.base+'/publications/eligibility')[1]['eligible'])

    def test_pass_persists_and_preview_is_allowed_from_new_session(self):
        self.no_eligibility()
        status, result=self.validate(); self.assertEqual(status,200)
        with self.sessions() as db:
            run=db.get(ProjectRun,result['run_id'])
            self.assertEqual((run.operation,run.status,run.max_repairs,run.repair_count,run.validation_count),
                             ('validation','completed',0,0,1))
            self.assertFalse(run.automatic_repair); self.assertIsNone(run.active_project)
            validation=db.scalar(select(RunValidation).where(RunValidation.run_id==run.id))
            self.assertEqual((validation.revision,validation.profile,validation.passed,validation.cleanup_confirmed),
                             (self.revision,'static-web-v1',True,True))
            self.assertEqual(db.scalar(select(func.count()).select_from(ModelAttempt)),0)
            started=db.scalar(select(RunEvent).where(RunEvent.run_id==run.id, RunEvent.sequence==1))
            self.assertEqual(started.created_at,run.started_at)
        with self.sessions.begin() as db: self.assertIsNone(run_store.claim(db,'c'*32))
        history=request('GET',self.base+'/runs')[1]
        self.assertEqual(history[0]['operation'],'validation')
        detail=request('GET',self.base+'/runs/'+result['run_id'])[1]
        self.assertEqual(len(detail['validations']),1); self.assertEqual(detail['attempts'],[])
        self.assertEqual([e['event_type'] for e in request('GET',self.base+'/runs/'+result['run_id']+'/events')[1]],
                         ['validation_started','validation_passed'])
        self.assertTrue(request('GET',self.base+'/publications/eligibility')[1]['eligible'])
        plan=self.preview(); self.assertEqual(plan.status,'preview')
        with self.sessions() as db:
            self.assertIsNone(db.get(Publication,plan.id).active_repository)

    def test_failed_validation_is_durable_but_not_eligible(self):
        result={**self.result,'passed':False,'status':'failed','errors':[{
            'file':'index.html','line':1,'column':1,'check':'html-syntax','code':'syntax_error','message':'sensitive content'}]}
        status, body=self.validate(result); self.assertEqual(status,200)
        self.no_eligibility()
        with self.sessions() as db:
            self.assertEqual(db.get(ProjectRun,body['run_id']).failure_class,'validation_failed')
            self.assertNotIn('sensitive',str(db.scalar(select(RunValidation)).errors))

    def test_old_revision_does_not_authorize_current_revision(self):
        self.assertEqual(self.validate()[0],200)
        ws.write_file(self.project,'index.html','<!doctype html><title>Changed</title>')
        self.no_eligibility()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(RunValidation)).revision,self.revision)

    def test_cleanup_false_or_missing_never_authorizes(self):
        status, body=self.validate({**self.result,'cleanup_confirmed':False})
        self.assertEqual(status,200); self.no_eligibility()
        with self.sessions() as db:
            self.assertFalse(db.scalar(select(RunValidation)).cleanup_confirmed)
            self.assertEqual(db.get(ProjectRun,body['run_id']).status,'failed')
        missing=dict(self.result); missing.pop('cleanup_confirmed')
        self.assertEqual(self.validate(missing)[0],503); self.no_eligibility()

    def test_invalid_profile_revision_project_and_contract_are_rejected(self):
        for change in ({'profile':'different-profile'},{'revision':'b'*64},{'project_id':'b'*32},
                       {'current_revision':'b'*64},{'passed':'true'},{'status':'failed'}, {'cleanup_confirmed':'true'}):
            with self.subTest(change=change): self.assertEqual(self.validate({**self.result,**change})[0],503)
        self.no_eligibility()
        with self.sessions() as db: self.assertEqual(db.scalar(select(func.count()).select_from(ProjectRun)),0)

    def test_repeats_append_independent_runs_and_preserve_repair_history(self):
        with self.sessions.begin() as db:
            run_store.submit(db,self.project,'b'*32,self.revision)
            run=run_store.claim(db,'c'*32)
            run_store.observe(db,run,'c'*32,'validation_finished',{'result':self.result})
            run_store.finish(db,run,'c'*32,{'status':'repaired_successfully','final_revision':self.revision,
                'worker_cleanup_confirmed':True,'validations':[self.result]})
        self.assertEqual(self.preview().status,'preview')
        ids=[self.validate()[1]['run_id'] for _ in range(3)]
        self.assertEqual(len(set(ids)),3)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RunValidation)),4)
            self.assertEqual(db.get(ProjectRun,'b'*32).operation,'repair')
            self.assertEqual(db.get(ProjectRun,'b'*32).failure_class,'repaired_successfully')
            for ident in ids:
                self.assertEqual([e.sequence for e in db.scalars(select(RunEvent).where(RunEvent.run_id==ident)
                    .order_by(RunEvent.sequence))],[1,2])

    def test_stale_request_does_not_call_host_and_mid_request_mutation_is_rejected(self):
        with patch('app.routers.projects.requests.post') as host:
            self.assertEqual(request('POST',self.base+'/validate',{'revision':'b'*64})[0],409)
            host.assert_not_called()
        def response(*a,**kw):
            ws.write_file(self.project,'index.html','changed during validation')
            return Mock(status_code=200,json=lambda:self.result)
        with patch('app.routers.projects.requests.post',side_effect=response):
            self.assertEqual(request('POST',self.base+'/validate',{'revision':self.revision})[0],409)
        with self.sessions() as db: self.assertEqual(db.scalar(select(func.count()).select_from(RunValidation)),0)

    def test_auth_failure_redirect_and_timeout_create_no_evidence(self):
        import requests
        with patch.dict(os.environ,{'PROJECT_BUILDER_TOKEN':''}), patch('app.routers.projects.requests.post') as host:
            self.assertEqual(request('POST',self.base+'/validate',{'revision':self.revision})[0],503)
            host.assert_not_called()
        for response in (Mock(status_code=302,json=lambda:self.result), Mock(status_code=401)):
            if response.status_code==401: response.raise_for_status.side_effect=requests.HTTPError()
            with patch('app.routers.projects.requests.post',return_value=response):
                self.assertEqual(request('POST',self.base+'/validate',{'revision':self.revision})[0],503)
        with patch('app.routers.projects.requests.post',side_effect=requests.Timeout()):
            self.assertEqual(request('POST',self.base+'/validate',{'revision':self.revision})[0],504)
        self.no_eligibility()

    def test_commit_failure_rolls_back_entire_history(self):
        with self.sessions() as db, patch.object(db,'commit',side_effect=OperationalError('commit',{},Exception())), \
             patch('app.routers.projects.requests.post',return_value=Mock(status_code=200,json=lambda:self.result)):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as error:
                validate_existing_project(self.project,ValidateProject(revision=self.revision),db)
            self.assertEqual(error.exception.status_code,503)
        with self.sessions() as db:
            for model in (ProjectRun,RunValidation,RunEvent):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)),0)

    def test_active_repair_still_blocks_gate(self):
        self.assertEqual(self.validate()[0],200)
        with self.sessions.begin() as db: run_store.submit(db,self.project,'b'*32,self.revision)
        with self.assertRaisesRegex(PublicationFailure,'invalid_revision'): self.preview()


if __name__=='__main__': unittest.main()
