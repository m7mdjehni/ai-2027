"""Local-only Ollama HTTP transport. Never uses a proxy or cloud fallback."""
import os
import random
import re
from urllib.parse import urlsplit

import requests

DEFAULT_OLLAMA_MODELS = ('qwen2.5:1.5b', 'llama3.2:3b')


def model_names():
    configured = os.getenv('OLLAMA_MODELS')
    if configured and configured.strip():
        models = [item.strip() for item in configured.split(',')]
    else:
        models = list(DEFAULT_OLLAMA_MODELS)
        legacy_model = os.getenv('OLLAMA_MODEL', '').strip()
        if legacy_model:
            models.append(legacy_model)
    models = list(dict.fromkeys(models))
    if not models or any(not model or 'cloud' in model.lower() or '/' in model
                         for model in models):
        raise ValueError('Use installed local Ollama models')
    return tuple(models)


def model_name(exclude=()):
    available = [model for model in model_names() if model not in exclude]
    if not available:
        raise ValueError('No alternate local Ollama model is configured')
    return random.choice(available)


def chat_with_model(messages, *, host=False, output_format=None, timeout=180, model=None):
    key = 'OLLAMA_HOST_BASE_URL' if host else 'OLLAMA_BASE_URL'
    base = os.getenv(key, 'http://127.0.0.1:11434').rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'http' or url.hostname not in
            {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}
            or url.username or url.password or url.query or url.fragment
            or url.path):
        raise ValueError('Ollama must use a local HTTP endpoint')
    context_length = int(os.getenv('OLLAMA_CONTEXT_LENGTH', '2048'))
    configured_models = model_names()
    selected_model = model or model_name()
    if selected_model not in configured_models:
        raise ValueError('Use a model from the configured local Ollama pool')
    candidates = [selected_model] + random.sample(
        [candidate for candidate in configured_models if candidate != selected_model],
        len(configured_models) - 1,
    )
    last_error = None
    for candidate in candidates:
        payload = {'model': candidate, 'messages': messages, 'stream': False,
                   'options': {'temperature': 0, 'num_ctx': context_length, 'num_predict': 1024}}
        if output_format is not None:
            payload['format'] = output_format
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(base + '/api/chat', json=payload,
                                        timeout=(5, timeout), allow_redirects=False)
                if response.status_code != 200:
                    error = requests.HTTPError('Local Ollama returned HTTP ' + str(response.status_code),
                                               response=response)
                    if response.status_code not in {404, 500}:
                        raise error
                    last_error = error
                    continue
                content = response.json().get('message', {}).get('content')
        except ValueError as error:
            last_error = error
            continue
        except requests.RequestException as error:
            if (not isinstance(error, requests.HTTPError)
                    or getattr(error.response, 'status_code', None) not in {404, 500}):
                raise
            last_error = error
            continue
        if not isinstance(content, str) or not content.strip() or _has_repeated_tokens(content):
            last_error = ValueError('Local Ollama returned empty or repetitive output')
            continue
        return content, candidate
    if last_error is not None:
        raise last_error
    raise ValueError('No local Ollama model produced a usable response')


def _has_repeated_tokens(content):
    words = re.findall(r'\S+', content.lower())
    repeated = 1
    duplicate_pairs = 0
    for previous, current in zip(words, words[1:]):
        if previous == current:
            duplicate_pairs += 1
            if duplicate_pairs >= 3:
                return True
        repeated = repeated + 1 if previous == current else 1
        if repeated >= 12:
            return True
    return False


def chat(messages, *, host=False, output_format=None, timeout=180):
    content, _ = chat_with_model(messages, host=host, output_format=output_format, timeout=timeout)
    return content
