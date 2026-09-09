import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import project_workspace as ws
from app.validation_contracts import ValidateProject, HostValidateProject
from hermes_diagnostics import safe_diagnostics, failure_category
import hermes_validation as supervisor
from validation.profiles import WEB, container_command
import test_projects
from test_projects import request
from hermes_service import app as host_app


class ValidationUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(ws, 'ROOT', Path(self.temp.name) / 'projects')
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.runtime = patch.object(supervisor, 'RUNTIME', Path(self.temp.name) / 'runtime' / 'validation')
        self.runtime.start(); self.addCleanup(self.runtime.stop)
        self.project = 'a' * 32
        ws.workspace(self.project, create=True)
        ws.write_file(self.project, 'index.html', '<!doctype html><title>Test</title>')
        self.revision, self.files = ws.capture_revision(self.project)
        self.commands = []

    def fake_docker(self, command, timeout=None):
        self.commands.append(command)
        if command[1:3] == ['image', 'inspect']:
            return 0, 'sha256:' + 'a' * 64
        if command[1] == 'start':
            return 0, json.dumps({'schema_version': 1, 'profile': WEB.name, 'revision': self.revision,
                'passed': True, 'status': 'passed', 'errors': [], 'warnings': []})
        return 0, ''

    def test_snapshot_revision_and_cleanup(self):
        with patch.object(supervisor, 'docker_call', side_effect=self.fake_docker):
            result = supervisor.validate_project(self.project, self.revision)
        self.assertTrue(result['passed'])
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(result['revision'], self.revision)
        self.assertFalse(list(supervisor.RUNTIME.iterdir()))
        self.assertEqual(self.commands[-1][1:4], ['rm', '--force', '--volumes'])

    def test_before_revision_change_does_not_start_docker(self):
        ws.write_file(self.project, 'index.html', '<title>Changed</title>')
        with patch.object(supervisor, 'docker_call') as docker:
            result = supervisor.validate_project(self.project, self.revision)
        self.assertEqual(result['status'], 'stale')
        docker.assert_not_called()

    def test_during_revision_change_cannot_claim_current_success(self):
        def docker(command, timeout=None):
            result = self.fake_docker(command, timeout)
            if command[1] == 'start':
                ws.write_file(self.project, 'script.js', 'const x = ;')
            return result
        with patch.object(supervisor, 'docker_call', side_effect=docker):
            result = supervisor.validate_project(self.project, self.revision)
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['passed'])
        self.assertTrue(result['snapshot_passed'])

    def test_fixed_container_policy(self):
        command = container_command('trusted-name', 'C:/trusted/snapshot', 'sha256:' + 'a' * 64)
        for fragment in ['none', '--read-only', '10001:10001', 'ALL', 'no-new-privileges=true', '256m', '32']:
            self.assertIn(fragment, command)
        self.assertEqual(command[-1], '/opt/validator/check_web.mjs')
        self.assertIn('type=bind,source=C:/trusted/snapshot,target=/project,readonly', command)
        self.assertNotIn('--privileged', command)
        self.assertNotIn('--env', command)
        self.assertNotIn('npm', command)

    def test_request_rejects_commands_images_paths_and_timeouts(self):
        for key in ['command', 'image', 'path', 'timeout', 'profile', 'args']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                ValidateProject(revision=self.revision, **{key: 'evil'})
        with self.assertRaises(ValueError):
            HostValidateProject(project_id='../outside', revision=self.revision)

    def test_timeout_outputlimit_badjson_all_cleanup(self):
        for reason in ['timeout', 'output_limit', 'badjson']:
            def docker(command, timeout=None):
                if command[1] == 'start':
                    self.commands.append(command)
                    if reason == 'badjson': return 0, '{broken'
                    raise supervisor.ValidationProcessError(reason)
                return self.fake_docker(command, timeout)
            with self.subTest(reason=reason), patch.object(supervisor, 'docker_call', side_effect=docker):
                result = supervisor.validate_project(self.project, self.revision)
                self.assertFalse(result['passed'])
                self.assertTrue(result['cleanup_confirmed'])
                self.assertEqual(self.commands[-1][1], 'rm')

    def test_cleanup_failure_never_claims_pass(self):
        def docker(command, timeout=None):
            if command[1] in ['rm', 'ps']: raise supervisor.ValidationProcessError('timeout')
            return self.fake_docker(command, timeout)
        with patch.object(supervisor, 'docker_call', side_effect=docker):
            result = supervisor.validate_project(self.project, self.revision)
        self.assertFalse(result['passed'])
        self.assertFalse(result['cleanup_confirmed'])
        self.assertEqual(result['error_code'], 'container_cleanup_unconfirmed')

    def test_missing_image_never_pulls(self):
        with patch.object(supervisor, 'docker_call', return_value=(1, 'missing')) as docker:
            result = supervisor.validate_project(self.project, self.revision)
        self.assertEqual(result['error_code'], 'validator_image_missing')
        self.assertEqual(docker.call_count, 1)

    def test_wrong_revision_and_inconsistent_pass_rejected(self):
        valid = {'schema_version': 1, 'profile': WEB.name, 'revision': self.revision,
                 'passed': True, 'status': 'passed', 'errors': []}
        for changes in [{'revision': 'b' * 64}, {'passed': False}, {'status': 'failed'}, {'errors': [{}]}]:
            with self.assertRaises(ValueError):
                supervisor._check_result({**valid, **changes}, self.revision, self.files)

    def test_diagnostics_do_not_include_freeform_secrets(self):
        secret = 'unlabeled-sensitive-value'
        data = safe_diagnostics({'api_mode': 'codex_responses', 'http_status': 400,
            'hermes_error': secret, 'hermes_final_response': secret, 'Authorization': secret,
            'model': secret, 'first_event_ms': 12, 'stream_events': 3, 'exception_type': secret})
        self.assertNotIn(secret, json.dumps(data))
        self.assertEqual(data['stream_events'], 3)
        self.assertEqual(failure_category('Non-streaming API call timed out after 90s ' + secret), 'timeout')
        self.assertEqual(safe_diagnostics({'model': {'Authorization': secret}}), {})

    def test_host_logs_do_not_expose_provider_response(self):
        from hermes_projects import build_project
        from types import SimpleNamespace
        secret = 'unlabeled-sensitive-value'
        output = json.dumps({'success': False, 'error': secret, 'diagnostics': {
            'hermes_final_response': secret, 'hermes_error': secret, 'http_status': 400,
            'exception_type': 'BadRequestError', 'api_mode': 'codex_responses'}})
        with patch('hermes_projects.hermes_python', return_value='trusted-python'), \
             patch('hermes_projects.run_worker', return_value=SimpleNamespace(returncode=0, stdout=output)), \
             self.assertLogs('hermes_projects', level='INFO') as logs:
            result = build_project('b' * 32, 'offline')
        self.assertNotIn(secret, '\n'.join(logs.output))
        self.assertNotIn(secret, json.dumps(result))


