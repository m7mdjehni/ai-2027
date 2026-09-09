"""Worker-local observation and watchdog progress; never serialize content."""
from hermes_project_watchdog import CURRENT_WATCHDOG
ALLOWED = frozenset({'project_write_file', 'project_read_file', 'project_list_files'})
COUNTERS = ('codex_function_calls_announced', 'codex_function_argument_delta_events',
    'codex_function_argument_bytes', 'codex_function_argument_done_events',
    'codex_function_output_items_done', 'codex_normalized_tool_calls',
    'codex_incomplete_function_calls', 'codex_terminal_output_function_calls_detected',
    'codex_unassociated_argument_events')
FLAGS = ('codex_response_completed_seen', 'codex_response_failed_seen',
         'codex_response_incomplete_seen', 'codex_observation_tracking_overflow', 'final_request_policy_rejected')


class CodexPolicyViolation(BaseException):
    """Terminal policy rejection; must not enter provider retries."""


def field(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def initialize(agent):
    for key in COUNTERS: agent.diagnostics.setdefault(key, 0)
    for key in FLAGS: agent.diagnostics.setdefault(key, False)


def check_request(agent, kwargs):
    initialize(agent)
    # Match the SDK's top-level JSON merge, including extra_body precedence.
    extra = kwargs.get('extra_body') or {}
    if not isinstance(extra, dict):
        agent.diagnostics['final_request_policy_rejected'] = True
        agent._emit_repair_diagnostics(force=True)
        raise CodexPolicyViolation()
    tools = extra.get('tools', kwargs.get('tools', []))
    choice = extra.get('tool_choice', kwargs.get('tool_choice'))
    parallel = extra.get('parallel_tool_calls', kwargs.get('parallel_tool_calls'))
    valid = (isinstance(tools, list) and len(tools) == 3
        and all(isinstance(t, dict) and t.get('type') == 'function' and isinstance(t.get('name'), str) for t in tools)
        and {t.get('name') for t in tools} == ALLOWED)
    name = choice.get('name') if isinstance(choice, dict) else None
    kind = choice.get('type') if isinstance(choice, dict) else choice
    valid = valid and ((kind == 'function' and isinstance(name, str) and name in ALLOWED)
                      or (isinstance(choice, str) and choice in {'auto', 'required', 'none'}))
    valid = valid and type(parallel) is bool
    if not getattr(agent, 'repair_mode', False) and not getattr(agent, '_project_file_written', False):
        valid = valid and choice == {'type':'function', 'name':'project_write_file'} and parallel is False
    if not valid:
        agent.diagnostics['final_request_policy_rejected'] = True
        agent._emit_repair_diagnostics(force=True)
        raise CodexPolicyViolation()
    agent.diagnostics.update(final_request_tool_count=3,
        final_request_tool_names=','.join(sorted(ALLOWED)),
        final_request_tool_choice_type=kind, final_request_parallel_tool_calls=parallel)
    agent.diagnostics.pop('final_request_forced_tool_name', None)
    if name is not None: agent.diagnostics['final_request_forced_tool_name'] = name
    agent._emit_repair_diagnostics(force=True)


class StreamObserver:
    def __init__(self, agent):
        self.agent = agent; self.pending = set(); self.completed = False
        self.structured_pending = set()
        self.watchdog = CURRENT_WATCHDOG.get()
        self.base_incomplete = agent.diagnostics['codex_incomplete_function_calls']

    def observe(self, event):
        d = self.agent.diagnostics; kind = field(event, 'type', '')
        structured = False
        item = field(event, 'item'); item_type = field(item, 'type')
        if kind == 'response.output_item.added' and item_type == 'function_call':
            d['codex_function_calls_announced'] += 1
            ident = field(item, 'id')
            if isinstance(ident, str) and len(ident) <= 256 and len(self.pending) < 128:
                self.pending.add(ident)
                if field(item, 'name') in ALLOWED:
                    self.structured_pending.add(ident)
            else: d['codex_observation_tracking_overflow'] = True
        elif kind in {'response.function_call_arguments.delta', 'response.function_call_arguments.done'}:
            if field(event, 'item_id') not in self.pending:
                d['codex_unassociated_argument_events'] += 1
            if kind.endswith('.delta'):
                d['codex_function_argument_delta_events'] += 1
                delta = field(event, 'delta', '')
                if isinstance(delta, str): d['codex_function_argument_bytes'] += len(delta.encode('utf-8', errors='replace'))
                structured = (field(event, 'item_id') in self.structured_pending
                              and isinstance(delta, str) and bool(delta))
            else: d['codex_function_argument_done_events'] += 1
        elif kind == 'response.output_item.done' and item_type == 'function_call':
            d['codex_function_output_items_done'] += 1
            self.pending.discard(field(item, 'id'))
            self.structured_pending.discard(field(item, 'id'))
            if field(item, 'status') in {'queued', 'in_progress', 'incomplete'}:
                self.base_incomplete += 1
        if kind in {'response.completed', 'response.failed', 'response.incomplete'}:
            d[{'response.completed':'codex_response_completed_seen', 'response.failed':'codex_response_failed_seen',
               'response.incomplete':'codex_response_incomplete_seen'}[kind]] = True
            output = field(field(event, 'response'), 'output', [])
            if isinstance(output, list):
                d['codex_terminal_output_function_calls_detected'] += sum(field(i, 'type') == 'function_call' for i in output)
            if kind == 'response.completed':
                self.completed = True
                # Installed parser settles announced pending calls on completed.
                self.pending.clear()
                self.structured_pending.clear()
        if self.watchdog is not None:
            self.watchdog.observe(structured)
        d['codex_incomplete_function_calls'] = self.base_incomplete + len(self.pending)
        self.agent._emit_repair_diagnostics()


class ObservedStream:
    def __init__(self, stream, agent):
        self.stream = stream; self.observer = StreamObserver(agent)
    def __getattr__(self, key): return getattr(self.stream, key)
    def __iter__(self):
        try:
            for event in self.stream:
                self.observer.observe(event)
                yield event  # Original event unchanged; Hermes alone parses it.
        finally:
            self.observer.agent._emit_repair_diagnostics(force=True)
    def close(self): return self.stream.close()


class ObservedResponses:
    def __init__(self, responses, agent): self.responses = responses; self.agent = agent
    def __getattr__(self, key): return getattr(self.responses, key)
    def create(self, **kwargs):
        check_request(self.agent, kwargs)
        return ObservedStream(self.responses.create(**kwargs), self.agent)


class ObservedClient:
    def __init__(self, client, agent):
        self.client = client; self.responses = ObservedResponses(client.responses, agent)
    def __getattr__(self, key): return getattr(self.client, key)


class ObservedTransport:
    def __init__(self, transport, agent): self.transport = transport; self.agent = agent
    def __getattr__(self, key): return getattr(self.transport, key)
    def normalize_response(self, response, **kwargs):
        normalized = self.transport.normalize_response(response, **kwargs)
        # Hermes may normalize the same response for inspection and dispatch.
        if getattr(self.agent, '_codex_observed_response', None) is not response:
            initialize(self.agent)
            self.agent.diagnostics['codex_normalized_tool_calls'] += len(normalized.tool_calls or [])
            self.agent._codex_observed_response = response
            self.agent._emit_repair_diagnostics(force=True)
        return normalized
