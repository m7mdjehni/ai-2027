"""Explicit offline Docker integration checks. Never resolves/calls AI providers.

Requires the locally built validator image. Uses temporary projects, not user projects.
Test-only probes run trusted fixed Node code under the production container policy.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import project_workspace as ws
import hermes_validation as supervisor


class DockerValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[1] / 'runtime'))
        self.addCleanup(self.temp.cleanup)
        root = patch.object(ws, 'ROOT', Path(self.temp.name) / 'projects')
        root.start(); self.addCleanup(root.stop)
        self.project = 'f' * 32
        ws.workspace(self.project, create=True)
        fixtures = Path(__file__).parent / 'fixtures' / 'static_valid'
        for file in fixtures.iterdir():
            ws.write_file(self.project, file.name, file.read_text())

    def validate(self):
        return supervisor.validate_project(self.project, ws.capture_revision(self.project)[0])

    def assert_cleanup(self, result):
        self.assertTrue(result['cleanup_confirmed'])
        code, remaining = supervisor.docker_call(['docker', 'ps', '--all', '--quiet', '--filter', 'label=hermes.validation=phase1'])
        self.assertEqual(code, 0)
        self.assertEqual(remaining.strip(), '')

    def test_valid_website(self):
        result = self.validate()
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['errors'], [])
        self.assert_cleanup(result)

    def test_broken_javascript_structured_errors(self):
        source = (Path(__file__).parent / 'fixtures/static_broken/script.js').read_text()
        ws.write_file(self.project, 'script.js', source)
        result = self.validate()
        self.assertFalse(result['passed'])
        error = next(error for error in result['errors'] if error['check'] == 'javascript-syntax')
        self.assertEqual(error['file'], 'script.js')
        self.assertEqual(error['line'], 2)
        self.assertEqual(error['column'], 16)
        self.assert_cleanup(result)
        print('BROKEN_JS_RESULT=' + json.dumps(result, sort_keys=True))

    def test_html_css_inline_js_and_assets(self):
        ws.write_file(self.project, 'index.html', '<!doctype html><html><head><title>T</title></head><body><img src="missing.svg"><script>const x = ;</script></body></html>')
        ws.write_file(self.project, 'style.css', 'body { color red; }')
        result = self.validate()
        checks = {error['check'] for error in result['errors']}
        self.assertTrue({'javascript-syntax', 'css-syntax', 'local-assets'} <= checks, result)
        self.assert_cleanup(result)

    def test_generated_code_and_config_never_execute(self):
        ws.write_file(self.project, 'package.json', json.dumps({'scripts': {'test': 'touch /project/executed.txt'}, 'type': 'module'}))
        ws.write_file(self.project, 'eslint.config.js', 'throw new Error("CONFIG_MUST_NOT_EXECUTE");')
        ws.write_file(self.project, 'script.js', 'fetch("https://example.invalid"); while (true) {}')
        result = self.validate()
        self.assertTrue(result['passed'], result)
        self.assertFalse((ws.workspace(self.project) / 'executed.txt').exists())
        self.assertNotIn('CONFIG_MUST_NOT_EXECUTE', json.dumps(result))
        self.assert_cleanup(result)

    def test_real_network_and_readonly_policy(self):
        # Only this test injects a trusted probe command; the HTTP contract cannot.
        original = supervisor.container_command
        probe = r'''
const fs = require('node:fs'), net = require('node:net'), os = require('node:os');
const assert = require('node:assert/strict');
assert.notEqual(process.getuid(), 0);
assert.deepEqual(Object.keys(os.networkInterfaces()), ['lo']);
for (const p of ['/project/__probe__.txt', '/opt/validator/__probe__.txt', '/tmp/__probe__.txt']) {
  assert.throws(() => fs.writeFileSync(p, 'denied'), e => ['EROFS','EACCES'].includes(e.code));
}
assert.equal(fs.existsSync('/var/run/docker.sock'), false);
assert.equal(fs.existsSync('/app/.env'), false);
assert.equal(process.env.PROJECT_BUILDER_TOKEN, undefined);
const socket = net.createConnection({host:'203.0.113.1',port:9});
socket.setTimeout(1500);
socket.on('connect', () => process.exit(2));
socket.on('timeout', () => {socket.destroy(); process.exit(3);});
socket.on('error', e => { assert.equal(e.code, 'ENETUNREACH'); console.log('POLICY_PROBE_PASS'); });
'''
        def command(name, snapshot, image):
            return original(name, snapshot, image)[:-1] + ['-e', probe]
        observed = []
        real_call = supervisor.docker_call
        def call(arguments, timeout=10):
            result = real_call(arguments, timeout)
            if arguments[1] == 'start':
                self.assertEqual(result[0], 0, result)
                self.assertIn('POLICY_PROBE_PASS', result[1]); observed.append(True)
            return result
        with patch.object(supervisor, 'container_command', side_effect=command), patch.object(supervisor, 'docker_call', side_effect=call):
            result = self.validate()
        self.assertTrue(observed)
        # Probe stdout deliberately is not a validation result: fail closed, cleanup still required.
        self.assertFalse(result['passed'])
        self.assert_cleanup(result)

    def test_real_timeout_kills_container_and_cleans_snapshot(self):
        original = supervisor.container_command
        real_call = supervisor.docker_call
        def command(name, snapshot, image):
            return original(name, snapshot, image)[:-1] + ['-e', 'setInterval(() => {}, 1000)']
        def call(arguments, timeout=10):
            return real_call(arguments, 1 if arguments[1] == 'start' else timeout)
        with patch.object(supervisor, 'container_command', side_effect=command), patch.object(supervisor, 'docker_call', side_effect=call):
            result = self.validate()
        self.assertEqual(result['status'], 'timeout', result)
        self.assert_cleanup(result)
        self.assertEqual(list(supervisor.RUNTIME.iterdir()), [])

    def test_real_output_limit_cleanup(self):
        original = supervisor.container_command
        def command(name, snapshot, image):
            return original(name, snapshot, image)[:-1] + ['-e', 'process.stdout.write("x".repeat(1000000))']
        with patch.object(supervisor, 'container_command', side_effect=command):
            result = self.validate()
        self.assertEqual(result['error_code'], 'output_limit', result)
        self.assert_cleanup(result)


class StandaloneValidationDockerTests(unittest.TestCase):
    from test_standalone_validation import StandaloneValidationTests as Fixture
    setUp = Fixture.setUp
    make_engine = Fixture.make_engine
    validate = Fixture.validate
    preview = Fixture.preview

    def test_real_host_validation_persists_and_allows_preview_only(self):
        result = supervisor.validate_project(self.project, self.revision)
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['cleanup_confirmed'])
        status, persisted = self.validate(result)
        self.assertEqual(status, 200)
        self.assertEqual(len(persisted['run_id']), 32)
        plan = self.preview()
        self.assertEqual(plan.status, 'preview')
        self.assertIsNone(plan.active_repository)


if __name__ == '__main__':
    unittest.main(verbosity=2)
