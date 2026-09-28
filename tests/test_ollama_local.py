import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

import requests

from app import project_workspace as ws
from app.ai import active_ai_provider, call_assistant, classify_ollama_error
from app.ollama_client import chat, model_name
import ollama_projects as worker


class LocalProviderTests(unittest.TestCase):
    def test_missing_model_preserves_http_error_without_online_fallback(self):
        response = MagicMock(status_code=404)
        with patch.dict(os.environ, {'AI_PROVIDER': 'ollama',
                                    'OLLAMA_BASE_URL': 'http://127.0.0.1:11434'}), \
                patch('requests.Session') as session, \
                patch('app.ai.call_hermes_router') as online:
            session.return_value.__enter__.return_value.post.return_value = response
            with self.assertRaises(requests.HTTPError) as caught:
                call_assistant([], 'Hello')
            self.assertEqual(classify_ollama_error(caught.exception), 'model_not_found')
            online.assert_not_called()

    def test_default_provider_and_model(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(active_ai_provider(), 'ollama')
            self.assertEqual(model_name(), 'llama3.2:3b')

    def test_remote_endpoint_rejected_before_network(self):
        with patch.dict(os.environ, {'OLLAMA_BASE_URL': 'https://example.com'}), patch('requests.Session') as session:
            with self.assertRaises(ValueError): chat([])
            session.assert_not_called()

    def test_cloud_model_rejected(self):
        with patch.dict(os.environ, {'OLLAMA_MODEL': 'example:cloud'}):
            with self.assertRaises(ValueError): model_name()

    def test_generation_and_stale_repair_boundary(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ws, 'ROOT', Path(temp) / 'projects'):
            pid = 'e' * 32
            ws.workspace(pid, create=True)
            output = {'index.html': '<!doctype html><title>Local</title>', 'styles.css': 'body {}', 'script.js': 'const n=1;'}
            with patch.object(worker, 'generate_files', return_value=output):
                self.assertTrue(worker.execute({'project_id': pid, 'prompt': 'portfolio'})['success'])
            revision = ws.capture_revision(pid)[0]
            ws.write_file(pid, 'script.js', 'const n=2;')
            with patch.object(worker, 'generate_files') as model:
                result = worker.execute({'project_id': pid, 'repair': True, 'revision': revision, 'errors': []})
                self.assertEqual(result['error'], 'stale_revision')
                model.assert_not_called()

    def test_model_cannot_select_paths(self):
        with patch.object(worker, 'chat', return_value='{"../escape.js":"bad"}'):
            with self.assertRaises(ValueError): worker.generate_files('test', ['script.js'])

    def test_concurrent_edit_prevents_model_write(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ws, 'ROOT', Path(temp) / 'projects'):
            pid = 'f' * 32
            ws.workspace(pid, create=True)
            ws.write_file(pid, 'script.js', 'const n = ;')
            revision = ws.capture_revision(pid)[0]
            def change(*args, **kwargs):
                ws.write_file(pid, 'script.js', 'const userEdit = 1;')
                return {'script.js': 'const n=1;'}
            with patch.object(worker, 'generate_files', side_effect=change):
                result = worker.execute({'project_id': pid, 'repair': True, 'revision': revision,
                                         'errors': [{'file': 'script.js'}]})
            self.assertEqual(result['error'], 'stale_revision')
            self.assertEqual(ws.read_file(pid, 'script.js'), 'const userEdit = 1;')

    def test_parser_selected_line_uses_model_replacement(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ws, 'ROOT', Path(temp) / 'projects'):
            pid = 'd' * 32
            ws.workspace(pid, create=True)
            ws.write_file(pid, 'script.js', 'const keep = 7;\nconst broken = ;\n')
            revision = ws.capture_revision(pid)[0]
            with patch.object(worker, 'chat', return_value='{"replacement":"const broken = 42;"}') as model:
                result = worker.execute({'project_id': pid, 'repair': True, 'revision': revision,
                    'errors': [{'file': 'script.js', 'line': 2, 'column': 16, 'check': 'javascript-syntax'}]})
            self.assertTrue(result['success'])
            self.assertEqual(ws.read_file(pid, 'script.js'), 'const keep = 7;\nconst broken = 42;\n')
            model.assert_called_once()
