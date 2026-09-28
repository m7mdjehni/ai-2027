"""Local-only Ollama HTTP transport. Never uses a proxy or cloud fallback."""
import os
from urllib.parse import urlsplit

import requests


def model_name():
    model = os.getenv('OLLAMA_MODEL', 'llama3.2:3b')
    if not model or 'cloud' in model.lower() or '/' in model:
        raise ValueError('Use an installed local Ollama model')
    return model


def chat(messages, *, host=False, output_format=None, timeout=180):
    key = 'OLLAMA_HOST_BASE_URL' if host else 'OLLAMA_BASE_URL'
    base = os.getenv(key, 'http://127.0.0.1:11434').rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'http' or url.hostname not in
            {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}
            or url.username or url.password or url.query or url.fragment
            or url.path):
        raise ValueError('Ollama must use a local HTTP endpoint')
    payload = {'model': model_name(), 'messages': messages, 'stream': False,
               'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 4096}}
    if output_format is not None:
        payload['format'] = output_format
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(base + '/api/chat', json=payload,
                                timeout=(5, timeout), allow_redirects=False)
        if response.status_code != 200:
            raise requests.HTTPError('Local Ollama returned HTTP ' + str(response.status_code),
                                     response=response)
        content = response.json().get('message', {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Local Ollama returned an empty response')
    return content
