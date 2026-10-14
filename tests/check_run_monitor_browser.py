"""Real Edge rendering with every page request intercepted; no live API/model calls.

Requires Playwright in the development virtual environment and installed Edge.
Screenshots are visibly labelled offline fixtures, not live provider evidence.
"""
import json
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
PROJECT='a'*32
RUN='b'*32
REVISION='c'*64
project={'project_id':PROJECT,'project_name':'Offline fixture — Travel website',
    'status':'completed','model':'gpt-5.6-sol','provider':'openai-codex','success':True,
    'fallback_used':False,'preview_pending':True,'files':['index.html','script.js'],
    'message':'Browser test fixture. No provider request was made.'}
failure={'revision':REVISION,'profile':'static-web-v1','passed':False,'status':'failed',
    'errors':[{'file':'script.js','line':18,'column':4,'check':'javascript-syntax','code':'syntax_error',
        'message':'<script>window.diagnosticExecuted = true</script>'}]}
run={'id':RUN,'project_id':PROJECT,'task':'Repair managed static website — offline fixture',
    'status':'completed','failure_class':'repaired_successfully','created_at':'2026-09-07T13:00:00Z',
    'started_at':'2026-09-07T13:00:01Z','finished_at':'2026-09-07T13:00:12Z',
    'repair_count':1,'max_repairs':3,'validation_count':2,'cleanup_confirmed':True,
    'cancellation_requested':False,'event_sequence':6,
    'attempts':[{'repair_cycle':1,'model':'gpt-5.6-sol','provider':'openai-codex',
        'attempt_result':'completed','changed_files':['script.js']}],
    'validations':[failure,{'revision':'d'*64,'profile':'static-web-v1','passed':True,'status':'passed','errors':[]}]}
events=[{'sequence':i+1,'created_at':f'2026-09-07T13:00:{i+1:02d}Z','event_type':kind,
         'safe_summary':kind.replace('_',' ')} for i,kind in enumerate([
             'run_created','baseline_failed','repair_started','files_changed','validation_passed','run_completed'])]


