"""Offline watchdog-unit regression; fake worker, actual installed timeout loop.

Run with Hermes Python and its source directory on PYTHONPATH. This tests the
watchdog boundary, not the parser or the production conversation retry policy.
"""
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import chat_completion_helpers as helpers
from run_agent import AIAgent

WATCHDOG_SECONDS = 90.0
ATTEMPTS = 5


class FakeClock:
    def __init__(self):
        self.value = 10000.0

    def time(self):
        return self.value

    monotonic = time

    def sleep(self, seconds):
        assert seconds >= 0
        self.value += seconds


class SyntheticCodexAgent:
    api_mode = "codex_responses"
    provider = "openai-codex"
    model = "gpt-5.6-sol"
    base_url = "https://chatgpt.com/backend-api/codex"
    _interrupt_requested = False

    def __init__(self, clock):
        self.clock = clock
        self.announcements = 0
        self.events = []
        self._codex_stream_last_event_ts = None
        self._codex_stream_last_progress_ts = None

    def _compute_non_stream_stale_timeout(self, api_kwargs):
        return WATCHDOG_SECONDS  # Existing fixture value; no Hermes settings changed.

    def _interruptible_api_call(self, api_kwargs):
        return AIAgent._interruptible_api_call(self, api_kwargs)

    def _touch_activity(self, reason): pass
    def _buffer_status(self, message): pass
    def _emit_wait_notice(self, message): pass
    def _codex_silent_hang_hint(self, model=None): return None
    def _is_codex_backend(self): return True
    def _is_copilot_url(self): return False

    def argument_events(self):
        self.announcements += 1
        fragment = '{"path":"index.html","content":"'
        while True:
            event = {"type": "response.function_call_arguments.delta",
                     "item_id": "offline_call", "output_index": 0,
                     "delta": fragment}
            fragment = "x"  # Never complete the JSON or announce arguments.done.
            now = self.clock.time()
            self._codex_stream_last_event_ts = now
            self._codex_stream_last_progress_ts = now
            self.events.append((now, event))
            yield event


def main():
    clock = FakeClock()
    agent = SyntheticCodexAgent(clock)
    workers, kills, effective, failures = [], [], [], []
    dispatch = Mock(name="project_write_file")
    source, first_line = inspect.getsourcelines(helpers.interruptible_api_call)
    branch_line = first_line + next(i for i, line in enumerate(source)
                                   if line.strip() == "if _elapsed > _stale_timeout:")
    boundary_checks = []

    def trace(frame, event, arg):
        if frame.f_code is helpers.interruptible_api_call.__code__:
            if event == "line" and frame.f_lineno == branch_line:
                values = frame.f_locals
                boundary_checks.append((values['_elapsed'], values['_stale_timeout']))
        elif (event == "call" and frame.f_code.co_name == "_close_request_client_once"
              and frame.f_back.f_code is helpers.interruptible_api_call.__code__):
            kills.append(frame.f_locals['reason'])
            values = frame.f_back.f_locals
            effective.append(values['_stale_timeout'])
            assert values['_elapsed'] > values['_stale_timeout']
            assert clock.time() - agent._codex_stream_last_event_ts == 0
            assert values['_codex_idle_enabled'] and values['_codex_idle_timeout'] > 0
        return trace

    class CooperativeWorker:
        """Model a pending request without executing its blocking target.

        Each join delivers one synthetic stream event. The real Hermes caller
        owns all deadline decisions and creates the TimeoutError itself.
        """
        def __init__(self, target, daemon):
            self.alive = False
            self.polls = 0
            self.stream = agent.argument_events()
            workers.append(self)

        def start(self):
            self.alive = True
            next(self.stream)

        def is_alive(self): return self.alive

        def join(self, timeout=None):
            assert timeout in (0.3, 2.0), timeout
            if timeout == 0.3:
                self.polls += 1
                assert self.polls <= 400, 'Watchdog failed within bounded virtual ticks'
                clock.sleep(0.5)  # Exact binary ticks also exercise equality at 90s.
                next(self.stream)
            else:
                assert kills[-1] == 'stale_call_kill', kills
                self.alive = False
                self.stream.close()
                # Pending worker supplies neither a response nor a transport error.

    api_kwargs = {'model': agent.model, 'stream': True, 'input': []}
    previous_trace = sys.gettrace()
    original_directory = Path.cwd()
    with tempfile.TemporaryDirectory() as directory, \
         patch.object(helpers, 'time', clock), \
         patch.object(helpers, 'threading', SimpleNamespace(
             Thread=CooperativeWorker, Lock=threading.Lock, get_ident=threading.get_ident)), \
         patch.object(helpers.logger, 'warning'), \
         patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), \
         patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
        try:
            os.chdir(directory)
            sys.settrace(trace)
            retry_count = 0
            while True:
                try:
                    response = AIAgent._interruptible_streaming_api_call(agent, dict(api_kwargs))
                except TimeoutError as error:
                    failures.append(str(error))
                else:
                    # Observe the response-to-dispatch boundary, including partial calls.
                    for choice in getattr(response, 'choices', []):
                        for call in getattr(choice.message, 'tool_calls', []) or []:
                            dispatch(call)
                    raise AssertionError('Pending stream unexpectedly returned a response')
                if retry_count == ATTEMPTS - 1:
                    break
                retry_count += 1
                clock.sleep(retry_count)  # Bounded synthetic retry backoff.

            assert failures == [
                'Non-streaming API call timed out after 90s with no response (threshold: 90s)'
            ] * ATTEMPTS, failures
            assert kills == ['stale_call_kill'] * ATTEMPTS, kills
            assert effective == [WATCHDOG_SECONDS] * ATTEMPTS, effective
            assert boundary_checks.count((90.0, 90.0)) == ATTEMPTS
            assert boundary_checks.count((90.5, 90.0)) == ATTEMPTS
            assert len(workers) == agent.announcements == ATTEMPTS
            assert all(w.polls == 181 and not w.is_alive() for w in workers)
            assert len(agent.events) == ATTEMPTS * 182
            dispatch.assert_not_called()
            assert not list(Path(directory).rglob('*')), 'Unexpected file created'
            return {'result': 'PASS', 'timeout_branch': 'total_wall_clock_request_deadline',
                    'effective_watchdog_seconds_per_attempt': effective,
                    'retry_count': retry_count, 'announcement_count': agent.announcements,
                    'partial_dispatch': dispatch.called, 'file_created': False}
        finally:
            sys.settrace(previous_trace)
            os.chdir(original_directory)


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({'result': 'FAIL', 'error': str(error)}))
        raise
