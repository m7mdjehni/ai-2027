import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

import requests

from app import project_workspace as ws
from app.ai import active_ai_provider, call_assistant, classify_ollama_error
from app.ollama_client import (
    DEFAULT_OLLAMA_MODELS, _has_repeated_tokens, chat, model_name, model_names,
)
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
            self.assertEqual(model_names(), DEFAULT_OLLAMA_MODELS)
            self.assertIn(model_name(), DEFAULT_OLLAMA_MODELS)
            with patch('app.ai.call_ollama_with_model',
                       return_value=('local reply', 'llama3.2:3b')):
                response = call_assistant([], 'Hello')
            self.assertEqual(response['model'], 'llama3.2:3b')
            self.assertEqual(response['models_used'], ['llama3.2:3b'])

    def test_configured_model_pool_replaces_the_defaults(self):
        with patch.dict(os.environ, {'OLLAMA_MODELS': 'llama3.2:3b, qwen2.5:1.5b'}, clear=True):
            self.assertEqual(model_names(), ('llama3.2:3b', 'qwen2.5:1.5b'))

    def test_models_are_chosen_randomly_for_generation_and_repair(self):
        responses = []
        for content in ('{"index.html":"<!doctype html><title>Generated</title>"}',
                        '{"index.html":"<!doctype html><title>Repaired</title>"}'):
            response = MagicMock(status_code=200)
            response.json.return_value = {'message': {'content': content}}
            responses.append(response)
        with patch.dict(os.environ, {'OLLAMA_HOST_BASE_URL': 'http://127.0.0.1:11434'}, clear=True), \
                patch('requests.Session') as session, \
                patch('app.ollama_client.random.choice',
                      side_effect=['llama3.2:3b', 'qwen2.5:1.5b']):
            session.return_value.__enter__.return_value.post.side_effect = responses
            generated_models = []
            repaired_models = []
            worker.generate_files('Create a page', ['index.html'], used_models=generated_models)
            worker.generate_files('Repair the page', ['index.html'], repair=True,
                                 used_models=repaired_models)
            payloads = [call.kwargs['json'] for call in
                        session.return_value.__enter__.return_value.post.call_args_list]
            self.assertEqual([payload['model'] for payload in payloads],
                             ['llama3.2:3b', 'qwen2.5:1.5b'])
            self.assertEqual(generated_models, ['llama3.2:3b'])
            self.assertEqual(repaired_models, ['qwen2.5:1.5b'])
            self.assertTrue(all(payload['options']['num_ctx'] == 2048 for payload in payloads))
            self.assertTrue(all(payload['options']['num_predict'] == 1024 for payload in payloads))

    def test_local_server_failure_retries_another_model(self):
        failed = MagicMock(status_code=500)
        succeeded = MagicMock(status_code=200)
        succeeded.json.return_value = {'message': {'content': 'local reply'}}
        with patch.dict(os.environ, {}, clear=True), \
                patch('requests.Session') as session, \
                patch('app.ollama_client.random.choice', return_value='qwen2.5:1.5b'):
            session.return_value.__enter__.return_value.post.side_effect = [failed, succeeded]
            from app.ollama_client import chat_with_model
            response, model = chat_with_model([{'role': 'user', 'content': 'hello'}])
            payloads = [call.kwargs['json'] for call in
                        session.return_value.__enter__.return_value.post.call_args_list]
        self.assertEqual(response, 'local reply')
        self.assertEqual(model, 'llama3.2:3b')
        self.assertEqual([payload['model'] for payload in payloads],
                         ['qwen2.5:1.5b', 'llama3.2:3b'])

    def test_repetitive_output_is_rejected_for_local_fallback(self):
        self.assertTrue(_has_repeated_tokens(
            'You You are are Qwen, by by Alibaba. You You are are the model.'))
        self.assertFalse(_has_repeated_tokens(
            'You are the model. Please build a small offline website.'))

    def test_invalid_generated_json_retries_other_local_model(self):
        invalid = MagicMock(status_code=200)
        invalid.json.return_value = {'message': {'content': 'not json'}}
        valid = MagicMock(status_code=200)
        valid.json.return_value = {
            'message': {'content': '{"index.html":"<!doctype html><title>Local</title>"}'}}
        with patch.dict(os.environ, {'OLLAMA_HOST_BASE_URL': 'http://127.0.0.1:11434'}, clear=True), \
                patch('requests.Session') as session, \
                patch('app.ollama_client.random.choice',
                      side_effect=['qwen2.5:1.5b', 'llama3.2:3b']):
            session.return_value.__enter__.return_value.post.side_effect = [invalid, valid]
            used_models = []
            files = worker.generate_files('Create a page', ['index.html'], used_models=used_models)
            payloads = [call.kwargs['json'] for call in
                        session.return_value.__enter__.return_value.post.call_args_list]
        self.assertEqual(files['index.html'], '<!doctype html><title>Local</title>')
        self.assertEqual(used_models, ['llama3.2:3b'])
        self.assertEqual([payload['model'] for payload in payloads],
                         ['qwen2.5:1.5b', 'llama3.2:3b'])

    def test_chat_uses_memory_safe_context(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'message': {'content': 'local reply'}}
        with patch.dict(os.environ, {'OLLAMA_HOST_BASE_URL': 'http://127.0.0.1:11434',
                                    'OLLAMA_CONTEXT_LENGTH': '2048'}), \
                patch('requests.Session') as session:
            session.return_value.__enter__.return_value.post.return_value = response
            self.assertEqual(chat([{'role': 'user', 'content': 'hello'}], host=True), 'local reply')
            payload = session.return_value.__enter__.return_value.post.call_args.kwargs['json']
            self.assertIn(payload['model'], DEFAULT_OLLAMA_MODELS)
            self.assertEqual(payload['options']['num_ctx'], 2048)
            self.assertEqual(payload['options']['num_predict'], 1024)

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
            def generate(*args, **kwargs):
                kwargs['used_models'].append('qwen2.5:1.5b')
                return output
            with patch.object(worker, 'generate_files', side_effect=generate), \
                    patch('hermes_validation.validate_project', return_value={'passed': True}):
                result = worker.execute({'project_id': pid, 'prompt': 'portfolio'})
                self.assertTrue(result['success'])
                self.assertEqual(result['model'], 'qwen2.5:1.5b')
                self.assertEqual(result['models_used'], ['qwen2.5:1.5b'])
            revision = ws.capture_revision(pid)[0]
            ws.write_file(pid, 'script.js', 'const n=2;')
            with patch.object(worker, 'generate_files') as model:
                result = worker.execute({'project_id': pid, 'repair': True, 'revision': revision, 'errors': []})
                self.assertEqual(result['error'], 'stale_revision')
                model.assert_not_called()

    def test_model_cannot_select_paths(self):
        with patch.object(worker, 'chat_with_model', return_value=('{"../escape.js":"bad"}', 'qwen2.5:1.5b')):
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
            with patch.object(worker, 'chat_with_model',
                              return_value=('{"replacement":"const broken = 42;"}',
                                            'llama3.2:3b')) as model:
                result = worker.execute({'project_id': pid, 'repair': True, 'revision': revision,
                    'errors': [{'file': 'script.js', 'line': 2, 'column': 16, 'check': 'javascript-syntax'}]})
            self.assertTrue(result['success'])
            self.assertEqual(ws.read_file(pid, 'script.js'), 'const keep = 7;\nconst broken = 42;\n')
            model.assert_called_once()