def main():
    screenshots=ROOT/'docs'/'screenshots'; screenshots.mkdir(exist_ok=True)
    requests=[]; errors=[]
    publications=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=True,args=['--disable-background-networking'])
        context=browser.new_context(viewport={'width':1440,'height':1800},service_workers='block')
        def route_request(route):
            url=urlparse(route.request.url); requests.append((route.request.method,url.path))
            if url.hostname!='phase4.test': return route.abort()
            if url.path.startswith('/static/'):
                name=url.path.removeprefix('/static/')
                if name not in {'index.html','projects.css','project-runs.js','project-publications.js','projects.js'}: return route.abort()
                path=ROOT/'app'/'static'/name
                content_type='text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript'
                return route.fulfill(body=path.read_bytes(),content_type=content_type)
            if url.path.endswith('/publications'): return route.fulfill(json=publications)
            if url.path.endswith('/publications/eligibility'):
                return route.fulfill(json={'revision':REVISION,'eligible':True,'reason':None})
            if url.path.endswith('/publications/preview'):
                return route.fulfill(json={'id':'9'*32,'revision':REVISION,
                    'repository':'example/managed-'+PROJECT,'files':['index.html','script.js']})
            if url.path.endswith('/publications/'+'9'*32+'/confirm'):
                publications.append({'id':'9'*32,'revision':REVISION,'repository':'example/managed-'+PROJECT,
                    'status':'completed','commit_sha':'8'*40,'public_url':'https://example.github.io/managed-'+PROJECT+'/',
                    'safe_summary':'Offline publication fixture','events':[{'state':s} for s in
                        ['pending','exporting','repository_creating','uploading','deploying','completed']]})
                return route.fulfill(status=202,json=publications[0])
            if route.request.method=='POST' and url.path.endswith('/runs'):
                payload=route.request.post_data_json
                assert payload['revision']==REVISION
                run.update(id=payload['run_id'],status='queued',failure_class=None,repair_count=0,
                    validation_count=0,attempts=[],validations=[],started_at=None,finished_at=None,
                    cancellation_requested=False,cleanup_confirmed=False,event_sequence=1)
                events[:]=[{'sequence':1,'created_at':'2026-09-07T13:01:00Z',
                    'event_type':'run_created','safe_summary':'run created'}]
                return route.fulfill(status=202,json=run)
            if route.request.method=='POST' and url.path.endswith('/cancel'):
                run.update(status='cancelling',cancellation_requested=True)
                return route.fulfill(json=run)
            if url.path.endswith('/revision'):data={'revision':REVISION}
            elif url.path==f'/api/projects/{PROJECT}/runs/{run["id"]}/events':
                after=int(parse_qs(url.query).get('after',['0'])[0]); data=[e for e in events if e['sequence']>after]
            elif url.path==f'/api/projects/{PROJECT}/runs/{run["id"]}':data=run
            elif url.path.endswith('/runs'):data=[run]
            elif url.path.endswith('/file'):data={'content':'<script>window.generatedExecuted = true</script>'}
            elif url.path==f'/api/projects/{PROJECT}':data=project
            elif url.path=='/api/projects':data=[project]
            else:data=[]
            route.fulfill(json=data)
        context.route('**/*',route_request)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(f'http://phase4.test/static/builder.html#project={PROJECT}&run={RUN}')
        page.get_by_text('Validation Passed — Project repaired successfully',exact=True).wait_for()
        monitor=page.locator('.run-monitor')
        monitor.get_by_role('button',name='script.js',exact=True).click()
        page.locator('.project-code').get_by_text('<script>window.generatedExecuted = true</script>',exact=True).wait_for()
        assert page.evaluate('window.diagnosticExecuted === undefined && window.generatedExecuted === undefined')
        monitor.screenshot(path=str(screenshots/'phase4-completed.png'))
        page.reload();page.get_by_text('Validation Passed — Project repaired successfully',exact=True).wait_for()
        run.update(id='f'*32,status='failed',failure_class='provider_failure',validations=[failure],validation_count=1)
        run['attempts'][0]['attempt_result']='failed'
        events[-2].update(event_type='repair_unchanged',safe_summary='repair unchanged')
        events[-1].update(event_type='run_failed',safe_summary='run failed')
        page.goto(f'http://phase4.test/static/builder.html#project={PROJECT}&run={run["id"]}')
        page.reload()
        page.get_by_text('Provider failure — The model request did not complete',exact=True).wait_for()
        monitor.screenshot(path=str(screenshots/'phase4-provider-failure.png'))
        assert page.get_by_role('button',name='Retry as New Run',exact=True).is_visible()
        page.get_by_role('button',name='Retry as New Run',exact=True).click()
        page.wait_for_function("document.querySelector('.run-facts')?.textContent.includes('Queued')")
        assert run['id'] not in {RUN,'f'*32}
        run.update(status='repairing',started_at='2026-09-07T13:01:01Z')
        page.get_by_role('button',name='Refresh Run',exact=True).click()
        page.wait_for_function("document.querySelector('.run-facts')?.textContent.includes('Repairing')")
        page.get_by_role('button',name='Cancel Run',exact=True).click()
        page.wait_for_function("document.querySelector('.run-facts')?.textContent.includes('Cancelling')")
        assert page.get_by_role('button',name='Cancel Run',exact=True).is_disabled()
        run.update(status='cancelled',failure_class='cancelled',cleanup_confirmed=True)
        page.get_by_role('button',name='Refresh Run',exact=True).click()
        page.get_by_text('Cancelled — Cleanup confirmed',exact=True).wait_for()
        page.get_by_role('textbox',name='GitHub account name').fill('example')
        page.get_by_role('button',name='Preview publication',exact=True).click()
        page.get_by_role('button',name='Confirm public publication',exact=True).wait_for()
        assert not any(path.endswith('/confirm') for method,path in requests)
        assert REVISION in page.locator('.project-publication').inner_text()
        page.locator('.project-publication').screenshot(path=str(screenshots/'phase6-publication-preview.png'))
        page.get_by_role('button',name='Confirm public publication',exact=True).click()
        page.get_by_role('link',name='https://example.github.io/managed-'+PROJECT+'/').wait_for()
        assert sum(method=='POST' and path.endswith('/confirm') for method,path in requests)==1
        assert not errors,errors
        assert sum(method=='POST' and path.endswith('/runs') for method,path in requests)==1,requests
        assert sum(method=='POST' and path.endswith('/cancel') for method,path in requests)==1,requests
        browser.close()
    print('PASS: real Edge monitor, safe diagnostics/file viewer, refresh restore, provider failure; all requests intercepted')
    print('Screenshots: docs/screenshots/phase4-completed.png and phase4-provider-failure.png (offline fixtures)')


if __name__=='__main__':main()
