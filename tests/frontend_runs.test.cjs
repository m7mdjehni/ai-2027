// Offline DOM/fetch/timer harness. No real service or model request is possible.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.textContent=''; this.handlers={}; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children=[]; this.textContent=''; }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(name,handler) { this.handlers[name]=handler; }
  set innerHTML(_) { throw Error('Unsafe HTML insertion'); }
}
const text=el=>[el.textContent,...el.children.map(text)].join('\n');
const all=el=>[el,...el.children.flatMap(all)];
const flush=()=>new Promise(setImmediate);
function harness(options={}) {
  const timers=new Map(), calls=[], opened=[], saved=options.saved || new Map();
  let tid=0, uid=10;
  const project='a'.repeat(32), id='b'.repeat(32), revision='c'.repeat(64);
  const state={run:{id,project_id:project,status:'repairing',task:'Repair managed static website',repair_count:1,
    max_repairs:3,validation_count:1,cancellation_requested:false,cleanup_confirmed:false,
    started_at:'2026-09-07T00:00:00Z',created_at:'2026-09-07T00:00:00Z',event_sequence:2,
    attempts:[{repair_cycle:1,model:'gpt-5.6-sol',provider:'openai-codex',attempt_result:'completed',changed_files:['script.js']}],
    validations:[{profile:'static-web-v1',status:'failed',passed:false,revision,
      errors:[{file:'script.js',line:18,column:4,check:'javascript-syntax',code:'syntax_error',message:'<script>steal()</script>'}]}]},
    events:[{sequence:2,created_at:'2026-09-07T00:00:02Z',event_type:'baseline_failed',safe_summary:'baseline failed'},
      {sequence:1,created_at:'2026-09-07T00:00:01Z',event_type:'run_created',safe_summary:'run created'}],
    fail:0,loseSubmission:false,exists:true,revision,posts:[]};
  const location={hash:options.hash || ''};
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},
    location,history:{replaceState(_a,_b,hash){location.hash=hash;}},
    sessionStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},
    crypto:{randomUUID:()=>String(++uid).padStart(32,'0')},URLSearchParams,AbortController,Date,
    setTimeout:(fn,delay)=>{timers.set(++tid,{fn,delay});return tid;},clearTimeout:id=>timers.delete(id),
    fetch:async (url,request={})=>{
      calls.push({url,request});
      if (state.fail) {state.fail--;throw Error('sensitive raw failure');}
      let data;
      if (url.endsWith('/revision')) data={revision:state.revision};
      else if (url.endsWith('/validate')) data=state.run.validations[0];
      else if (request.method==='POST' && url.endsWith('/runs')) {
        const payload=JSON.parse(request.body);state.posts.push(payload);state.exists=true;
        state.run={...state.run,id:payload.run_id,status:'queued',cancellation_requested:false,repair_count:0};
        if (state.loseSubmission) {state.loseSubmission=false;throw Error('Lost response');}
        data=state.run;
      } else if (url.endsWith('/cancel')) {
        state.run={...state.run,status:'cancelling',cancellation_requested:true}; data=state.run;
      } else if (url.includes('/events?')) {
        const after=Number(new URL(url,'http://test').searchParams.get('after'));
        data=state.events.filter(e=>e.sequence>after);
      } else if (url.endsWith('/runs?limit=50')) data=[state.run];
      else {
        if (!state.exists) return {ok:false,status:404,json:async()=>({})};
        data=state.run;
      }
      return {ok:true,json:async()=>JSON.parse(JSON.stringify(data))};
    }
  });
  vm.runInContext(fs.readFileSync('app/static/project-runs.js','utf8'),context);
  state.eligibilityRefreshes=0;
  const root=new Element(); const ui=new context.ProjectRunUI(root,project,path=>opened.push(path),async()=>{state.eligibilityRefreshes++;});
  async function tick() {
    const entry=[...timers.entries()][0];assert.ok(entry,'Expected scheduled poll');
    timers.delete(entry[0]); await entry[1].fn(); await flush();
  }
  return {state,ui,root,timers,calls,opened,saved,location,project,id,tick};
}
let passed=0;
async function test(name,body) { await body();passed++;console.log('PASS: '+name); }
(async()=>{
  await test('start returns run ID; duplicate clicks submit once',async()=>{
    const h=harness();await Promise.all([h.ui.start(),h.ui.start()]);
    assert.equal(h.state.posts.length,1);assert.equal(h.ui.runId,h.state.posts[0].run_id);
    assert.equal(h.ui.fixButton.disabled,true);assert.ok(h.location.hash.includes(h.ui.runId));h.ui.dispose();
  });
  await test('persisted events ordered and diagnostic scripts inert',async()=>{
    const h=harness();await h.ui.open(h.id);
    const content=text(h.root);assert.ok(content.indexOf('run created')<content.indexOf('baseline failed'));
    assert.ok(content.includes('<script>steal()</script>'));assert.ok(content.includes('Line 18, Column 4'));
    assert.ok(content.includes('gpt-5.6-sol'));assert.ok(content.includes('openai-codex'));
    h.ui.dispose();
  });
  await test('changed files use existing viewer callback',async()=>{
    const h=harness();await h.ui.open(h.id);
    const button=all(h.root).find(e=>e.tag==='button'&&e.textContent==='script.js');
    await button.handlers.click();assert.deepEqual(h.opened,['script.js']);h.ui.dispose();
  });
  await test('cancellation request stays cancelling until persisted confirmation',async()=>{
    const h=harness();await h.ui.open(h.id);await Promise.all([h.ui.cancel(),h.ui.cancel()]);
    assert.equal(h.calls.filter(c=>c.url.endsWith('/cancel')).length,1);
    assert.equal(h.ui.run.status,'cancelling');assert.equal(h.ui.cancelButton.disabled,true);
    assert.ok(!text(h.root).includes('Cancelled — Cleanup confirmed'));
    h.state.run={...h.state.run,status:'cancelled',failure_class:'cancelled',cleanup_confirmed:true};
    await h.tick();assert.ok(text(h.root).includes('Cancelled — Cleanup confirmed'));assert.equal(h.timers.size,0);
  });
  await test('failed run retry creates fresh identity and checks current revision',async()=>{
    const h=harness();h.state.run.status='failed';h.state.run.failure_class='unresolved_validation_errors';
    await h.ui.open(h.id);assert.equal(h.ui.retryButton.hidden,false);
    h.state.revision='d'.repeat(64);await h.ui.retryButton.handlers.click();
    assert.notEqual(h.state.posts[0].run_id,h.id);assert.equal(h.state.posts[0].revision,'d'.repeat(64));h.ui.dispose();
  });
  for (const [status,failure,expected] of [
    ['interrupted','interrupted','Recovery required'],['failed','provider_failure','Provider failure'],
    ['failed','unresolved_validation_errors','Unresolved errors remain'],
    ['completed','repaired_successfully','Project repaired successfully']]) {
    await test(`${status}/${failure} is distinct and polling stops`,async()=>{
      const h=harness();h.state.run={...h.state.run,status,failure_class:failure};
      await h.ui.open(h.id);assert.ok(text(h.root).includes(expected));assert.equal(h.timers.size,0);
      if (failure==='provider_failure') assert.ok(h.ui.monitor.className.includes('run-provider-failure'));
    });
  }
  await test('refresh reconstructs monitor from persisted data',async()=>{
    const h=harness({hash:'#project='+'a'.repeat(32)+'&run='+'b'.repeat(32)});await flush();
    assert.equal(h.ui.runId,h.id);assert.equal(h.ui.run.status,'repairing');assert.ok(text(h.root).includes('run created'));h.ui.dispose();
  });
  await test('temporary polling errors recover without fabricated stages',async()=>{
    const h=harness();await h.ui.open(h.id);h.state.fail=1;await h.tick();
    assert.equal(h.ui.run.status,'repairing');assert.ok(h.ui.notice.textContent.includes('Retrying'));
    assert.ok(!text(h.root).includes('sensitive raw failure'));await h.tick();assert.equal(h.ui.errors,0);h.ui.dispose();
  });
  await test('five failures pause; explicit refresh reconnects',async()=>{
    const h=harness();await h.ui.open(h.id);h.state.fail=5;
    for(let i=0;i<5;i++)await h.tick();assert.equal(h.timers.size,0);
    assert.ok(h.ui.notice.textContent.includes('paused'));await h.ui.refresh();assert.equal(h.ui.errors,0);h.ui.dispose();
  });
  await test('lost submission response reconciles same ID without another POST',async()=>{
    const h=harness();h.state.loseSubmission=true;await h.ui.start();
    assert.equal(h.state.posts.length,1);assert.equal(h.saved.size,1);
    await h.ui.start();assert.equal(h.state.posts.length,1);assert.equal(h.ui.runId,h.state.posts[0].run_id);h.ui.dispose();
  });
  await test('standalone Validate uses current revision and safe rendering',async()=>{
    const h=harness();await h.ui.validate();
    assert.ok(h.calls.some(c=>c.url.endsWith('/validate')));assert.equal(h.state.posts.length,0);
    assert.equal(h.state.eligibilityRefreshes,1);
    assert.ok(h.calls.some(c=>c.url.endsWith('/runs?limit=50')));
    assert.ok(text(h.ui.validationRoot).includes('<script>steal()</script>'));h.ui.dispose();
  });
  await test('standalone validation history never presents a repair retry',async()=>{
    const h=harness();
    h.state.run={...h.state.run,operation:'validation',task:'Validate managed static website',
      status:'failed',failure_class:'validation_failed',max_repairs:0,repair_count:0,validation_count:1,attempts:[]};
    await h.ui.open(h.id); await h.ui.list();
    assert.ok(text(h.root).includes('Standalone validation; no repairs performed'));
    assert.equal(h.ui.retryButton.hidden,true);
    assert.ok(h.ui.listRoot.children[0].textContent.startsWith('Validation'));
    assert.ok(text(h.root).includes('1 / 1'));
    h.ui.dispose();
  });
  await test('saved standalone pass refreshes durable eligibility and exposes its run',async()=>{
    const h=harness();
    h.state.run.validations=[{run_id:h.id,profile:'static-web-v1',passed:true,status:'passed',
                             cleanup_confirmed:true,revision:h.state.revision,errors:[]}];
    await h.ui.validate();
    assert.ok(text(h.ui.validationRoot).includes('Validation Passed'));
    assert.ok(all(h.ui.validationRoot).some(e=>e.textContent==='View validation run'));
    assert.equal(h.state.eligibilityRefreshes,1);
    assert.equal(h.calls.filter(c=>c.url.endsWith('/validate')).length,1);
    assert.equal(h.state.posts.length,0);
    h.ui.dispose();
  });
  await test('View Runs loads persisted history and selection',async()=>{
    const h=harness();await h.ui.list();assert.equal(h.ui.listRoot.children.length,1);
    await h.ui.listRoot.children[0].handlers.click();assert.equal(h.ui.runId,h.id);h.ui.dispose();assert.equal(h.timers.size,0);
  });
  console.log(`${passed} run-monitor scenarios passed; all requests mocked.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
