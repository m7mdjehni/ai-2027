"""Offline HTTP/regression tests. No network, provider calls, or production DB writes."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from urllib.parse import urlsplit, quote

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_db
from app.models import Base
from app import project_workspace as ws
from hermes_service import app as host_app
from hermes_router import route_prompt, run_hermes, _command_timeout
from hermes_projects import build_project, run_worker, _project_worker_timeout
from hermes_project_worker import ProjectToolBoundary


def request(method, url, body=None, application=app, headers=None):
    """Exercise the real ASGI app without an extra HTTP client dependency."""
    async def execute():
        parts = urlsplit(url)
        data = json.dumps(body).encode() if body is not None else b""
        output = []
        async def receive():
            return {"type": "http.request", "body": data, "more_body": False}
        async def send(message):
            output.append(message)
        await application({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                           "method": method, "scheme": "http", "path": parts.path,
                           "raw_path": parts.path.encode(), "query_string": parts.query.encode(),
                           "root_path": "", "headers": [(b"content-type", b"application/json"), *(headers or [])],
                           "server": ("test", 80), "client": ("test", 123)}, receive, send)
        status = next(item["status"] for item in output if item["type"] == "http.response.start")
        payload = b"".join(item.get("body", b"") for item in output if item["type"] == "http.response.body")
        return status, json.loads(payload)
    return asyncio.run(execute())


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_patch = patch.object(ws, "ROOT", Path(self.temp.name) / "generated_projects")
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.env = patch.dict(os.environ, {"PROJECT_BUILDER_TOKEN": "t" * 40})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        factory = sessionmaker(bind=self.engine)
        def database():
            with factory() as session:
                yield session
        app.dependency_overrides[get_db] = database
        self.addCleanup(app.dependency_overrides.clear)
        self.addCleanup(self.engine.dispose)
        self.post = patch("app.routers.projects.requests.post", side_effect=self.fake_build)
        self.post.start()
        self.addCleanup(self.post.stop)

    def fake_build(self, url, json, headers, timeout):
        self.assertTrue(url.endswith('/projects/build'))
        self.assertEqual(headers['X-Project-Token'], 't' * 40)
        project_id = json['project_id']
        ws.workspace(project_id, create=True)
        ws.write_file(project_id, 'index.html', '<h1>Coffee</h1><script>alert(1)</script>')
        ws.write_file(project_id, 'css/style.css', 'body { color: brown; }')
        return Mock(json=lambda: {"success": True, "model": "gpt-5.6-sol", "provider": "openai-codex",
                                  "fallback_used": True, "files": ["imaginary.js"]}, raise_for_status=lambda: None)

    def create(self):
        status, data = request('POST', '/api/projects', {'prompt': 'Create a coffee shop website'})
        self.assertEqual(status, 201)
        return data

    def test_create_and_actual_files_with_fallback_metadata(self):
        data = self.create()
        self.assertTrue(data['success'])
        self.assertEqual(data['task_type'], 'coding')
        self.assertEqual(data['files'], ['css/style.css', 'index.html'])
        self.assertTrue(data['fallback_used'])
        self.assertEqual(data['model'], 'gpt-5.6-sol')
        self.assertTrue(data['preview_pending'])
        self.assertFalse(data['preview_available'])

    def test_history_detail_list_and_read(self):
        data = self.create()
        path = '/api/projects/' + data['project_id']
        self.assertEqual(request('GET', '/api/projects')[1][0]['project_id'], data['project_id'])
        self.assertEqual(request('GET', path)[1]['status'], 'completed')
        self.assertEqual(request('GET', path + '/files')[1]['files'], data['files'])
        content = request('GET', path + '/file?path=css/style.css')[1]['content']
        self.assertIn('brown', content)
        self.assertEqual(request('GET', path + '/file?path=missing.txt')[0], 404)

    def test_traversal_and_sensitive_paths(self):
        data = self.create()
        endpoint = '/api/projects/' + data['project_id'] + '/file?path='
        for unsafe in ['../.env', '../../app/main.py', '/etc/passwd', 'C:/Windows/win.ini',
                       'C:\\Windows\\win.ini', '\\\\server\\share\\x.txt', '.env', 'a/../index.html',
                       'a//x.txt', 'index.html:secret', 'CON.txt', 'dir/NUL.txt', 'auth.json',
                       'oauth_credentials.json', '%2e%2e/.env', 'x.txt.', 'x.txt ', 'a\x00.txt']:
            with self.subTest(path=unsafe):
                self.assertEqual(request('GET', endpoint + quote(unsafe, safe=''))[0], 400)

    def test_request_validation(self):
        for body in [{'prompt': '  '}, {'prompt': 'x', 'output_directory': 'C:/'}, {'prompt': 'x' * 12001}]:
            self.assertEqual(request('POST', '/api/projects', body)[0], 422)
        self.assertEqual(request('GET', '/api/projects/bad-id')[0], 400)
        self.assertEqual(request('GET', '/api/projects/' + 'f' * 32)[0], 404)

    def test_missing_token_fails_closed(self):
        with patch.dict(os.environ, {'PROJECT_BUILDER_TOKEN': ''}):
            self.assertEqual(request('POST', '/api/projects', {'prompt': 'website'})[0], 503)

    def test_host_authentication(self):
        body = {'project_id': 'a' * 32, 'prompt': 'website'}
        self.assertEqual(request('POST', '/projects/build', body, host_app)[0], 401)
        with patch('hermes_projects.build_project', return_value={'success': True}) as build:
            self.assertEqual(request('POST', '/projects/build', body, host_app,
                                     [(b'x-project-token', b't' * 40)])[0], 200)
            build.assert_called_once()

    def test_empty_or_failed_generation_is_not_completed(self):
        with patch('app.routers.projects.requests.post', return_value=Mock(
                json=lambda: {'success': True}, raise_for_status=lambda: None)):
            data = self.create()
        self.assertFalse(data['success'])
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['files'], [])

    def test_host_unavailable_and_timeout_saved(self):
        for error, expected in [(requests.ConnectionError(), 'failed'), (requests.Timeout(), 'unknown')]:
            with patch('app.routers.projects.requests.post', side_effect=error):
                self.assertEqual(self.create()['status'], expected)
        self.assertEqual(len(request('GET', '/api/projects')[1]), 2)

    def test_limits_binary_and_hardlinks(self):
        project_id = self.create()['project_id']
        with self.assertRaises(ValueError):
            ws.write_file(project_id, 'large.txt', 'x' * (ws.MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            ws.write_file(project_id, 'binary.txt', '\x00')
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('secret')
        os.link(outside, ws.workspace(project_id) / 'linked.txt')
        with self.assertRaises(ValueError):
            ws.read_file(project_id, 'linked.txt')

    def test_symlinks_rejected_when_supported(self):
        project_id = self.create()['project_id']
        target = ws.workspace(project_id) / 'link.txt'
        try:
            target.symlink_to(Path(self.temp.name) / 'outside.txt')
        except OSError:
            self.skipTest('Windows account cannot create symlinks; hard-link and reparse validation tested separately')
        with self.assertRaises(ValueError):
            ws.read_file(project_id, 'link.txt')

    def test_windows_reparse_detection(self):
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat_mode(), st_file_attributes=0x400)):
            with self.assertRaises(ValueError):
                ws.reject_link(Path('junction'))

    def test_agent_tool_dispatch_writes_real_files_and_denies_shell(self):
        project_id = self.create()['project_id']
        agent = ProjectToolBoundary()
        agent.project_id = project_id
        messages = []
        calls = [SimpleNamespace(id=str(i), function=SimpleNamespace(name=name, arguments=json.dumps(args)))
                 for i, (name, args) in enumerate([
                     ('project_write_file', {'path': 'script.js', 'content': '// created by a tool call'}),
                     ('terminal', {'command': 'unsafe'}),
                     ('project_write_file', {'path': '../outside.txt', 'content': 'unsafe'})])]
        agent._execute_tool_calls(SimpleNamespace(tool_calls=calls), messages, 'test')
        self.assertIn('script.js', ws.list_files(project_id))
        self.assertIn('error', json.loads(messages[1]['content']))
        self.assertIn('error', json.loads(messages[2]['content']))
        self.assertFalse((ws.ROOT / 'outside.txt').exists())

    def test_agent_tool_dispatch_records_safe_diagnostics(self):
        project_id = self.create()['project_id']
        agent = ProjectToolBoundary()
        agent.project_id = project_id
        agent.diagnostics = {'tool_calls': 0, 'write_file_invoked': False,
                             'tool_call_failure_reason': None}
        messages = []
        call = SimpleNamespace(id='write', function=SimpleNamespace(
            name='project_write_file', arguments=json.dumps({'path': '../outside.txt', 'content': 'unsafe'})))
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[call]), messages, 'test')
        self.assertTrue(agent.diagnostics['write_file_invoked'])
        self.assertEqual(agent.diagnostics['tool_calls'], 1)
        self.assertIn('Invalid path', agent.diagnostics['tool_call_failure_reason'])

    def test_router_primary_429_timeout_and_fallback_failure(self):
        for error in ['HTTP 429', 'timeout', 'model unavailable']:
            runner = Mock(side_effect=[(False, '', error), (True, 'created', None)])
            result = route_prompt('website', runner=runner, task_type_override='coding')
            self.assertTrue(result.success)
            self.assertTrue(result.fallback_used)
            self.assertEqual(result.provider, 'openai-codex')
        result = route_prompt('website', runner=Mock(return_value=(False, '', 'timeout')))
        self.assertFalse(result.success)
        self.assertTrue(result.fallback_used)
        result = route_prompt('website', runner=Mock(return_value=(True, 'created', None)))
        self.assertFalse(result.fallback_used)

    def test_host_uses_restricted_worker_and_existing_router(self):
        project_id = 'b' * 32
        def process(command, **kwargs):
            self.assertFalse(kwargs['shell'])
            self.assertIn('hermes_project_worker.py', command[-1])
            self.assertEqual(Path(kwargs['cwd']), ws.workspace(project_id))
            self.assertEqual(kwargs['timeout'], 900)
            self.assertTrue(json.loads(kwargs['input'])['capture_diagnostics'])
            ws.write_file(project_id, 'index.html', '<h1>Actual tool output</h1>')
            return SimpleNamespace(returncode=0, stdout='{"success": true}')
        with patch('hermes_projects.hermes_python', return_value='trusted-python'), patch('hermes_projects.run_worker', side_effect=process):
            result = build_project(project_id, 'build a website')
        self.assertTrue(result['success'])
        self.assertEqual(result['model'], 'gpt-5.6-sol')
        self.assertEqual(result['files'], ['index.html'])
        with self.assertRaises(FileExistsError):
            ws.workspace(project_id, create=True)

    def test_project_worker_timeout_is_dedicated_and_normal_chat_timeout_is_unchanged(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_project_worker_timeout(), 900)
            self.assertEqual(_command_timeout(), 300)
        with patch.dict(os.environ, {'HERMES_PROJECT_WORKER_TIMEOUT': '1200', 'HERMES_COMMAND_TIMEOUT': '45'}):
            self.assertEqual(_project_worker_timeout(), 1200)
            self.assertEqual(_command_timeout(), 45)

    @unittest.skipUnless(os.name == 'nt', 'Windows-specific worker tree cleanup')
    def test_worker_timeout_terminates_process_tree_before_fallback(self):
        process = Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', 1), ('', '')]
        context = Mock()
        context.__enter__ = Mock(return_value=process)
        context.__exit__ = Mock(return_value=False)
        with patch('hermes_projects.subprocess.Popen', return_value=context), \
             patch('hermes_projects.subprocess.run', return_value=SimpleNamespace(returncode=0)) as terminate:
            with self.assertRaises(subprocess.TimeoutExpired):
                run_worker(['trusted-python'], input='{}', cwd=self.temp.name, timeout=1)
            self.assertEqual(terminate.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])
            self.assertFalse(terminate.call_args.kwargs['shell'])

    def test_existing_converse_tasks_notes(self):
        metadata = {'response': 'Hello', 'task_type': 'reasoning', 'model': 'gpt-5.6-sol',
                    'provider': 'openai-codex', 'fallback_used': False}
        with patch('app.routers.conversation.call_assistant', return_value=metadata):
            status, reply = request('POST', '/api/converse', {'message': 'Hello'})
        self.assertEqual(status, 200)
        self.assertEqual(reply['reply'], 'Hello')
        self.assertEqual(reply['model'], metadata['model'])
        self.assertEqual(len(request('GET', '/api/conversations')[1]), 2)
        task = request('POST', '/api/tasks', {'title': 'Demo'})[1]['task']
        self.assertEqual(len(request('GET', '/api/tasks')[1]), 1)
        self.assertTrue(request('POST', f"/api/tasks/{task['id']}/done", {'done': True})[1]['done'])
        self.assertEqual(request('DELETE', f"/api/tasks/{task['id']}")[0], 200)
        note = request('POST', '/api/notes', {'title': 'Demo', 'content': 'Notes'})[1]['note']
        self.assertEqual(len(request('GET', '/api/notes')[1]), 1)
        self.assertEqual(request('DELETE', f"/api/notes/{note['id']}")[0], 200)

    def test_ollama_adapter_preserved(self):
        from app.ai import call_assistant
        with patch.dict(os.environ, {'AI_PROVIDER': 'ollama'}), patch('app.ai.call_ollama', return_value='Local reply'):
            self.assertEqual(call_assistant([], 'Hello')['provider'], 'ollama')

    def test_cli_errors_and_no_shell(self):
        with patch('hermes_router.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='HTTP 429')) as run:
            self.assertEqual(run_hermes('test', 'model', 'provider')[2], 'HTTP 429')
            self.assertFalse(run.call_args.kwargs['shell'])
        with patch('hermes_router.subprocess.run', side_effect=subprocess.TimeoutExpired('hermes', 1)):
            self.assertFalse(run_hermes('test', 'model', 'provider')[0])


def stat_mode():
    import stat
    return stat.S_IFDIR


if __name__ == '__main__':
    unittest.main()
