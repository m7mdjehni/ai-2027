"""Offline installed Hermes parser, Project watchdog and bounded retry regression.
Virtual clock ticks are acknowledged by the parser; no polling sleeps or timed joins.
Real Event waits only synchronize threads and fail within five seconds on a bug.
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
        def __getattr__(self, key): return getattr(time, key)

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
        from agent.codex_responses_adapter import _normalize_codex_response
        import httpx
        from openai import OpenAI
        stack.enter_context(patch.object(ws, 'ROOT', Path(tmp)/'projects'))
        stack.enter_context(patch.object(diagnostics, 'DIAGNOSTIC_ROOT', Path(tmp)/'diagnostics'))
        for module in (helpers, codex_runtime, conversation_loop, worker, watchdog):
            stack.enter_context(patch.object(module, 'time', clock))
        logging.disable(logging.CRITICAL)

        for number, case in enumerate(('active_complete', 'inactive', 'endless', 'no_first_event')):
            streams, threads, terminations, clients = [], [], [], []
            active = [None]
            opened = threading.Event()
            dispatched = []

            class PollThread(real_thread):
                def __init__(self, **kwargs):
                    super().__init__(**kwargs)
                    self.finished = threading.Event()
                    self.polls = 0
                    threads.append(self)
                def run(self):
                    try: super().run()
                    finally: self.finished.set()
                def join(self, timeout=None):
                    assert timeout in (0.3, 2.0), timeout
                    if timeout == 2.0:
                        assert self.finished.wait(5), 'Cancelled worker did not exit'
                        return
                    if self.finished.is_set(): return
                    assert opened.wait(5), 'Synthetic stream did not open'
                    stream = active[0]
                    assert stream.ready.wait(5), 'Initial events were not consumed'
                    self.polls += 1
                    assert self.polls <= 601, 'Unbounded watchdog'
                    clock.sleep(.5)
                    stream.ack.clear()
                    stream.tick.set()
                    assert stream.ack.wait(5), 'Parser did not acknowledge tick'
                    if stream.terminal:
                        assert self.finished.wait(5), 'Completed worker did not exit'

            class SSE(httpx.SyncByteStream):
                def __init__(self):
                    self.tick = threading.Event(); self.ack = threading.Event()
                    self.ready = threading.Event(); self.stop = threading.Event()
                    self.events = 0; self.terminal = False
                    self.started = clock.time()
                    streams.append(self); active[0] = self; opened.set()
                def __iter__(self):
                    def frame(kind, **data):
                        return ('data: '+json.dumps({'type':kind, **data})+'\n\n').encode()
                    item = {'type':'function_call','id':'offline_item','call_id':'offline_call',
                            'name':'project_write_file','arguments':'','status':'in_progress'}
                    try:
                        if case != 'no_first_event':
                            yield frame('response.output_item.added', output_index=0, item=item)
                            yield frame('response.function_call_arguments.delta', item_id='offline_item',
                                        output_index=0, delta='{"path":"index.html","content":"')
                        self.ready.set()
                        while True:
                            assert self.tick.wait(5), 'Fake clock stopped'
                            self.tick.clear()
                            if self.stop.is_set():
                                raise TimeoutError('Synthetic transport aborted')
                            if case in ('active_complete', 'endless'):
                                yield frame('response.function_call_arguments.delta', item_id='offline_item',
                                            output_index=0, delta='x')
                                self.events += 1
                            if case == 'active_complete' and clock.time()-self.started >= 120:
                                arguments = json.dumps({'path':'index.html','content':'x'*self.events})
                                yield frame('response.function_call_arguments.delta', item_id='offline_item',
                                            output_index=0, delta='"}')
                                yield frame('response.function_call_arguments.done', item_id='offline_item',
                                            output_index=0, arguments=arguments)
                                item.update(arguments=arguments, status='completed')
                                yield frame('response.output_item.done', output_index=0, item=item)
                                yield frame('response.completed', response={'id':'offline_response',
                                    'status':'completed','object':'response','model':'gpt-5.6-sol','output':[item]})
                                self.terminal = True
                                return
                            self.ack.set()  # Previous event was consumed by installed parser.
                    finally:
                        self.ack.set()
                def close(self): pass

            def http(request):
                payload = json.loads(request.content)
                assert len(payload['tools']) == 3
                assert {t['name'] for t in payload['tools']} == ALLOWED
                assert payload['tool_choice'] == {'type':'function','name':'project_write_file'}
                assert payload['parallel_tool_calls'] is False
                return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=SSE())

            def create(self, **kwargs):
                assert self.valid_tool_names == ALLOWED and self._skip_mcp_refresh
                assert self._api_max_retries == 3
                opened.clear(); active[0] = None
                client = OpenAI(api_key='offline-placeholder',base_url='https://offline.invalid/v1',
                    max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(http)))
                clients.append(client)
                return client

            def abort(self, client, *, reason):
                assert reason == 'stale_call_kill'
                terminations.append(dict(self.diagnostics))
                active[0].stop.set(); active[0].tick.set()

            def conversation(self, prompt):
                # Endless case below keeps the actual conversation retry loop.
                payload = self._build_api_kwargs([{'role':'user','content':'Offline fixture'}])
                payload = self._get_transport().preflight_kwargs(payload, allow_stream=False)
                response = self._interruptible_streaming_api_call(payload)
                message, _ = _normalize_codex_response(response)
                for call in message.tool_calls or []:
                    assert active[0].terminal, 'Partial tool dispatch'
                    dispatched.append(call)
                self._execute_tool_calls(message, [])
                return {'completed':True,'failed':False}

            project = str(number+1)*32
            ws.workspace(project, create=True)
            with contextlib.ExitStack() as case_stack:
                case_stack.enter_context(patch.object(watchdog, 'threading', SimpleNamespace(
                    Thread=PollThread, Lock=threading.Lock)))
                case_stack.enter_context(patch.object(AIAgent, '_create_request_openai_client', create))
                case_stack.enter_context(patch.object(AIAgent, '_abort_request_openai_client', abort))
                case_stack.enter_context(patch.object(AIAgent, '_close_request_openai_client', lambda *a, **k: None))
                case_stack.enter_context(patch('hermes_cli.runtime_provider.resolve_runtime_provider', return_value={
                    'provider':'openai-codex','api_mode':'codex_responses','api_key':'offline-placeholder',
                    'base_url':'https://offline.invalid/v1'}))
                if case != 'endless':
                    case_stack.enter_context(patch.object(AIAgent, 'run_conversation', conversation))
                if case == 'no_first_event':
                    # Isolate TTFB from the shorter initial generic deadline.
                    case_stack.enter_context(patch.object(AIAgent, '_compute_non_stream_stale_timeout', return_value=200))
                try:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        result = worker.run({'project_id':project,'model':'gpt-5.6-sol',
                            'provider':'openai-codex','prompt':'Create a small static website.',
                            'capture_diagnostics':True})
                    d = result['diagnostics']
                    expected = {'active_complete':'completed', 'inactive':'stream_inactivity',
                                'endless':'hard_absolute_deadline', 'no_first_event':'time_to_first_event'}[case]
                    assert d['watchdog_termination_reason'] == expected, result
                    assert d['hard_absolute_deadline_seconds'] == 300
                    assert d['initial_watchdog_seconds'] == (200 if case == 'no_first_event' else 90)
                    assert all(t.finished.is_set() for t in threads)
                    if case == 'active_complete':
                        assert result['success'] and d['watchdog_elapsed_seconds'] == 120, result
                        assert len(dispatched) == d['tool_calls'] == 1
                        assert ws.read_file(project, 'index.html') == 'x'*240
                    else:
                        assert not result['success'] and result['error'] == 'timeout', result
                        attempts = 3 if case == 'endless' else 1
                        assert len(streams) == len(terminations) == attempts
                        assert all(t['watchdog_termination_reason'] == expected for t in terminations)
                        assert all(t['watchdog_elapsed_seconds'] == {
                            'inactive':60, 'endless':300, 'no_first_event':120}[case] for t in terminations)
                        assert d['tool_calls'] == d['codex_normalized_tool_calls'] == 0
                        assert not d['write_file_invoked'] and not dispatched
                        assert not ws.list_files(project)
                        assert d['codex_function_argument_done_events'] == d['codex_function_output_items_done'] == 0
                    summaries.append({'case':case, 'reason':expected, 'attempts':len(streams),
                        'elapsed_per_attempt':d['watchdog_elapsed_seconds'], 'partial_dispatch':False,
                        'partial_files':False})
                finally:
                    for stream in streams: stream.stop.set(); stream.tick.set()
                    for thread in threads: assert thread.finished.wait(5)
                    for client in clients: client.close()
                    os.chdir(original)
    print(json.dumps({'result':'PASS', 'cases':summaries}))


if __name__ == '__main__': main()
