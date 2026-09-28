"""Offline Codex contract/security tests. All projects are temporary.

The repair integration mocks only model generation and uses real node --check;
Docker validation and live provider acceptance remain separate checks.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import codex_adapter as adapter
from app import project_workspace as ws
from hermes_projects import build_project, run_worker
from hermes_router import route_prompt


def response(files):
    return json.dumps({'files': [{'path': p, 'content': c} for p, c in files.items()]})


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = patch.object(ws, 'ROOT', Path(self.temp.name) / 'projects')
        root.start(); self.addCleanup(root.stop)
        engine = patch.dict(os.environ, {'PROJECT_AI_ENGINE': 'codex', 'CHAT_AI_ENGINE': 'codex'})
        engine.start(); self.addCleanup(engine.stop)
        self.pid = 'a' * 32
        self.target = ws.workspace(self.pid, create=True)

    def test_build_writes_actual_files_and_preserves_existing_project(self):
        with patch.object(adapter, 'generate', return_value=response({'index.html': '<h1>Coffee</h1>', 'script.js': 'const coffee = 1;'})):
            result = build_project('b' * 32, 'Create a modern responsive coffee shop website with Home, About, Menu, and Contact sections.')
        self.assertTrue(result['success'])
        self.assertEqual(result['provider'], 'openai-codex')
        self.assertEqual(result['files'], ['index.html', 'script.js'])
        self.assertEqual(ws.list_files(self.pid), [])
        with self.assertRaises(FileExistsError):
            build_project('b' * 32, 'again')

    def test_cli_is_read_only_no_shell_and_ignores_user_config(self):
        with patch('codex_adapter.resolve_codex', return_value='C:/trusted/codex.exe'):
            args = adapter.command(self.target, adapter.SCHEMA)
        self.assertIn('--skip-git-repo-check', args)
        self.assertIn('--ignore-user-config', args)
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertEqual(args[args.index('--cd') + 1], str(self.target.resolve()))
        self.assertIn('shell_tool', args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertEqual(args[-1], '-')

    def test_process_uses_explicit_environment_and_shell_false(self):
        import sys
        result = run_worker([sys.executable, '-I', '-c', 'import sys; print(sys.stdin.read())'],
                            input='hello', cwd=self.target, timeout=10, env=adapter.environment())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'hello')

    def test_environment_allowlist(self):
        with patch.dict(os.environ, {'PROJECT_BUILDER_TOKEN': 'dummy', 'GITHUB_TOKEN': 'dummy',
                                    'OPENAI_API_KEY': 'dummy', 'POSTGRES_PASSWORD': 'dummy'}):
            env = adapter.environment()
        self.assertFalse(any(k in env for k in ('PROJECT_BUILDER_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY', 'POSTGRES_PASSWORD')))

    def test_invalid_paths_rejected_before_any_writes(self):
        for name in ('../escape.js', '/absolute.js', 'C:/escape.js', '.env', 'secret.txt', 'token.json', 'nested/../escape.js'):
            with self.subTest(name=name), patch.object(adapter, 'generate', return_value=response({'index.html': 'valid', name: 'unsafe'})):
                self.assertFalse(adapter.project(self.pid, 'build')['success'])
                self.assertEqual(ws.list_files(self.pid), [])

    def test_invalid_shapes_and_empty_output(self):
        for answer in ('not JSON', '{}', '[]', '{"files": []}', '{"files": [{}]}', response({'script.js': 'ok'})):
            with self.subTest(answer=answer), patch.object(adapter, 'generate', return_value=answer):
                self.assertFalse(adapter.project(self.pid, 'build')['success'])
                self.assertEqual(ws.list_files(self.pid), [])

    def test_limits_and_conflicting_names(self):
        for files in ({'index.html': 'x' * (ws.MAX_FILE_BYTES + 1)},
                      {'index.html': 'ok', 'a.js': 'a', 'a.js/b.js': 'b'},
                      {'index.html': 'ok', 'A.js': 'a', 'a.js': 'b'}):
            with patch.object(adapter, 'generate', return_value=response(files)):
                self.assertFalse(adapter.project(self.pid, 'build')['success'])
                self.assertEqual(ws.list_files(self.pid), [])

    def test_stale_revision_prevents_provider_call(self):
        with patch.object(adapter, 'generate') as generate:
            self.assertEqual(adapter.project(self.pid, 'repair', revision='stale')['error'], 'stale_revision')
        generate.assert_not_called()

    def test_concurrent_edit_is_preserved(self):
        def generate(*args, **kwargs):
            ws.write_file(self.pid, 'index.html', 'user edit')
            return response({'index.html': 'model edit'})
        with patch.object(adapter, 'generate', side_effect=generate):
            self.assertEqual(adapter.project(self.pid, 'build')['error'], 'stale_revision')
        self.assertEqual(ws.read_file(self.pid, 'index.html'), 'user edit')

    def test_cancel_prevents_writes(self):
        event = threading.Event(); event.set()
        with patch.object(adapter, 'generate', return_value=response({'index.html': 'model edit'})):
            self.assertEqual(adapter.project(self.pid, 'build', cancel_event=event)['error'], 'cancelled')
        self.assertEqual(ws.list_files(self.pid), [])

    def test_timeout_returns_failure(self):
        with patch.object(adapter, 'generate', side_effect=subprocess.TimeoutExpired('codex', 1)):
            self.assertIn('timed out', adapter.project(self.pid, 'build')['error'])

    def test_raw_provider_errors_never_enter_build_results_or_logs(self):
        with patch.object(adapter, 'generate', side_effect=ValueError('unlabeled-sensitive-fixture')), self.assertLogs('hermes_projects') as logs:
            result = build_project('b' * 32, 'coffee')
        self.assertFalse(result['success'])
        self.assertNotIn('unlabeled-sensitive-fixture', json.dumps(result) + '\n'.join(logs.output))

    def test_cleanup_failure_does_not_allow_success(self):
        with patch.object(adapter, 'generate', side_effect=RuntimeError('cleanup unconfirmed')):
            with self.assertRaises(RuntimeError):
                adapter.project(self.pid, 'build')

    def test_jsonl_completed_answer_only(self):
        events = [{'type': 'thread.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Hello'}}, {'type': 'turn.completed'}]
        with patch.object(adapter, 'command', return_value=['codex.exe']), patch('hermes_projects.run_worker', return_value=SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, events)))):
            self.assertEqual(adapter.generate('hi', self.target, 10), 'Hello')

    def test_jsonl_failure_and_tool_activity_rejected(self):
        for event in ({'type': 'turn.failed'}, {'type': 'item.completed', 'item': {'type': 'command_execution'}}, {'type': 'turn.completed'}):
            with patch.object(adapter, 'command', return_value=['codex.exe']), patch('hermes_projects.run_worker', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(event))):
                with self.assertRaises(ValueError):
                    adapter.generate('hi', self.target, 10)

    def test_jsonl_warnings_and_recovered_retry_allow_completed_answer(self):
        events = [
            {'type': 'item.completed', 'item': {'type': 'error', 'message': 'startup warning'}},
            {'type': 'error', 'message': 'Reconnecting 1/5'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Hello'}},
            {'type': 'turn.completed'},
        ]
        with patch.object(adapter, 'command', return_value=['codex.exe']), patch(
                'hermes_projects.run_worker', return_value=SimpleNamespace(
                    returncode=0, stdout='\n'.join(map(json.dumps, events)))):
            self.assertEqual(adapter.generate('hi', self.target, 10), 'Hello')

    def test_jsonl_diagnostics_never_substitute_for_success(self):
        answer = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'partial'}}
        for events in (
            [answer, {'type': 'error', 'message': 'sensitive fixture'}],
            [answer, {'type': 'turn.failed'}],
            [{'type': 'item.completed', 'item': {'type': 'error', 'message': 'sensitive fixture'}},
             {'type': 'turn.completed'}],
            [answer, {'type': 'turn.completed'}, {'type': 'turn.failed'}],
        ):
            with self.subTest(events=events), patch.object(adapter, 'command', return_value=['codex.exe']), patch(
                    'hermes_projects.run_worker', return_value=SimpleNamespace(
                        returncode=0, stdout='\n'.join(map(json.dumps, events)))):
                with self.assertRaises(ValueError) as raised:
                    adapter.generate('hi', self.target, 10)
                self.assertNotIn('sensitive fixture', str(raised.exception))

    def test_chat_default_uses_codex_and_history(self):
        with patch.object(adapter, 'generate', return_value='Hello') as generate, patch('hermes_router.run_hermes') as legacy:
            result = route_prompt('Hello', [{'role': 'user', 'message': 'My name is Alex'}])
        self.assertTrue(result.success)
        self.assertEqual(result.response, 'Hello')
        self.assertIn('Alex', generate.call_args.args[0])
        legacy.assert_not_called()

    def test_backend_codex_provider_uses_existing_host_bridge(self):
        from app.ai import call_assistant
        with patch.dict(os.environ, {'AI_PROVIDER': 'codex'}), patch('app.ai.call_hermes_router', return_value={'response': 'Hello'}) as bridge:
            self.assertEqual(call_assistant([], 'Hello'), {'response': 'Hello'})
        bridge.assert_called_once_with([], 'Hello')

    def test_host_bridge_rejects_invalid_response_shape(self):
        from app.ai import call_hermes_router
        with patch('app.ai.requests.post', return_value=SimpleNamespace(json=lambda: [])):
            with self.assertRaisesRegex(RuntimeError, 'invalid response'):
                call_hermes_router([], 'Hello')

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the real syntax check')
    def test_build_break_validate_autonomous_repair_validate(self):
        import hermes_repair as repair
        ws.write_file(self.pid, 'index.html', '<!doctype html><html lang="en"><head><title>Coffee</title></head><body><script src="script.js"></script></body></html>')
        ws.write_file(self.pid, 'script.js', 'const coffee = 1;')
        def validate(pid, revision):
            result = subprocess.run(['node', '--check', str(ws.file_path(pid, 'script.js'))], capture_output=True, shell=False, timeout=10)
            errors = [] if result.returncode == 0 else [dict(file='script.js', line=1, column=1, check='javascript-syntax', code='syntax', message='JavaScript syntax is invalid.')]
            return dict(schema_version=1, profile='static-web-v1', revision=revision, current_revision=revision,
                        passed=not errors, status='failed' if errors else 'passed', errors=errors, warnings=[], cleanup_confirmed=True)
        self.assertTrue(validate(self.pid, ws.capture_revision(self.pid)[0])['passed'])
        ws.write_file(self.pid, 'script.js', 'const coffee = ;')
        revision = ws.capture_revision(self.pid)[0]
        self.assertFalse(validate(self.pid, revision)['passed'])
        with patch.object(repair, 'RUNTIME', Path(self.temp.name) / 'repairs'), patch.object(repair, 'validate_project', side_effect=validate), patch.object(adapter, 'generate', return_value=response({'script.js': 'const coffee = 1;'})) as ai:
            result = repair.repair_project(self.pid, revision, 'c' * 32)
        self.assertTrue(result['success'], result)
        self.assertTrue(result['validations'][-1]['passed'])
        ai.assert_called_once()


if __name__ == '__main__':
    unittest.main()
