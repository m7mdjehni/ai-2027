"""Optional installed-Hermes compatibility smoke test; blocks all socket connections.

Run this with the Python executable from the Windows Hermes environment.
The real agent constructor is used; provider resolution and the model turn are mocked.
"""
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import project_workspace as ws
from hermes_project_worker import run


def fake_conversation(self, prompt):
    assert self.valid_tool_names == {'project_write_file', 'project_read_file', 'project_list_files'}
    assert self._skip_mcp_refresh is True
    assert not self._fallback_chain
    self._touch_activity('receiving stream response')
    self._fire_stream_delta('synthetic-sensitive-text')
    self._fire_reasoning_delta('synthetic-sensitive-reasoning')
    from run_agent import AIAgent
    with patch.object(AIAgent, '_compute_non_stream_stale_timeout', return_value=90):
        assert self._compute_non_stream_stale_timeout({}) == 90
    calls = [SimpleNamespace(id='write', function=SimpleNamespace(name='project_write_file',
              arguments=json.dumps({'path': 'index.html', 'content': '<h1>Offline compatibility test</h1>'}))),
             SimpleNamespace(id='deny', function=SimpleNamespace(name='terminal', arguments='{"command":"denied"}'))]
    messages = []
    self._execute_tool_calls(SimpleNamespace(tool_calls=calls), messages, 'offline')
    assert 'error' in json.loads(messages[1]['content'])
    return {'completed': True, 'failed': False}


if __name__ == '__main__':
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as temp:
        try:
            with patch.dict(os.environ, {'HERMES_HOME': str(Path(temp) / 'hermes')}), \
                 patch.object(ws, 'ROOT', Path(temp) / 'generated_projects'), \
                 patch('socket.socket.connect', side_effect=RuntimeError('Network disabled in test')):
                from run_agent import AIAgent
                project_id = 'c' * 32
                ws.workspace(project_id, create=True)
                with patch('hermes_cli.runtime_provider.resolve_runtime_provider', return_value={
                    'provider': 'openrouter', 'api_key': 'offline-test-only', 'base_url': 'http://127.0.0.1:1/v1'
                }), patch.object(AIAgent, 'run_conversation', fake_conversation):
                    result = run({'project_id': project_id, 'prompt': 'offline test', 'model': 'offline-model', 'provider': 'openrouter'})
                assert result['success'], result
                assert result['diagnostics']['stream_events'] >= 1
                assert result['diagnostics']['effective_watchdog_seconds'] == 90
                assert 'synthetic-sensitive' not in json.dumps(result)
                print('PASS: installed Hermes constructor, restricted dispatch, disabled fallback, real file detection; no provider call')
        finally:
            os.chdir(original_cwd)
            logging.shutdown()
