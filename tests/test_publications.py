"""Offline publication tests. No GitHub, Hermes or generated code is executed."""
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, Mock
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from app.models import Base, Project
from app.run_models import ProjectRun, RunValidation
from app.publication_models import Publication
from app.migrations import migrate
from app import project_workspace as ws
from app import publication_service as service
from app.publication_export import validate_artifact
from app.github_publication import GitHubPublisher, PublicationFailure
from hermes_publication_export import export_project
import hermes_repair


class Publications(unittest.TestCase):
    def make_engine(self):
        return create_engine('sqlite:///' + str(self.root/'db.sqlite'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for obj, key, value in [(ws, 'ROOT', self.root/'projects'), (hermes_repair, 'RUNTIME', self.root/'repairs')]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)
        self.engine = self.make_engine()
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine, tables=[Project.__table__]); migrate(self.engine); migrate(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.project = 'a'*32; self.run = 'b'*32
        ws.workspace(self.project, create=True)
        ws.write_file(self.project, 'index.html', '<!doctype html><title>Publication</title>')
        ws.write_file(self.project, 'script.js', 'throw new Error("must never execute");')
        self.revision, self.files = ws.capture_revision(self.project)
        with self.sessions.begin() as db:
            db.add(Project(id=self.project, name='Fixture', original_prompt='fixture', status='completed'))
            db.add(ProjectRun(id=self.run, project_id=self.project, requested_revision=self.revision,
                status='completed', created_at=service.timestamp()))
            db.flush()
            db.add(RunValidation(run_id=self.run, cycle=0, revision=self.revision, profile='static-web-v1',
                passed=True, status='passed', errors=[], cleanup_confirmed=True))

    def preview(self):
        with self.sessions.begin() as db:
            return service.preview(db, self.project, self.revision, 'example', export_project)

    def test_ready_preview_requires_explicit_confirmation(self):
        with self.sessions.begin() as db: db.get(Project, self.project).status = 'ready'
        row = self.preview()
        self.assertEqual(row.status, 'preview')
        self.assertIsNone(row.active_repository)
        factory = Mock(side_effect=AssertionError('Preview cannot publish'))
        service.execute(row.id, self.files, self.sessions, factory)
        factory.assert_not_called()
        with self.sessions() as db: self.assertEqual(db.get(Publication, row.id).status, 'preview')

    def test_ready_security_gates_remain_required(self):
        with self.sessions.begin() as db: db.get(Project, self.project).status = 'ready'
        for field, bad in [('passed', False), ('cleanup_confirmed', False), ('status', 'failed'), ('revision', 'f'*64)]:
            with self.subTest(field=field):
                with self.sessions.begin() as db:
                    validation = db.scalar(select(RunValidation)); old = getattr(validation, field); setattr(validation, field, bad)
                with self.assertRaises(PublicationFailure): self.preview()
                with self.sessions.begin() as db: setattr(db.scalar(select(RunValidation)), field, old)
        with self.sessions.begin() as db: db.get(ProjectRun, self.run).active_project = self.project
        with self.assertRaises(PublicationFailure): self.preview()
        with self.sessions.begin() as db: db.get(ProjectRun, self.run).active_project = None
        ws.write_file(self.project, 'server.py', 'unsupported')
        self.revision = ws.capture_revision(self.project)[0]
        with self.sessions.begin() as db: db.scalar(select(RunValidation)).revision = self.revision
        with self.assertRaises(ValueError): self.preview()

    def test_ready_without_validation_blocked(self):
        with self.sessions.begin() as db:
            db.get(Project, self.project).status = 'ready'
            db.delete(db.scalar(select(RunValidation)))
        with self.assertRaises(PublicationFailure): self.preview()

    def test_ineligible_project_statuses_stay_blocked(self):
        for status in ['failed', 'building', 'repairing', 'interrupted', 'invalid']:
            with self.subTest(status=status):
                with self.sessions.begin() as db: db.get(Project, self.project).status = status
                with self.assertRaises(PublicationFailure): self.preview()

    def confirm(self, row):
        with self.sessions.begin() as db:
            return service.confirm(db, self.project, row.id, export_project)

    def test_validated_publication_persists_real_stages(self):
        row, artifact = self.confirm(self.preview())
        adapter = Mock()
        def publish(repo, files, previous, emit):
            self.assertEqual(files, self.files); self.assertIsNone(previous)
            for stage in ['repository_creating', 'uploading', 'deploying']: emit(stage)
            emit('completed', commit_sha='c'*40, public_url='https://example.github.io/managed-'+self.project+'/')
        adapter.publish.side_effect = publish
        with patch('subprocess.Popen', side_effect=AssertionError('No commands')):
            service.execute(row.id, artifact, self.sessions, lambda: adapter)
        with self.sessions() as db:
            result = db.get(Publication, row.id)
            self.assertEqual(result.status, 'completed')
            self.assertIsNone(result.active_repository)
            self.assertEqual([e['state'] for e in result.events], ['pending','exporting','repository_creating','uploading','deploying','completed'])
        self.assertEqual(ws.capture_revision(self.project)[0], self.revision)

    def test_unvalidated_and_unconfirmed_cleanup_blocked(self):
        for field in ['passed', 'cleanup_confirmed']:
            with self.sessions.begin() as db:
                validation = db.scalar(select(RunValidation)); setattr(validation, field, False)
            with self.assertRaisesRegex(PublicationFailure, 'validation_required'): self.preview()
            with self.sessions.begin() as db: setattr(db.scalar(select(RunValidation)), field, True)

    def test_active_lease_and_filesystem_lock_block(self):
        with self.sessions.begin() as db: db.get(ProjectRun, self.run).active_project = self.project
        with self.assertRaises(PublicationFailure): self.preview()
        with self.sessions.begin() as db: db.get(ProjectRun, self.run).active_project = None
        hermes_repair.RUNTIME.mkdir()
        (hermes_repair.RUNTIME/(self.project+'.lock')).write_text('existing')
        with self.assertRaises(FileExistsError): self.preview()
        self.assertEqual((hermes_repair.RUNTIME/(self.project+'.lock')).read_text(), 'existing')

    def test_stale_revision_and_changed_preview_rejected(self):
        row = self.preview()
        ws.write_file(self.project, 'script.js', 'const changed = 1;')
        with self.assertRaises(ValueError): self.confirm(row)
        self.assertFalse((hermes_repair.RUNTIME/(self.project+'.lock')).exists())

    def test_unsupported_sensitive_traversal_workflows(self):
        for name in ['server.py', 'package.json', '.env', '.github/workflows/deploy.yml', '../a.js',
                     '/a.js', 'C:/a.js', 'secret.txt', 'logs/app.txt', 'Dockerfile', 'x.png']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_artifact(self.project, self.revision, {**self.files, name: 'unsafe'})

    def test_secret_content_rejected(self):
        for value in ['ghp_'+'x'*40, '-----BEGIN RSA PRIVATE KEY-----', 'const password = "sensitive123";']:
            with self.assertRaises(ValueError):
                validate_artifact(self.project, self.revision, {**self.files, 'script.js': value})

    def test_hardlink_rejected(self):
        os.link(ws.file_path(self.project, 'script.js'), self.root/'outside.js')
        with self.assertRaises(ValueError): export_project(self.project, self.revision)

    def test_reparse_rejected(self):
        info = Mock(st_mode=0, st_file_attributes=0x400)
        with patch.object(Path, 'lstat', return_value=info), self.assertRaises(ValueError):
            ws.reject_link(self.root)

    def test_symlink_rejected_without_windows_privilege(self):
        import stat
        info = Mock(st_mode=stat.S_IFLNK, st_file_attributes=0)
        with patch.object(Path, 'lstat', return_value=info), self.assertRaises(ValueError):
            ws.reject_link(self.root)

    def test_publication_http_routes_are_not_mounted(self):
        from app.main import app
        from test_projects import request
        url = '/api/projects/'+self.project+'/publications/'+'c'*32+'/confirm'
        self.assertEqual(request('POST', url, {}, application=app)[0], 404)

    def test_authenticated_host_handoff_has_no_provider(self):
        from test_projects import request, host_app
        with patch.dict(os.environ, {'PROJECT_BUILDER_TOKEN':'t'*40}), \
             patch('hermes_run_dispatcher.host_sessions', return_value=self.sessions), \
             patch('subprocess.Popen', side_effect=AssertionError('No provider')):
            body = {'project_id':self.project,'revision':self.revision}
            self.assertEqual(request('POST','/projects/publication-export',body,application=host_app)[0],401)
            status, result = request('POST','/projects/publication-export',body,application=host_app,
                headers=[(b'x-project-token',b't'*40)])
            self.assertEqual(status,200); self.assertEqual(result['files'],self.files)

    def test_duplicate_confirmation_and_repository_reservation(self):
        first, second = self.preview(), self.preview()
        self.confirm(first)
        with self.assertRaises(PublicationFailure): self.confirm(first)
        with self.assertRaises(IntegrityError): self.confirm(second)

    def test_failure_categories_and_fresh_retry(self):
        for category in ['authentication_failure','repository_failure','upload_failure','deployment_failure',
                         'invalid_revision','validation_required','security_violation','network_failure']:
            row, files = self.confirm(self.preview())
            service.execute(row.id, files, self.sessions, Mock(side_effect=PublicationFailure(category)))
            with self.sessions() as db:
                result = db.get(Publication, row.id)
                self.assertEqual(result.failure_class, category); self.assertEqual(result.status, 'failed')
            self.assertNotEqual(self.preview().id, row.id)

    def test_reconciliation_gates_and_preserves_original_failure(self):
        row = Publication(id=uuid.uuid4().hex, project_id=self.project, revision=self.revision,
            repository='example/managed-'+self.project, commit_sha='c'*40, status='failed',
            failure_class='deployment_failure', files=sorted(self.files), events=[],
            created_at=service.timestamp())
        with self.sessions.begin() as db: db.add(row)
        with self.sessions.begin() as db:
            result = service.reconcile(db, self.project, row.id)
            self.assertEqual(result.reconciliation_status, 'pending')
            self.assertEqual(result.status, 'failed')
            self.assertEqual(result.failure_class, 'deployment_failure')
        with self.sessions() as db:
            result = db.get(Publication, row.id)
            self.assertEqual(result.events[-1]['state'], 'reconciliation_pending')

        with self.sessions.begin() as db:
            db.get(RunValidation, db.scalar(select(RunValidation)).id).cleanup_confirmed = False
        with self.assertRaisesRegex(PublicationFailure, 'validation_required'):
            with self.sessions.begin() as db: service.reconcile(db, self.project, row.id)
        with self.sessions.begin() as db:
            db.scalar(select(RunValidation)).cleanup_confirmed = True
            db.get(ProjectRun, self.run).active_project = self.project
        with self.assertRaisesRegex(PublicationFailure, 'invalid_revision'):
            with self.sessions.begin() as db: service.reconcile(db, self.project, row.id)

    def test_reconciliation_wrong_revision_and_category_are_rejected(self):
        for failure_class, revision in [('upload_failure', self.revision), ('deployment_failure', 'f'*64)]:
            row = Publication(id=uuid.uuid4().hex, project_id=self.project, revision=revision,
                repository='example/managed-'+self.project, commit_sha='c'*40, status='failed',
                failure_class=failure_class, files=sorted(self.files), events=[],
                created_at=service.timestamp())
            with self.sessions.begin() as db: db.add(row)
            with self.assertRaises(PublicationFailure):
                with self.sessions.begin() as db: service.reconcile(db, self.project, row.id)

    def test_actions_migration_gates_and_preserves_history(self):
        row = Publication(id=uuid.uuid4().hex, project_id=self.project, revision=self.revision,
            repository='example/managed-'+self.project, commit_sha='c'*40, status='failed',
            failure_class='deployment_failure', files=sorted(self.files), events=[],
            created_at=service.timestamp())
        with self.sessions.begin() as db: db.add(row)
        with self.sessions.begin() as db:
            result = service.actions_migrate(db, self.project, row.id)
            self.assertEqual(result.actions_status, 'pending')
            self.assertEqual(result.status, 'failed')
            self.assertEqual(result.failure_class, 'deployment_failure')
        with self.sessions.begin() as db:
            db.get(Publication, row.id).actions_status = None
            db.get(ProjectRun, self.run).active_project = self.project
        with self.assertRaisesRegex(PublicationFailure, 'invalid_revision'):
            with self.sessions.begin() as db: service.actions_migrate(db, self.project, row.id)
        with self.sessions.begin() as db:
            db.get(ProjectRun, self.run).active_project = None
            db.get(Publication, row.id).actions_status = None
            db.scalar(select(RunValidation)).cleanup_confirmed = False
        with self.assertRaisesRegex(PublicationFailure, 'validation_required'):
            with self.sessions.begin() as db: service.actions_migrate(db, self.project, row.id)

    def test_credentials_backend_file_only_and_safe_errors(self):
        credential = self.root/'credential'; credential.write_text('fake-offline-credential-only')
        transport = Mock(); transport.headers = {}
        adapter = GitHubPublisher(str(credential), transport)
        transport.request.return_value.status_code = 401
        transport.request.return_value.text = 'must never log credential'
        with self.assertRaisesRegex(PublicationFailure, '^authentication_failure$'):
            adapter.call('GET', '/user', 'repository_failure')
        self.assertFalse(transport.trust_env)
        self.assertFalse(transport.request.call_args.kwargs['allow_redirects'])
        # Host export has no publisher construction or provider invocation.
        with patch('app.github_publication.GitHubPublisher', side_effect=AssertionError('Host must not read credentials')), \
             patch('subprocess.Popen', side_effect=AssertionError('Hermes must not run')):
            self.assertEqual(export_project(self.project, self.revision), self.files)
