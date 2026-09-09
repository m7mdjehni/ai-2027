"""Actual installed Hermes + OpenAI SDK + mocked HTTP SSE; no provider I/O."""
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import project_workspace as ws
import hermes_diagnostics as diagnostics
from hermes_project_worker import run
from hermes_codex_observer import ALLOWED


def main():
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as temporary:
        try:
            with patch.dict(os.environ, {'HERMES_HOME':str(Path(temporary)/'hermes')}), \
                 patch.object(ws, 'ROOT', Path(temporary)/'projects'), \
                 patch.object(diagnostics, 'DIAGNOSTIC_ROOT', Path(temporary)/'diagnostics'), \
                 patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), \
                 patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
                import httpx
                from openai import OpenAI
                from run_agent import AIAgent
                from agent.codex_responses_adapter import _normalize_codex_response
                content = '<!doctype html><title>Offline</title>' + (' harmless' * 700)
                arguments = json.dumps({'path':'index.html', 'content':content})
                item = {'type':'function_call', 'id':'fc_fixture', 'call_id':'call_fixture',
                        'name':'project_write_file', 'arguments':arguments, 'status':'completed'}

                for number, case in enumerate(['success', 'timeout', 'terminal_only']):
                    project = str(number+5)*32; ws.workspace(project, create=True)
                    requests_seen = []
                    class SSE(httpx.SyncByteStream):
                        def __iter__(self):
                            def frame(kind, **data):
                                return ('event: '+kind+'\ndata: '+json.dumps({'type':kind, **data})+'\n\n').encode()
                            if case != 'terminal_only':
                                yield frame('response.output_item.added', output_index=0,
                                    item={**item, 'arguments':'', 'status':'in_progress'})
                                for delta in arguments:
                                    yield frame('response.function_call_arguments.delta',item_id='fc_fixture',output_index=0,delta=delta)
                                if case == 'timeout':
                                    # Inject the existing timeout failure type after many valid frames.
                                    # No real clock/timeout settings are changed and no partial call is executed.
                                    raise TimeoutError('Synthetic stream timeout')
                                yield frame('response.function_call_arguments.done',item_id='fc_fixture',output_index=0,arguments=arguments)
                                yield frame('response.output_item.done',output_index=0,item=item)
                            yield frame('response.completed',response={'id':'resp_fixture','status':'completed',
                                'object':'response','model':'gpt-5.6-sol','output':[item]})
                    def http(request):
                        sent = json.loads(request.content)
                        assert request.method == 'POST' and request.url.path.endswith('/responses')
                        assert {t['name'] for t in sent['tools']} == ALLOWED and len(sent['tools']) == 3
                        assert sent['tool_choice'] == {'type':'function','name':'project_write_file'}
                        assert sent['parallel_tool_calls'] is False and sent['stream'] is True
                        assert 'extra_body' not in sent
                        requests_seen.append(True)
                        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=SSE())
                    client = OpenAI(api_key='offline-placeholder',base_url='https://offline.invalid/v1',
                        max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(http)))
                    def conversation(self, prompt):
                        payload = self._build_api_kwargs([{'role':'system','content':self.ephemeral_system_prompt},
                            {'role':'user','content':'Offline fixture'}])
                        payload = self._get_transport().preflight_kwargs(payload, allow_stream=False)
                        response = self._run_codex_stream(payload, client=client)
                        normalized = self._get_transport().normalize_response(response)
                        # Reinspection must not double-count normalization.
                        self._get_transport().normalize_response(response)
                        if normalized.tool_calls:
                            message, _ = _normalize_codex_response(response)
                            self._execute_tool_calls(message, [])
                        return {'completed':True,'failed':False}
                    started = time.monotonic()
                    try:
                        with patch('hermes_cli.runtime_provider.resolve_runtime_provider',return_value={
                                'provider':'openai-codex','api_mode':'codex_responses','api_key':'offline-placeholder',
                                'base_url':'https://offline.invalid/v1'}), \
                             patch.object(AIAgent,'run_conversation',conversation):
                            result=run({'project_id':project,'model':'gpt-5.6-sol','provider':'openai-codex',
                                'prompt':'offline private prompt','capture_diagnostics':True})
                    finally: client.close()
                    assert time.monotonic()-started < 15
                    assert len(requests_seen)==1
                    d=result['diagnostics']
                    assert d['final_request_tool_count']==3
                    assert d['final_request_forced_tool_name']=='project_write_file'
                    assert d['final_request_parallel_tool_calls'] is False
                    assert (diagnostics.DIAGNOSTIC_ROOT/(project+'.json')).is_file()
                    safe = json.dumps(d) + (diagnostics.DIAGNOSTIC_ROOT/(project+'.json')).read_text()
                    for sensitive in [content, arguments, 'offline-placeholder', 'offline private prompt', 'fc_fixture']:
                        assert sensitive not in safe
                    if case == 'success':
                        assert result['success'] and ws.read_file(project,'index.html')==content
                        assert d['tool_calls']==d['codex_normalized_tool_calls']==d['codex_function_calls_announced']==1
                        assert d['codex_function_argument_delta_events']==len(arguments)>5000
                        assert d['codex_function_argument_bytes']==len(arguments.encode())
                        assert d['codex_function_argument_done_events']==d['codex_function_output_items_done']==1
                        assert d['codex_response_completed_seen'] and d['codex_incomplete_function_calls']==0
                    elif case == 'timeout':
                        assert not result['success'] and result['error']=='timeout'
                        assert not ws.list_files(project) and d['tool_calls']==d['codex_normalized_tool_calls']==0
                        assert d['codex_function_calls_announced']==d['codex_incomplete_function_calls']==1
                        assert d['codex_function_argument_delta_events']>5000
                        assert not d['codex_response_completed_seen'] and d['codex_function_output_items_done']==0
                    else:
                        assert not result['success'] and not ws.list_files(project)
                        assert d['codex_terminal_output_function_calls_detected']==1
                        assert d['codex_response_completed_seen'] and d['codex_function_calls_announced']==0
                        assert d['codex_normalized_tool_calls']==d['tool_calls']==0
                    print('PASS:',case,'final SDK JSON verified; aggregate-only diagnostics; one mocked HTTP request')
        finally:
            os.chdir(original);logging.shutdown()


if __name__=='__main__':main()