# Reuse the isolated database fixture, without inheriting duplicate regression tests.
class ValidationApiTests(unittest.TestCase):
    setUp = test_projects.ProjectTests.setUp
    fake_build = test_projects.ProjectTests.fake_build
    create = test_projects.ProjectTests.create

    def test_revision_and_validation_api_without_provider(self):
        project = self.create()['project_id']
        endpoint = '/api/projects/' + project
        status, revision = request('GET', endpoint + '/revision')
        self.assertEqual(status, 200)
        result = {'schema_version':1, 'project_id':project, 'revision': revision['revision'],
                  'current_revision':revision['revision'], 'profile':'static-web-v1',
                  'passed': True, 'status': 'passed', 'cleanup_confirmed':True, 'duration_ms':10, 'errors':[]}
        with patch('app.routers.projects.requests.post') as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = result
            status, saved = request('POST', endpoint + '/validate', {'revision': revision['revision']})
            self.assertEqual(status, 200)
            self.assertEqual({k:v for k,v in saved.items() if k!='run_id'}, result)
            self.assertEqual(len(saved['run_id']), 32)
            self.assertTrue(post.call_args.args[0].endswith('/projects/validate'))
        self.assertEqual(request('POST', endpoint + '/validate', {'revision': revision['revision'], 'command': 'npm test'})[0], 422)

    def test_host_validation_auth_and_rejected_arbitrary_options(self):
        body = {'project_id': 'a' * 32, 'revision': 'b' * 64}
        self.assertEqual(request('POST', '/projects/validate', body, host_app)[0], 401)
        with patch('hermes_validation.validate_project', return_value={'passed': True}) as validate:
            self.assertEqual(request('POST', '/projects/validate', body, host_app, [(b'x-project-token', b't' * 40)])[0], 200)
            validate.assert_called_once_with('a' * 32, 'b' * 64)
        self.assertEqual(request('POST', '/projects/validate', {**body, 'image': 'evil'}, host_app)[0], 422)
