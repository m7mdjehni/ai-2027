"""Offline real Hermes conversation: completed write -> later model turns.
Only transport, clock, and thread scheduling are faked; parser/loop/dispatch run.
"""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    original = Path.cwd()
    real_thread = threading.Thread

    class Clock:
        now = 10000.0
        def time(self): return self.now
        def monotonic(self): return self.now
        def sleep(self, seconds): self.now += seconds
        def __getattr__(self, name): return getattr(time, name)

    clock = Clock()
    summaries = []
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(Path(tmp)/'hermes')}))
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
        stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Network forbidden')))
        from app import project_workspace as ws
        import hermes_diagnostics as diagnostics
        import hermes_project_worker as worker
        import hermes_project_watchdog as watchdog
        from hermes_codex_observer import ALLOWED
        from run_agent import AIAgent
        from agent import chat_completion_helpers as helpers, codex_runtime, conversation_loop
        import httpx
        from openai import OpenAI
        stack.enter_context(patch.object(ws, 'ROOT', Path(tmp)/'projects'))
        stack.enter_context(patch.object(diagnostics, 'DIAGNOSTIC_ROOT', Path(tmp)/'diagnostics'))
        for module in (helpers, codex_runtime, conversation_loop, worker, watchdog):
            stack.enter_context(patch.object(module, 'time', clock))
        logging.disable(logging.CRITICAL)

        for number, case in enumerate(('success', 'inactive', 'endless', 'late', 'no_first_event')):
            streams, threads, clients, dispatched, terminations, finished_turns = [], [], [], [], [], []
            active = [None]
            agent_ref = [None]
            opened = threading.Event()

            class PollThread(real_thread):
                def __init__(self, **kwargs):
                    super().__init__(**kwargs)
                    self.finished = threading.Event(); self.polls = 0
                    threads.append(self)
                def run(self):
                    try: super().run()
                    finally: self.finished.set()
                def join(self, timeout=None):
                    assert timeout in (0.3, 2.0)
                    if timeout == 2.0:
                        assert self.finished.wait(5), 'Abort did not release worker'
                        return
                    if self.finished.is_set(): return
                    assert opened.wait(5), 'Stream not opened'
                    stream = active[0]
                    assert stream.ready.wait(5), 'Initial events not consumed'
                    self.polls += 1
                    assert self.polls <= 601, 'Unbounded watchdog'
                    clock.sleep(.5)
                    stream.ack.clear(); stream.tick.set()
                    assert stream.ack.wait(5), 'Tick not consumed'
                    if stream.terminal:
                        assert self.finished.wait(5), 'Completed worker did not exit'

            class SSE(httpx.SyncByteStream):
                def __init__(self):
                    self.ready = threading.Event(); self.ack = threading.Event()
                    self.tick = threading.Event(); self.stop = threading.Event()
                    self.started = clock.time(); self.terminal = False; self.late_attempts = 0
                    self.turn = agent_ref[0]._api_call_count
                    self.path = {1:'index.html', 2:'styles.css', 3:'script.js'}.get(self.turn)
                    streams.append(self); active[0] = self; opened.set()
                def __iter__(self):
                    def frame(kind, **data):
                        return ('data: '+json.dumps({'type':kind, **data})+'\n\n').encode()
                    ident = 'item_' + str(len(streams))
                    item = {'type':'function_call', 'id':ident, 'call_id':'call_'+ident,
                            'name':'project_write_file', 'arguments':'', 'status':'in_progress'}
                    def announce():
                        yield frame('response.output_item.added', output_index=0, item=item)
                        yield frame('response.function_call_arguments.delta', item_id=ident, output_index=0,
                                    delta='{"path":'+json.dumps(self.path)+',"content":"')
                    def completed(output):
                        return frame('response.completed', response={'id':'response_'+ident,
                            'status':'completed', 'object':'response', 'model':'gpt-5.6-sol', 'output':output})
                    delay = 100 if case == 'success' and self.turn == 2 else 0
                    silent = case == 'no_first_event' and self.turn == 2
                    announced = False
                    content = ''
                    try:
                        if self.path and not delay and not silent:
                            yield from announce(); announced = True
                        elif not silent:
                            yield frame('response.in_progress', response={'id':'response_'+ident,'status':'in_progress'})
                        self.ready.set()
                        while True:
                            assert self.tick.wait(5), 'Clock stopped'
                            self.tick.clear()
                            if self.stop.is_set():
                                if case == 'late':
                                    # Complete-looking output AFTER cancellation must never dispatch.
                                    self.late_attempts += 1
                                    late = dict(item, status='completed', arguments=json.dumps({
                                        'path':'styles.css','content':'late file must not exist'}))
                                    yield completed([late])
                                raise TimeoutError('Synthetic transport aborted')
                            elapsed = clock.time() - self.started
                            if self.turn == 4:
                                yield frame('response.output_text.delta', output_index=0, content_index=0,
                                            item_id=ident, delta='Done.')
                                self.terminal = True
                                yield completed([{'type':'message','id':ident,'role':'assistant',
                                    'status':'completed','content':[{'type':'output_text','text':'Done.', 'annotations':[]}]}])
                                return
                            if silent:
                                pass
                            elif case in ('inactive', 'late') and self.turn == 2:
                                pass
                            elif elapsed < delay:
                                yield frame('response.in_progress', response={'id':'response_'+ident,'status':'in_progress'})
                            else:
                                if not announced:
                                    yield from announce(); announced = True
                                yield frame('response.function_call_arguments.delta', item_id=ident,
                                            output_index=0, delta='x')
                                content += 'x'
                                finish_at = 1 if self.turn == 1 else 130 if self.turn == 2 else 120
                                if elapsed >= finish_at and (self.turn == 1 or case == 'success'):
                                    arguments = json.dumps({'path':self.path,'content':content})
                                    yield frame('response.function_call_arguments.delta', item_id=ident, output_index=0, delta='"}')
                                    yield frame('response.function_call_arguments.done', item_id=ident, output_index=0, arguments=arguments)
                                    item.update(arguments=arguments, status='completed')
                                    yield frame('response.output_item.done', output_index=0, item=item)
                                    self.terminal = True
                                    yield completed([item])
                                    return
                            self.ack.set()
                    finally:
                        self.ack.set()
                def close(self): pass

            def http(request):
                payload = json.loads(request.content)
                assert len(payload['tools']) == 3 and {t['name'] for t in payload['tools']} == ALLOWED
                if agent_ref[0]._api_call_count == 1:
                    assert payload['tool_choice'] == {'type':'function','name':'project_write_file'}
                    assert payload['parallel_tool_calls'] is False
                else:
                    assert payload['tool_choice'] == 'auto'
                    assert any(item.get('type') == 'function_call_output' for item in payload['input']), payload['input']
                return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=SSE())

            def create(self, **kwargs):
                assert self.valid_tool_names == ALLOWED and self._skip_mcp_refresh and not self._fallback_chain
                assert self._api_max_retries == 3
                agent_ref[0] = self
                opened.clear(); active[0] = None
                client = OpenAI(api_key='offline-placeholder',base_url='https://offline.invalid/v1',max_retries=0,
                                http_client=httpx.Client(transport=httpx.MockTransport(http)))
                clients.append(client)
                return client

            def abort(self, client, *, reason):
                assert reason == 'stale_call_kill'
                terminations.append(dict(self.diagnostics))
                active[0].stop.set(); active[0].tick.set()

            original_invoke = worker.ProjectToolBoundary._invoke_tool
            def invoke(self, name, args, *a, **kw):
                assert active[0].terminal, 'Partial tool dispatch'
                assert self.diagnostics['watchdog_termination_reason'] == 'completed', 'Late tool dispatch'
                finished_turns.append(dict(self.diagnostics))
                result = original_invoke(self, name, args, *a, **kw)
                assert 'error' not in json.loads(result)
                dispatched.append(args['path'])
                return result

            project = str(number+1)*32
            ws.workspace(project, create=True)
            with contextlib.ExitStack() as case_stack:
                case_stack.enter_context(patch.object(watchdog, 'threading', SimpleNamespace(Thread=PollThread, Lock=threading.Lock)))
                case_stack.enter_context(patch.object(AIAgent, '_create_request_openai_client', create))
                case_stack.enter_context(patch.object(AIAgent, '_abort_request_openai_client', abort))
                case_stack.enter_context(patch.object(AIAgent, '_close_request_openai_client', lambda *a, **k: None))
                case_stack.enter_context(patch.object(worker.ProjectToolBoundary, '_invoke_tool', invoke))
                case_stack.enter_context(patch('hermes_cli.runtime_provider.resolve_runtime_provider', return_value={
                    'provider':'openai-codex','api_mode':'codex_responses','api_key':'offline-placeholder','base_url':'https://offline.invalid/v1'}))
                try:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        result = worker.run({'project_id':project,'model':'gpt-5.6-sol','provider':'openai-codex',
                                             'prompt':'Create index.html, styles.css and script.js.', 'capture_diagnostics':True})
                    d = result['diagnostics']
                    assert all(t.finished.is_set() for t in threads)
                    assert d['watchdog_turn_phase'] == 'post_tool_continuation', result
                    assert d['prior_tool_call_dispatched'] and d['prior_successful_tool_call']
                    assert finished_turns[0]['model_turn_number'] == 1
                    assert not finished_turns[0]['prior_successful_tool_call']
                    assert d['total_build_elapsed_seconds'] > d['watchdog_elapsed_seconds']
                    if case == 'success':
                        assert result['success'], result
                        assert dispatched == ['index.html','styles.css','script.js']
                        assert ws.list_files(project) == sorted(dispatched)
                        assert [t['model_turn_number'] for t in finished_turns] == [1,2,3]
                        assert [t['watchdog_elapsed_seconds'] for t in finished_turns] == [1,130,120]
                        assert all(t['structured_argument_progress_observed'] for t in finished_turns)
                        assert d['model_turn_number'] == d['watchdog_request_number'] == 4
                        assert d['active_stream_watchdog_mode'] == 'post_tool_continuation'
                        assert not terminations
                    else:
                        expected = {'inactive':'stream_inactivity','late':'stream_inactivity',
                                    'endless':'hard_absolute_deadline','no_first_event':'time_to_first_event'}[case]
                        elapsed = {'inactive':60, 'late':60, 'endless':300, 'no_first_event':120}[case]
                        assert not result['success'] and result['error'] == 'timeout', result
                        assert len(terminations) == 3, terminations
                        assert all(t['watchdog_termination_reason'] == expected and t['watchdog_elapsed_seconds'] == elapsed
                                   and t['model_turn_number'] == 2 for t in terminations)
                        assert [t['watchdog_request_number'] for t in terminations] == [2,3,4]
                        assert dispatched == ['index.html'] and ws.list_files(project) == ['index.html']
                        assert d['tool_calls'] == 1 and d['codex_normalized_tool_calls'] == 1
                        if case == 'late': assert sum(s.late_attempts for s in streams) == 3
                    summaries.append({'case':case,'result':'PASS','model_turn':d['model_turn_number'],
                        'requests':d['watchdog_request_number'],'files':dispatched,
                        'termination':d['watchdog_termination_reason'],'partial_or_late_dispatch':False})
                finally:
                    for stream in streams: stream.stop.set(); stream.tick.set()
                    for thread in threads: assert thread.finished.wait(5)
                    for client in clients: client.close()
                    os.chdir(original)
    print(json.dumps({'result':'PASS','cases':summaries}))


if __name__ == '__main__': main()
