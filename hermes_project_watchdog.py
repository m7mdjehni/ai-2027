"""Project Builder only: bounded Codex waits; no parser or provider changes."""
from contextvars import ContextVar, copy_context
import math
import os
import threading
import time


# Five minutes per request, plus at most two seconds for abort cleanup. The
# existing supervisor independently caps the whole worker (900s by default).
HARD_DEADLINE_SECONDS = 300.0
CURRENT_WATCHDOG = ContextVar('project_codex_watchdog', default=None)


def positive_seconds(value, default):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else default
    except (TypeError, ValueError):
        return default


class Watchdog:
    def __init__(self, initial, idle, first_event, *, continuation=False):
        self.started = time.monotonic()
        self.initial = min(positive_seconds(initial, 90.0), HARD_DEADLINE_SECONDS)
        self.idle = idle
        self.first_event = first_event
        self.continuation = continuation
        self.last_event = self.last_argument = None
        self.cancelled = False
        self.lock = threading.Lock()

    def observe(self, structured=False):
        with self.lock:
            if self.cancelled:
                raise TimeoutError('Project Codex request already cancelled')
            self.last_event = time.monotonic()
            if structured:
                self.last_argument = self.last_event

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.started
            active = self.last_argument is not None and now - self.last_argument <= self.idle
            reason = None
            if elapsed >= HARD_DEADLINE_SECONDS:
                reason = 'hard_absolute_deadline'
            elif self.last_event is None and elapsed >= self.first_event:
                reason = 'time_to_first_event'
            elif self.last_event is not None and now - self.last_event >= self.idle:
                reason = 'stream_inactivity'
            elif elapsed >= self.initial and not active and not self.continuation:
                reason = 'initial_request_watchdog'
            return reason, {
                'initial_watchdog_seconds': self.initial,
                'active_stream_watchdog_mode': ('structured_argument_progress' if active else
                                               'post_tool_continuation' if self.continuation else 'initial_request'),
                'watchdog_turn_phase': 'post_tool_continuation' if self.continuation else 'initial_request',
                'hard_absolute_deadline_seconds': HARD_DEADLINE_SECONDS,
                'watchdog_elapsed_seconds': elapsed,
                'structured_argument_progress_observed': self.last_argument is not None,
            }


def interruptible_project_codex_call(agent, payload):
    """Own only the wait/abort policy; Hermes still builds and parses the stream.

    Request-local state is bound to the worker context so a cancelled worker
    cannot extend a later retry's deadline. A late response is never returned.
    """
    if agent._interrupt_requested:
        raise InterruptedError('Project Codex request interrupted')
    state = Watchdog(agent._compute_non_stream_stale_timeout(payload),
                     positive_seconds(os.getenv('HERMES_CODEX_EVENT_STALE_TIMEOUT_SECONDS'), 60.0),
                     positive_seconds(os.getenv('HERMES_CODEX_TTFB_TIMEOUT_SECONDS'), 120.0),
                     continuation=getattr(agent, '_project_successful_tool_dispatch', False))
    agent._project_watchdog_requests = getattr(agent, '_project_watchdog_requests', 0) + 1
    # Hermes increments _api_call_count for model turns, outside its retry loop.
    # Request attempts are separate: a retry must not masquerade as a new turn.
    agent.diagnostics.update(
        model_turn_number=getattr(agent, '_api_call_count', 1),
        watchdog_request_number=agent._project_watchdog_requests,
        prior_tool_call_dispatched=agent.diagnostics.get('tool_calls', 0) > 0,
        prior_successful_tool_call=state.continuation)
    result = {}
    client_holder = {}
    client_lock = threading.Lock()
    on_first_delta = getattr(agent, '_codex_on_first_delta', None)

    def publish(reason=None):
        _, values = state.snapshot()
        agent.diagnostics.update(values)
        agent.diagnostics['total_build_elapsed_seconds'] = max(
            0.0, time.monotonic() - getattr(agent, 'diagnostic_start', state.started))
        agent.diagnostics['watchdog_termination_reason'] = reason or 'pending'
        agent._emit_repair_diagnostics(force=True)

    def call():
        token = CURRENT_WATCHDOG.set(state)
        client = None
        try:
            client = agent._create_request_openai_client(reason='codex_stream_request', api_kwargs=payload)
            with client_lock:
                if state.cancelled:
                    return
                client_holder['client'] = client
            result['response'] = agent._run_codex_stream(payload, client=client, on_first_delta=on_first_delta)
        except BaseException as error:
            result['error'] = error
        finally:
            try:
                # Serialize abort vs close/cache reuse, matching Hermes ownership.
                with client_lock:
                    client_holder.clear()
                if client is not None:
                    agent._close_request_openai_client(client, reason=(
                        'request_complete' if 'response' in result and not state.cancelled
                        else 'request_error_cleanup'))
            except BaseException as error:
                result.setdefault('error', error)
            finally:
                CURRENT_WATCHDOG.reset(token)

    context = copy_context()
    worker = threading.Thread(target=lambda: context.run(call), daemon=True)
    publish()
    worker.start()
    while True:
        worker.join(timeout=0.3)
        reason, _ = state.snapshot()
        if agent._interrupt_requested:
            reason = 'interrupted'
        if reason:
            with state.lock:
                state.cancelled = True
            # Record the decision before cleanup so elapsed excludes abort grace.
            publish(reason)
            with client_lock:
                client = client_holder.get('client')
                if client is not None:
                    try:
                        agent._abort_request_openai_client(client, reason='stale_call_kill')
                    except Exception:
                        pass  # Deadline still wins; never dispatch a late result.
            worker.join(timeout=2.0)
            if reason == 'interrupted':
                raise InterruptedError('Project Codex request interrupted')
            raise TimeoutError('Project Codex timeout: ' + reason)
        if not worker.is_alive():
            publish('provider_error' if 'error' in result else 'completed')
            if 'error' in result:
                raise result['error']
            return result['response']
        agent.diagnostics.update(state.snapshot()[1])
        agent.diagnostics['total_build_elapsed_seconds'] = max(
            0.0, time.monotonic() - getattr(agent, 'diagnostic_start', state.started))
        agent._touch_activity('waiting for Project Codex response')
        agent._emit_repair_diagnostics()
