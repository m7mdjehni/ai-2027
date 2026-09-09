"""Installed Hermes request construction + mocked Responses calls. No network."""
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
from hermes_project_worker import run, TOOLS


def conversation(self, prompt):
    from run_agent import AIAgent
    from agent.codex_responses_adapter import _preflight_codex_api_kwargs, _normalize_codex_response
    assert self.valid_tool_names == {t['function']['name'] for t in TOOLS}
    assert self.tools == TOOLS and self._skip_mcp_refresh and not self._fallback_chain
    assert 'workspace root is already selected' in self.ephemeral_system_prompt
    assert 'generated_projects/' + self.project_id not in self.ephemeral_system_prompt
    messages = [{'role':'system', 'content':self._build_system_prompt(None) + '\n' + self.ephemeral_system_prompt},
                {'role':'user', 'content':'Create a small static website.'}]
    normal = AIAgent._build_api_kwargs(self, messages)
    assert normal['tool_choice'] == 'auto'  # reproduce installed default
    payload = self._build_api_kwargs(messages)
    assert 'project_write_file' in payload['instructions']
    assert {t['name'] for t in payload['tools']} == self.valid_tool_names
    assert all(t['type']=='function' and isinstance(t['parameters'],dict) for t in payload['tools'])
    payload = _preflight_codex_api_kwargs(payload)
    assert payload['tool_choice'] == {'type':'function','name':'project_write_file'}
    assert payload['parallel_tool_calls'] is False
    try:
        self._build_api_kwargs(messages, tools_for_api=TOOLS[:1])
    except ValueError:
        pass
    else:
        raise AssertionError('Unexpected tool exposure must fail before delivery')
    # A denied write must not release the first-write requirement.
    assert 'error' in json.loads(self._invoke_tool('project_write_file',{'path':'../escape.js','content':'denied'}))
    assert self._build_api_kwargs(messages)['tool_choice'] == payload['tool_choice']
    assert 'error' in json.loads(self._invoke_tool('terminal', {'command':'denied'}))
    final = SimpleNamespace(status='completed', output=[SimpleNamespace(type='function_call',
        status='completed', name='project_write_file', call_id='offline', id='fc_offline',
        arguments=json.dumps({'path':'index.html','content':'<!doctype html><title>Offline</title>'}))])
    message, _ = _normalize_codex_response(final)
    results=[]; self._execute_tool_calls(message, results)
    assert ws.read_file(self.project_id,'index.html') == '<!doctype html><title>Offline</title>'
    # Installed Responses normalization must feed the same canonical tool boundary.
    for prefix in ['', 'generated_projects/' + self.project_id + '/',
                   ('generated_projects/' + self.project_id + '/') * 2]:
        final.output[0].arguments = json.dumps({'path':prefix+'index.html', 'content':'canonical'})
        message, _ = _normalize_codex_response(final)
        self._execute_tool_calls(message, [])
        assert ws.list_files(self.project_id) == ['index.html']
        assert ws.read_file(self.project_id, 'index.html') == 'canonical'
    assert self._build_api_kwargs(messages)['tool_choice'] == 'auto'
    self.repair_mode=True
    self._project_file_written=False
    assert self._build_api_kwargs(messages)['tool_choice'] == 'auto'
    self.repair_mode=False
    return {'completed':True,'failed':False}


def main():
    original=Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        try:
            with patch.dict(os.environ, {'HERMES_HOME':str(Path(directory)/'hermes')}), \
                 patch.object(ws,'ROOT',Path(directory)/'projects'), \
                 patch('socket.socket.connect',side_effect=AssertionError('Network forbidden')), \
                 patch('socket.create_connection',side_effect=AssertionError('Network forbidden')):
                from run_agent import AIAgent
                runtime={'provider':'openai-codex','api_mode':'codex_responses','api_key':'offline-test-only',
                         'base_url':'https://chatgpt.com/backend-api/codex'}
                with patch('hermes_cli.runtime_provider.resolve_runtime_provider',return_value=runtime):
                    for number,turn in enumerate([conversation,
                            lambda self,prompt:{'completed':True,'failed':False},
                            lambda self,prompt:{'completed':False,'failed':True,'error':'timeout'}]):
                        project=str(number+1)*32;ws.workspace(project,create=True)
                        with patch.object(AIAgent,'run_conversation',turn):
                            result=run({'project_id':project,'model':'gpt-5.6-sol','provider':'openai-codex','prompt':'offline'})
                        assert result['success'] is (number==0)
                        if number:assert not ws.list_files(project)
                        if number==2:assert result['error']=='timeout'
                print('PASS: real Hermes Codex payload, forced first write, auto after write, repair unchanged, schemas, dispatch, denied tools, no-tool and timeout failures; no provider calls')
        finally:
            os.chdir(original);logging.shutdown()


if __name__=='__main__':main()
