"""Real Phase 1 Docker validation around mocked Hermes edits. No provider calls."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import project_workspace as ws
import hermes_repair as repair
import hermes_validation as validator


class RepairDockerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[1] / 'runtime'))
        self.addCleanup(self.temp.cleanup)
        for obj, key, value in [(ws, 'ROOT', Path(self.temp.name) / 'projects'),
                                (repair, 'RUNTIME', Path(self.temp.name) / 'repairs')]:
            context = patch.object(obj, key, value)
            context.start(); self.addCleanup(context.stop)
        self.project = 'e' * 32
        self.run_id = uuid.uuid4().hex
        ws.workspace(self.project, create=True)
        for file in (Path(__file__).parent / 'fixtures/static_valid').iterdir():
            ws.write_file(self.project, file.name, file.read_text())
        ws.write_file(self.project, 'script.js', 'const broken = ;')
        self.revision = ws.capture_revision(self.project)[0]

    def execute(self, worker):
        with patch.object(repair, 'repair_worker', side_effect=worker):
            result = repair.repair_project(self.project, self.revision, self.run_id)
        code, containers = validator.docker_call(['docker','ps','--all','--quiet','--filter','label=hermes.validation=phase1'])
        self.assertEqual((code, containers.strip()), (0, ''))
        self.assertFalse(list(validator.RUNTIME.iterdir()))
        self.assertFalse(repair.ACTIVE)
        return result

    def test_real_broken_js_repaired(self):
        def worker(project, revision, errors, *args):
            self.assertEqual(errors[0]['code'], 'syntax_error')
            ws.write_file(project, 'script.js', 'const repaired = 1;')
            return {'success': True, 'revision': ws.capture_revision(project)[0]}
        result = self.execute(worker)
        self.assertTrue(result['success'], result)
        self.assertEqual(len(result['validations']), 2)
        print('SUCCESS_TRACE=' + json.dumps({'status':result['status'],
            'validation_passes':[v['passed'] for v in result['validations']],
            'repairs':len(result['repairs']), 'checkpoints':len(result['checkpoints'])}))

    def test_real_multiple_errors_then_new_error(self):
        ws.write_file(self.project, 'style.css', 'body { color red; }')
        self.revision = ws.capture_revision(self.project)[0]
        counter = [0]
        def worker(project, revision, errors, *args):
            counter[0] += 1
            if counter[0] == 1:
                ws.write_file(project, 'script.js', 'const repaired = 1;')
            elif counter[0] == 2:
                ws.write_file(project, 'style.css', 'body { color: red; }')
                ws.write_file(project, 'script.js', 'function broken( {')
            else:
                ws.write_file(project, 'script.js', 'const repaired = 1;')
            return {'success':True, 'revision':ws.capture_revision(project)[0]}
        result = self.execute(worker)
        self.assertTrue(result['success'],result); self.assertEqual(len(result['validations']),4)

    def test_real_no_changes_no_progress(self):
        result = self.execute(lambda p,r,*args: {'success':True,'revision':r})
        self.assertEqual(result['status'],'no_progress')
        self.assertEqual(len(result['repairs']),2)
        print('NO_PROGRESS_TRACE=' + json.dumps({'status':result['status'],'reason':result['reason'],
            'validation_passes':[v['passed'] for v in result['validations']],
            'repairs':len(result['repairs'])}))

    def test_cancellation_during_real_validator_cleans_up(self):
        original = validator.docker_call
        def call(command, *args, **kwargs):
            if command[1] == 'start':
                self.assertTrue(repair.cancel(self.project,self.run_id))
            return original(command,*args,**kwargs)
        with patch.object(validator,'docker_call',side_effect=call), patch.object(repair,'repair_worker') as model:
            result = self.execute(model)
        self.assertEqual(result['status'],'cancelled'); model.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
