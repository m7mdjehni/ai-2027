"""Offline Phase 2 tests: temporary managed projects, mocked Hermes/validator."""
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from app import project_workspace as ws
from app.repair_contracts import RepairProject
from hermes_project_worker import ProjectToolBoundary, RepairStop
import hermes_repair as repair
import test_projects
from test_projects import request
from hermes_service import app as host_app


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for obj, key, value in [(ws, 'ROOT', Path(self.temp.name) / 'projects'),
                                (repair, 'RUNTIME', Path(self.temp.name) / 'runtime' / 'repairs')]:
            context = patch.object(obj, key, value)
            context.start(); self.addCleanup(context.stop)
        self.project = 'a' * 32
        self.run_id = 'b' * 32
        ws.workspace(self.project, create=True)
        ws.write_file(self.project, 'index.html', '<!doctype html><title>Test</title><script src="script.js"></script>')
        ws.write_file(self.project, 'script.js', 'const broken = ;')
        self.revision = ws.capture_revision(self.project)[0]

    def validation(self, project, revision):
        content = ws.read_file(project, 'script.js')
        errors = [] if content == 'const repaired = 1;' else [dict(file='script.js', line=1, column=16,
            check='javascript-syntax', code=content, message='JavaScript syntax is invalid.')]
        return dict(schema_version=1, profile='static-web-v1', revision=revision,
                    current_revision=revision, passed=not errors, status='failed' if errors else 'passed',
                    errors=errors, warnings=[], cleanup_confirmed=True)

    def worker(self, project, revision, errors, event, timeout):
        self.assertEqual(ws.capture_revision(project)[0], revision)
        self.assertLessEqual(timeout, repair.REPAIR_SECONDS)
        ws.write_file(project, 'script.js', 'const repaired = 1;')
        return {'success': True, 'revision': ws.capture_revision(project)[0]}

    def execute(self, worker=None, validator=None):
        with patch.object(repair, 'repair_worker', side_effect=worker or self.worker) as model, \
             patch.object(repair, 'validate_project', side_effect=validator or self.validation) as check:
            result = repair.repair_project(self.project, self.revision, self.run_id)
        self.assertFalse(repair.ACTIVE)
        return result, model, check

    def test_broken_repaired_and_protected_baseline(self):
        result, _, _ = self.execute()
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'repaired_successfully')
        self.assertFalse(result['baseline']['passed'])
        self.assertEqual(len(result['validations']), 2)
        saved = repair.RUNTIME / self.run_id / '0' / 'script.js'
        self.assertEqual(saved.read_text(), 'const broken = ;')
        self.assertNotIn(str(saved), json.dumps(result))
        self.assertFalse((repair.RUNTIME / (self.project + '.lock')).exists())

    def test_valid_no_repairs(self):
        ws.write_file(self.project, 'script.js', 'const repaired = 1;')
        self.revision = ws.capture_revision(self.project)[0]
        result, model, _ = self.execute()
        self.assertEqual(result['reason'], 'already_valid'); model.assert_not_called()

    def test_multiple_errors_and_new_error_continue(self):
        steps = iter(['second error', 'new introduced error', 'const repaired = 1;'])
        def worker(project, revision, errors, event, timeout):
            ws.write_file(project, 'script.js', next(steps))
            return {'success': True, 'revision': ws.capture_revision(project)[0]}
        result, model, check = self.execute(worker)
        self.assertTrue(result['success']); self.assertEqual(model.call_count, 3)
        self.assertEqual(check.call_count, 4)

    def test_maximum_repairs(self):
        steps = iter(['error2', 'error3', 'error4'])
        def worker(project, *args):
            ws.write_file(project, 'script.js', next(steps))
            return {'success': True, 'revision': ws.capture_revision(project)[0]}
        result, model, check = self.execute(worker)
        self.assertEqual(result['status'], 'unresolved_validation_errors')
        self.assertEqual((model.call_count, check.call_count), (3, 4))

    def test_identical_errors_despite_changed_bytes_stop(self):
        def validator(project, revision):
            value = self.validation(project, revision)
            for error in value['errors']: error['code'] = 'syntax_error'
            return value
        def worker(project, *args):
            ws.write_file(project, 'script.js', '// irrelevant change\nconst broken = ;')
            return {'success': True, 'revision': ws.capture_revision(project)[0]}
        result, model, _ = self.execute(worker, validator)
        self.assertEqual(result['status'], 'no_progress'); self.assertEqual(model.call_count, 1)

    def test_two_no_change_repairs(self):
        result, model, check = self.execute(lambda p, r, *args: {'success': True, 'revision': r})
        self.assertEqual(result['reason'], 'two_unchanged_repairs')
        self.assertEqual((model.call_count, check.call_count), (2, 3))

    def test_stale_rejected_before_validator(self):
        ws.write_file(self.project, 'script.js', 'changed')
        with patch.object(repair, 'validate_project') as validator, self.assertRaises(repair.RepairConflict):
            repair.repair_project(self.project, self.revision, self.run_id)
        validator.assert_not_called()

    def test_cancel_during_validator_waits_for_cleanup(self):
        cleaned = []
        def validator(project, revision):
            self.assertTrue(repair.cancel(project, self.run_id))
            cleaned.append(True)
            return self.validation(project, revision)
        result, model, _ = self.execute(validator=validator)
        self.assertTrue(cleaned); self.assertEqual(result['status'], 'cancelled'); model.assert_not_called()

    def test_cancel_worker_preserves_partial_checkpoint(self):
        def worker(project, revision, errors, event, timeout):
            ws.write_file(project, 'script.js', 'partial edit')
            event.set()
            raise subprocess.TimeoutExpired('mock', timeout)
        result, _, check = self.execute(worker)
        self.assertEqual(result['status'], 'cancelled'); self.assertEqual(check.call_count, 1)
        self.assertEqual((repair.RUNTIME / self.run_id / '1' / 'script.js').read_text(), 'partial edit')

    def test_deadline_before_start(self):
        with patch.object(repair, 'RUN_SECONDS', 1):
            result, model, check = self.execute()
        self.assertEqual(result['status'], 'deadline_exceeded')
        model.assert_not_called(); check.assert_not_called()

    def test_deadline_during_worker(self):
        clock = [0]
        def worker(*args):
            clock[0] = 601
            return {'success': True, 'revision': self.revision}
        with patch.object(repair.time, 'monotonic', side_effect=lambda: clock[0]):
            result, _, _ = self.execute(worker)
        self.assertEqual(result['status'], 'deadline_exceeded')

    def test_provider_failure_and_unvalidated_edits(self):
        def worker(project, *args):
            ws.write_file(project, 'script.js', 'const repaired = 1;')
            return {'success': False, 'error': 'provider_failure'}
        result, _, _ = self.execute(worker)
        self.assertEqual(result['status'], 'provider_failure'); self.assertFalse(result['success'])
        self.assertEqual(len(result['checkpoints']), 2)

    def test_security_violation_stops(self):
        result, model, check = self.execute(lambda *args: {'success': False, 'error': 'security_violation'})
        self.assertEqual(result['status'], 'security_violation')
        self.assertEqual((model.call_count, check.call_count), (1, 1))

    def test_cleanup_unconfirmed_retains_lock(self):
        def worker(*args): raise RuntimeError('private process detail')
        result, _, _ = self.execute(worker)
        self.assertEqual(result['reason'], 'worker_cleanup_unconfirmed')
        self.assertFalse(result['worker_cleanup_confirmed'])
        self.assertTrue((repair.RUNTIME / (self.project + '.lock')).exists())
        self.assertNotIn('private', json.dumps(result))

    def test_infrastructure_failure_not_success(self):
        for mode in ['timeout', 'exception']:
            with self.subTest(mode=mode):
                self.run_id = ('c' if mode == 'timeout' else 'd') * 32
                def validator(*args):
                    if mode == 'exception': raise OSError('secret')
                    return dict(status='timeout', passed=False, revision=self.revision, cleanup_confirmed=True)
                result, model, _ = self.execute(validator=validator)
                self.assertEqual(result['status'], 'validation_infrastructure_failure'); model.assert_not_called()

    def test_final_pass_requires_matching_revision_and_cleanup(self):
        for changes in [{'revision': '0'*64}, {'cleanup_confirmed': False}, {'status': 'failed', 'passed': True}]:
            self.run_id = __import__('uuid').uuid4().hex
            def validator(p, r): return {**self.validation(p, r), **changes}
            result, _, _ = self.execute(validator=validator)
            self.assertFalse(result['success'])

    def test_single_modifying_run(self):
        def worker(*args):
            with self.assertRaises(repair.RepairConflict):
                repair.repair_project(self.project, self.revision, 'c'*32)
            return {'success': False}
        self.execute(worker)

    def test_feedback_is_bounded(self):
        errors = [dict(file='script.js', line=1, column=1, check='js', code='bad', message='x'*900,
                       command='terminal', token='sensitive')] * 100
        output = repair.feedback({'errors': errors})
        self.assertEqual(len(output), 30); self.assertEqual(len(output[0]['message']), 200)
        self.assertNotIn('command', json.dumps(output)); self.assertNotIn('sensitive', json.dumps(output))

    def test_repair_boundary_fail_stop_and_attempt_limits(self):
        for name, args in [('terminal', {}), ('project_read_file', {'path': '../outside.txt'}),
                           ('project_write_file', {'path':'script.js','content':'ok','command':'evil'})]:
            agent = self.agent()
            with self.assertRaises(RepairStop): agent._invoke_tool(name, args)
            with self.assertRaises(RepairStop): agent._invoke_tool('project_list_files', {})
        agent = self.agent()
        for _ in range(24): agent._invoke_tool('project_list_files', {})
        with self.assertRaises(RepairStop): agent._invoke_tool('project_list_files', {})
        agent = self.agent()
        for _ in range(24): agent._repair_api_gate()
        with self.assertRaises(RepairStop): agent._repair_api_gate()

    def agent(self):
        agent = ProjectToolBoundary()
        agent.project_id = self.project; agent.repair_mode = True; agent.repair_stopped = None
        agent.repair_revision = ws.capture_revision(self.project)[0]; agent.repair_tool_calls = 0
        return agent

    def test_atomic_failure_leaves_original(self):
        with patch.object(ws.os, 'replace', side_effect=OSError('failure')), self.assertRaises(OSError):
            ws.write_file(self.project, 'script.js', 'replacement')
        self.assertEqual(ws.read_file(self.project, 'script.js'), 'const broken = ;')
        self.assertEqual(len(ws.list_files(self.project)), 2)

    def test_stale_revision_at_tool_boundary(self):
        agent = self.agent()
        ws.write_file(self.project, 'script.js', 'external change')
        with self.assertRaises(RepairStop):
            agent._invoke_tool('project_write_file', {'path':'script.js','content':'overwrite'})
        self.assertEqual(ws.read_file(self.project,'script.js'),'external change')

    def test_stale_revision_during_validation(self):
        def validator(project, revision):
            ws.write_file(project,'script.js','external change')
            return self.validation(project,revision)
        result, model, _ = self.execute(validator=validator)
        self.assertEqual(result['status'],'security_violation'); model.assert_not_called()

    def test_reused_run_id_and_persistent_lock_rejected(self):
        self.execute()
        current = ws.capture_revision(self.project)[0]
        with self.assertRaises(repair.RepairConflict):
            repair.repair_project(self.project,current,self.run_id)
        (repair.RUNTIME/(self.project+'.lock')).write_text('stale-process-lock')
        with self.assertRaises(repair.RepairConflict):
            repair.repair_project(self.project,current,'c'*32)

    def test_cancel_requires_matching_run(self):
        def worker(project, revision, *args):
            self.assertFalse(repair.cancel(project,'c'*32))
            self.assertTrue(repair.cancel(project,self.run_id))
            return {'success':True,'revision':revision}
        result, _, _ = self.execute(worker)
        self.assertEqual(result['status'],'cancelled')

    def test_error_oscillation_stops(self):
        steps = iter(['different error','const broken = ;'])
        def worker(project,*args):
            ws.write_file(project,'script.js',next(steps))
            return {'success':True,'revision':ws.capture_revision(project)[0]}
        result, model, _ = self.execute(worker)
        self.assertEqual(result['status'],'no_progress')
        self.assertEqual(model.call_count,2)

    def test_attempt_budget_exhausted_is_unresolved(self):
        result, _, check = self.execute(lambda *args: {'success':False,'error':'tool_attempt_limit'})
        self.assertEqual(result['status'],'unresolved_validation_errors')
        self.assertEqual(check.call_count,1)


