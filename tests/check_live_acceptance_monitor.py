"""Read-only verification of the ONE Phase 5 live run. No mocks and no POSTs."""
import json
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'runtime'/'phase5'/'acceptance.json'


def main():
    state=json.loads(STATE.read_text()); project=state['project_id']; run_id=state['run_id']
    url=f'http://localhost:8000/api/projects/{project}/runs/{run_id}'
    response=requests.get(url,timeout=10);response.raise_for_status(); run=response.json()
    assert run['status'] in {'completed','failed','cancelled','interrupted'}
    screenshot=ROOT/'docs'/'screenshots'/'phase5-live-acceptance.png'
    blocked=[]; errors=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=True,args=['--disable-background-networking'])
        context=browser.new_context(viewport={'width':1440,'height':2400},service_workers='block')
        def gate(route):
            request=route.request; parsed=urlparse(request.url)
            if request.method!='GET' or parsed.hostname!='localhost' or parsed.port!=8000:
                blocked.append(request.method);return route.abort()
            route.continue_()  # Real local backend responses, never substituted fixture data.
        context.route('**/*',gate)
        page=context.new_page();page.on('pageerror',lambda error:errors.append(type(error).__name__))
        page.goto(f'http://localhost:8000/static/index.html#project={project}&run={run_id}')
        monitor=page.locator('.run-monitor');monitor.locator('.run-final').wait_for()
        content=monitor.inner_text()
        assert run_id in content and 'gpt-5.6-sol' in content and 'openai-codex' in content
        assert 'Timeline' in content and 'Validation' in content and 'Files Changed' in content
        for attempt in run['attempts']:
            for file in attempt['changed_files']:assert file in content
        if run['status']=='completed':assert 'Project repaired successfully' in content
        monitor.screenshot(path=str(screenshot))
        assert not errors
        assert not blocked,'Unexpected non-read-only request was blocked'
        page.reload();page.locator('.run-monitor .run-final').wait_for()
        browser.close()
    result={'run_id':run_id,'real_backend':True,'mocked':False,'reload_reconstructed':True,
        'model_provider_visible':True,'timeline_visible':True,'validations_visible':True,
        'changed_files_visible':True,'final_result_visible':True,'mutating_requests':0,
        'screenshot':'docs/screenshots/phase5-live-acceptance.png'}
    (ROOT/'runtime'/'phase5'/'ui-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
