"""Offline installed-Hermes Codex transport probes; never resolves credentials.

Run with Hermes's Python. All sockets are blocked, HTTP/Relay delivery is mocked,
and only temporary project files are written. No production settings are changed.
"""
import ast
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def probe():
    from app import project_workspace as ws
    from hermes_project_worker import TOOLS, ProjectToolBoundary
    from agent import codex_runtime as runtime
    from agent import chat_completion_helpers as helpers
    from agent.codex_responses_adapter import (
        _responses_tools, _preflight_codex_api_kwargs, _normalize_codex_response,
    )
    import hermes_cli.oneshot as oneshot

    # Check the installed CLI source, without running CLI startup/provider lookup.
    tree = ast.parse(Path(oneshot.__file__).read_text(encoding='utf-8'))
    constructors = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'AIAgent']
    assert len(constructors) == 1
    args = {kw.arg: ast.unparse(kw.value) for kw in constructors[0].keywords}
    assert args['api_mode'] == "runtime.get('api_mode')"
    assert args['quiet_mode'] == 'True'
    print('PASS: installed one-shot passes resolved api_mode to the same AIAgent')

    payload = _preflight_codex_api_kwargs({
        'model': 'gpt-5.6-sol', 'instructions': 'Call project_write_file to create index.html.',
        'input': [{'role': 'user', 'content': 'Offline fixture'}],
        'tools': _responses_tools(TOOLS), 'store': False,
    })
    assert [tool['name'] for tool in payload['tools']] == [tool['function']['name'] for tool in TOOLS]
    print('PASS: all three current custom tool schemas pass installed Codex conversion/preflight')

    final = SimpleNamespace(status='completed', output=[SimpleNamespace(
        type='function_call', status='completed', name='project_write_file',
        arguments=json.dumps({'path': 'index.html', 'content': '<h1>Offline</h1>'}),
        call_id='call_offline', id='fc_offline')])
    client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=iter([]))))
    agent = SimpleNamespace(api_mode='codex_responses', provider='openai-codex',
                            session_id='', _interrupt_requested=False,
                            stream_delta_callback=None, _stream_callback=None)
    agent._run_codex_stream = lambda kwargs, **options: runtime.run_codex_stream(agent, kwargs, **options)
    agent._interruptible_api_call = lambda kwargs: helpers._dispatch_nonstreaming_api_request(
        agent, kwargs, make_client=lambda reason: client)

    def fake_relay(request, opener, **options):
        return opener(request)

    with patch('agent.relay_llm.stream', side_effect=fake_relay), \
         patch.object(runtime, '_consume_codex_event_stream', return_value=final):
        # Both outer entry points must reach the real installed stream=True seam.
        for entry in [agent._interruptible_api_call,
                      lambda kwargs: helpers.interruptible_streaming_api_call(agent, kwargs)]:
            assert entry(payload) is final
            assert client.responses.create.call_args.kwargs['stream'] is True
            sent = client.responses.create.call_args.kwargs
            assert len(sent.get('tools', sent.get('extra_body', {}).get('tools', []))) == 3
    print('PASS: both outer wait paths invoke Responses.create(stream=True) with custom tools')

    message, _ = _normalize_codex_response(final)
    assert message.tool_calls[0].function.name == 'project_write_file'
    project_id = 'e' * 32
    ws.workspace(project_id, create=True)
    boundary = ProjectToolBoundary()
    boundary.project_id = project_id
    boundary.diagnostics = {'tool_calls': 0, 'write_file_invoked': False, 'tool_call_failure_reason': None}
    messages = []
    boundary._execute_tool_calls(message, messages)
    assert ws.read_file(project_id, 'index.html') == '<h1>Offline</h1>'
    assert boundary.diagnostics['write_file_invoked'] is True
    print('PASS: installed Codex function-call normalization reaches the restricted writer')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(os.environ, {'HERMES_HOME': str(Path(directory) / 'hermes')}), \
             patch('socket.socket.connect', side_effect=AssertionError('Network prohibited')), \
             patch('socket.create_connection', side_effect=AssertionError('Network prohibited')):
            from app import project_workspace as ws
            try:
                with patch.object(ws, 'ROOT', Path(directory) / 'generated_projects'):
                    probe()
            finally:
                logging.shutdown()