class RepairApiTests(unittest.TestCase):
    def test_auth_and_schema(self):
        import os
        with patch.dict(os.environ, {'PROJECT_BUILDER_TOKEN': 't'*40}), patch.object(repair, 'repair_project') as worker:
            payload = {'project_id':'a'*32, 'run_id':'b'*32, 'revision':'c'*64}
            self.assertEqual(request('POST','/projects/repair',payload,application=host_app)[0],401)
            payload['command'] = 'evil'
            self.assertEqual(request('POST','/projects/repair',payload,application=host_app,
                headers=[(b'x-project-token',b't'*40)])[0],422)
            worker.assert_not_called()

    def test_backend_managed_project_repair_and_cancel(self):
        fixture = test_projects.ProjectTests()
        fixture.setUp()
        try:
            project = fixture.create()['project_id']
            payload = {'run_id':'b'*32,'revision':ws.capture_revision(project)[0]}
            response = Mock(status_code=200, json=lambda: {'status':'cancelled','success':False})
            with patch('app.routers.projects.requests.post', return_value=response) as host:
                self.assertEqual(request('POST',f'/api/projects/{project}/repair',payload)[0],200)
                self.assertEqual(host.call_args.kwargs['json']['project_id'],project)
                self.assertEqual(request('POST',f'/api/projects/{project}/repair/'+'b'*32+'/cancel')[0],200)
        finally:
            fixture.doCleanups()
