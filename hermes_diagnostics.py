"""Allowlisted scalar diagnostics only. Never serialize provider bodies or headers."""
import re
import json
import os
from pathlib import Path

DIAGNOSTIC_ROOT = Path(__file__).resolve().parent / 'runtime' / 'repair_diagnostics'


def persist_worker_diagnostics(project_id, data):
    """Trusted, atomic scalar snapshot outside all model-readable workspaces."""
    from app.project_workspace import validate_id, reject_link
    validate_id(project_id)
    for path in (DIAGNOSTIC_ROOT.parent, DIAGNOSTIC_ROOT): reject_link(path)
    DIAGNOSTIC_ROOT.mkdir(parents=True, exist_ok=True)
    target=DIAGNOSTIC_ROOT/(project_id+'.json')
    temporary=DIAGNOSTIC_ROOT/(project_id+'.tmp')
    reject_link(target); reject_link(temporary)
    temporary.write_text(json.dumps(safe_diagnostics(data),sort_keys=True),encoding='utf-8')
    os.replace(temporary,target)

MODELS = {'gpt-5.6-sol', 'mimo-v2.5-free'}
PROVIDERS = {'openai-codex', 'opencode-free', 'openrouter'}
API_MODES = {'codex_responses', 'chat_completions', 'anthropic_messages'}


def http_status(value):
    match = re.search(r'(?i)(?:http(?: status)?|status(?:_code)?|error code)\s*[:=]?\s*([45]\d\d)\b', str(value or ''))
    return int(match.group(1)) if match else None


def failure_category(value):
    text = str(value or '').lower()
    if 'timed out' in text or 'timeout' in text:
        return 'timeout'
    if http_status(text):
        return 'http_error'
    if 'rate limit' in text or 'too many requests' in text:
        return 'rate_limit'
    return 'worker_failure'


def exception_category(error):
    name = type(error).__name__
    return name if name in {'TimeoutError', 'TimeoutExpired', 'ValueError', 'TypeError', 'OSError',
        'RuntimeError', 'APIError', 'APIConnectionError', 'APITimeoutError', 'BadRequestError',
        'RateLimitError', 'AuthenticationError', 'PermissionDeniedError'} else 'Exception'


def safe_diagnostics(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    enums = {'model': MODELS, 'provider': PROVIDERS, 'api_mode': API_MODES,
             'failure_category': {'timeout', 'http_error', 'rate_limit', 'worker_failure', 'security_violation'},
             'exception_type': {'TimeoutError', 'TimeoutExpired', 'ValueError', 'TypeError', 'OSError', 'RuntimeError',
                 'APIError', 'APIConnectionError', 'APITimeoutError', 'BadRequestError', 'RateLimitError',
                 'AuthenticationError', 'PermissionDeniedError', 'Exception'}}
    from hermes_codex_observer import ALLOWED, COUNTERS, FLAGS
    enums.update(final_request_forced_tool_name=ALLOWED,
                 active_stream_watchdog_mode={'initial_request', 'structured_argument_progress', 'post_tool_continuation'},
                 watchdog_turn_phase={'initial_request', 'post_tool_continuation'},
                 watchdog_termination_reason={'pending', 'completed', 'provider_error', 'interrupted',
                     'hard_absolute_deadline', 'time_to_first_event', 'stream_inactivity', 'initial_request_watchdog'},
                 final_request_tool_names={','.join(sorted(ALLOWED))},
                 final_request_tool_choice_type={'function', 'auto', 'required', 'none'})
    for key, allowed in enums.items():
        if isinstance(data.get(key), str) and data[key] in allowed:
            result[key] = data[key]
    for key in ['write_file_invoked', 'fresh_worker_session', 'workspace_matches_request', 'completed',
                'final_request_parallel_tool_calls', 'structured_argument_progress_observed',
                'prior_tool_call_dispatched', 'prior_successful_tool_call', *FLAGS]:
        if type(data.get(key)) is bool:
            result[key] = data[key]
    for key in ['api_attempts', 'tool_calls', 'tool_failures', 'stream_events', 'text_characters', 'reasoning_characters',
                'first_event_ms', 'last_event_ms', 'elapsed_ms', 'final_response_characters', 'effective_watchdog_seconds',
                'final_request_tool_count', 'initial_watchdog_seconds', 'hard_absolute_deadline_seconds',
                'watchdog_elapsed_seconds', 'total_build_elapsed_seconds',
                'model_turn_number', 'watchdog_request_number', *COUNTERS]:
        if type(data.get(key)) in {int, float} and 0 <= data[key] <= 1_000_000_000:
            result[key] = data[key]
    if type(data.get('http_status')) is int and 400 <= data['http_status'] <= 599:
        result['http_status'] = data['http_status']
    return result
