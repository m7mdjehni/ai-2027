"""Live acceptance: creates ONE new project; never edits/deletes older projects.

Run from the project directory with both services and the validator running.
No credential files are read and no raw HTTP/provider errors are printed.
"""
import argparse
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import project_workspace as ws


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8000')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.username or parsed.password:
        parser.error('Use the local backend HTTP address without credentials')
    session = requests.Session()
    session.trust_env = False

    health = session.get('http://localhost:8765/health', timeout=10, allow_redirects=False)
    if health.status_code != 200 or health.json().get('status') != 'ok':
        raise RuntimeError('Host health check failed')
    print('Host health passed', flush=True)

    def call(method, path, payload=None, timeout=15):
        result = session.request(method, base + path, json=payload, timeout=timeout, allow_redirects=False)
        if not 200 <= result.status_code < 300:
            raise RuntimeError('API check failed with HTTP ' + str(result.status_code))
        return result.json()

    call('GET', '/')
    print('Backend reachable', flush=True)
    reply = call('POST', '/api/converse', {'message': 'Explain autonomous coding agents in one sentence.'}, timeout=330)
    if not isinstance(reply.get('reply'), str) or not reply['reply'].strip():
        raise RuntimeError('Chat returned no answer')
    print('Chat passed', flush=True)
    created = call('POST', '/api/projects', {'prompt': 'Create a modern responsive coffee shop website with Home, About, Menu, and Contact sections. Use index.html, styles.css, and script.js with a working mobile menu.'}, timeout=630)
    pid = created['project_id']
    ws.validate_id(pid)
    print('New presentation project: ' + pid, flush=True)
    if not created.get('success'):
        raise RuntimeError('Build failed; project retained for inspection')
    files = ws.list_files(pid)
    if 'index.html' not in files:
        raise RuntimeError('Website entry point missing')
    prefix = '/api/projects/' + pid

    def validate():
        revision = call('GET', prefix + '/revision')['revision']
        result = call('POST', prefix + '/validate', {'revision': revision}, timeout=85)
        return revision, result

    _, baseline = validate()
    if not baseline.get('passed'):
        raise RuntimeError('Generated project failed baseline validation')
    print('Build and baseline validation passed', flush=True)
    name = next((name for name in files if name.endswith('.js')), None)
    if name is None:
        raise RuntimeError('No JavaScript file available for this demonstration')
    original = ws.read_file(pid, name)
    ws.write_file(pid, name, original + '\nconst demoBroken = ;\n')
    revision, broken = validate()
    if broken.get('passed') or not any(error.get('file') == name for error in broken.get('errors', [])):
        raise RuntimeError('Validator did not detect the introduced failure')
    print('Introduced JavaScript error detected', flush=True)
    run_id = uuid.uuid4().hex
    call('POST', prefix + '/runs', {'run_id': run_id, 'revision': revision})
    deadline = time.monotonic() + 720
    while time.monotonic() < deadline:
        run = call('GET', prefix + '/runs/' + run_id)
        if run.get('finished_at'):
            if run.get('status') != 'completed' or not run.get('validations') or not run['validations'][-1].get('passed'):
                raise RuntimeError('Repair did not complete successfully; inspect Run Monitor')
            _, final = validate()
            if not final.get('passed'):
                raise RuntimeError('Final project failed validation')
            print('PASS: build -> syntax failure -> durable AI repair -> validation passed', flush=True)
            print('Project and Run Monitor history retained', flush=True)
            return 0
        time.sleep(2)
    call('POST', prefix + '/runs/' + run_id + '/cancel', {})
    raise RuntimeError('Repair deadline reached; cancellation requested')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except requests.RequestException:
        print('BLOCKED: local API unavailable or timed out. Check services and Run Monitor before retrying.')
        raise SystemExit(1)
    except RuntimeError as error:
        # Only fixed messages constructed above; never raw HTTP/provider bodies.
        print('FAIL: ' + str(error))
        raise SystemExit(1)
    except (ValueError, OSError, KeyError, TypeError):
        print('FAIL: presentation flow did not complete. Inspect the retained project and Run Monitor.')
        raise SystemExit(1)
