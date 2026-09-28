"""Real local acceptance. Only --internet-disconnected records an offline claim.

Use that flag only AFTER manually disconnecting Internet. A successful public TCP
probe rejects the claim; failed probes alone do not establish disconnection.
Creates and retains a new project and its database history; changes no old project.
"""
import argparse
import json
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import project_workspace as ws


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--internet-disconnected', action='store_true')
    args = parser.parse_args()
    evidence = {'started_at': datetime.now(timezone.utc).isoformat(),
                'internet_disconnected_attested': args.internet_disconnected, 'checks': {}}
    folder = ROOT / 'runtime' / 'offline'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('disconnected.json' if args.internet_disconnected else 'local-connected.json')
    def record(name, value=True):
        evidence['checks'][name] = value
        path.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print('PASS: ' + name, flush=True)
    session = requests.Session(); session.trust_env = False
    def call(method, route, payload=None, timeout=20, base='http://127.0.0.1:8000'):
        r = session.request(method, base + route, json=payload, timeout=timeout, allow_redirects=False)
        if r.status_code not in {200, 201, 202}: raise RuntimeError('Unexpected API status: ' + str(r.status_code))
        return r.json()
    try:
        if args.internet_disconnected:
            for address in ['1.1.1.1', '8.8.8.8']:
                try:
                    with socket.create_connection((address, 443), timeout=3):
                        raise RuntimeError('Public Internet remains reachable; disconnect all uplinks')
                except OSError: pass
            record('public_tcp_probes_unreachable')
        tags = call('GET', '/api/tags', base='http://127.0.0.1:11434')
        assert any(m['name'] == 'llama3.2:3b' for m in tags['models'])
        record('ollama_model_available')
        reply = call('POST', '/api/chat', {'model': 'llama3.2:3b', 'stream': False,
                     'messages': [{'role': 'user', 'content': 'Reply exactly: LOCAL MODEL WORKING'}],
                     'options': {'temperature': 0}}, timeout=180, base='http://127.0.0.1:11434')
        assert reply['message']['content'].strip()
        record('direct_model_response', reply['message']['content'])
        call('GET', '/')
        record('backend_http')
        health = call('GET', '/health', base='http://127.0.0.1:8766')
        assert health['project_engine'] == 'ollama'
        record('local_project_helper')
        reply = call('POST', '/api/converse', {'message': 'Reply exactly: LOCAL CHAT WORKING'}, timeout=200)
        assert reply['provider'] == 'ollama' and reply['model'] == 'llama3.2:3b' and reply['reply'].strip()
        record('backend_local_chat', {k: reply[k] for k in ['provider', 'model', 'reply']})
        marker = 'Offline audit ' + uuid.uuid4().hex[:8]
        task = call('POST', '/api/tasks', {'title': marker})['task']
        note = call('POST', '/api/notes', {'title': marker, 'content': 'Local persistence verification'})['note']
        assert any(t['id'] == task['id'] for t in call('GET', '/api/tasks'))
        assert any(n['id'] == note['id'] for n in call('GET', '/api/notes'))
        assert call('POST', '/api/tasks/' + str(task['id']) + '/done', {'done': True})['done']
        record('database_tasks_notes', {'task_id': task['id'], 'note_id': note['id']})
        created = call('POST', '/api/projects', {'prompt': 'Create a simple responsive one-page student portfolio with Home, About, Projects, and Contact sections. Use index.html, styles.css, and script.js. Use only local assets and system fonts.'}, timeout=350)
        evidence['project_id'] = pid = created['project_id']
        assert created['success'] and created['provider'] == 'ollama', 'Local build failed'
        assert created['model'] == 'llama3.2:3b' and created['fallback_used'] is False
        assert {'index.html', 'styles.css', 'script.js'} <= set(ws.list_files(pid))
        record('local_project_generation', {'provider': created['provider'], 'model': created['model'], 'files': ws.list_files(pid)})
        prefix = '/api/projects/' + pid
        def validate():
            rev = call('GET', prefix + '/revision')['revision']
            return rev, call('POST', prefix + '/validate', {'revision': rev}, timeout=90)
        _, baseline = validate()
        assert baseline['passed'], 'Generated project needs repair before baseline demo'
        record('baseline_validation', baseline)
        ws.write_file(pid, 'script.js', ws.read_file(pid, 'script.js') + '\nconst demoBroken = ;\n')
        revision, broken = validate()
        assert not broken['passed'] and any(e['check'] == 'javascript-syntax' for e in broken['errors'])
        record('deliberate_syntax_error_detected', broken)
        run_id = uuid.uuid4().hex; evidence['run_id'] = run_id
        call('POST', prefix + '/runs', {'run_id': run_id, 'revision': revision})
        deadline = time.monotonic() + 660
        while time.monotonic() < deadline:
            run = call('GET', prefix + '/runs/' + run_id)
            if run['finished_at']: break
            time.sleep(2)
        else: raise RuntimeError('Repair did not finish within the acceptance deadline')
        assert run['status'] == 'completed', 'Local repair failed: ' + str(run.get('failure_class'))
        assert run['attempts'] and all(a['provider'] == 'ollama' for a in run['attempts'])
        assert any(a.get('changed_files') for a in run['attempts'])
        _, final = validate()
        assert final['passed']
        record('real_ollama_repair', {'attempts': run['attempts'], 'final': final})
        demos = {'water_shop': '0b59fd2ac45b45aabe3febf97060f464',
                 'preserved_portfolio': '024b7d588ddd4c579bac7ee7351b07a2',
                 'coffee_demo': 'fd28085590c6494093c5a5ce20992d66'}
        for name, demo_id in demos.items():
            route = '/api/projects/' + demo_id
            revision = call('GET', route + '/revision')['revision']
            result = call('POST', route + '/validate', {'revision': revision}, timeout=90)
            assert result['passed'], name + ' validation failed'
            record(name + '_validation', result)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True, args=['--disable-background-networking'])
            context = browser.new_context(service_workers='block', viewport={'width': 1440, 'height': 900})
            from urllib.parse import urlsplit
            external = []; failures = []; errors = []
            def intercept(route):
                if urlsplit(route.request.url).hostname not in {'localhost', '127.0.0.1'}:
                    external.append(route.request.url); route.abort()
                else: route.continue_()
            context.route('**/*', intercept)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            page.on('requestfailed', lambda request: failures.append(request.url))
            page.on('response', lambda response: failures.append(response.url) if response.status >= 400 else None)
            for name, url in [('main_ui', 'http://127.0.0.1:8000/static/index.html'),
                              ('new_project', 'http://127.0.0.1:8050/' + pid + '/index.html'),
                              *[(name, 'http://127.0.0.1:8050/' + demo_id + '/index.html')
                                for name, demo_id in demos.items()]]:
                response = page.goto(url, wait_until='networkidle')
                assert response.status == 200
                assert page.locator('body').inner_text().strip()
                assert not external and not failures and not errors, 'Browser asset or JavaScript failure'
                page.screenshot(path=str(folder / (name + '.png')), full_page=True)
                record(name + '_browser_local_assets')
            browser.close()
        evidence['result'] = 'OFFLINE PASS' if args.internet_disconnected else 'LOCAL PASS; Internet-disconnected test pending'
        print(evidence['result'], flush=True)
    except Exception as error:
        evidence['result'] = 'FAIL'
        evidence['error_type'] = type(error).__name__
        print('FAIL: ' + type(error).__name__ + '; inspect retained evidence and project', flush=True)
        raise
    finally:
        path.write_text(json.dumps(evidence, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__': main()
