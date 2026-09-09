const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(){this.children=[];this.textContent='';this.handlers={};}
  appendChild(e){this.children.push(e);return e;}
  replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}
  addEventListener(k,v){this.handlers[k]=v;}
  set innerHTML(_){throw Error('Unsafe HTML');}
}
(async () => {
  const calls=[], timers=new Map(); let n=0, rows=[], eligible=false;
  const project='a'.repeat(32), revision='b'.repeat(64);
  const context=vm.createContext({document:{createElement:()=>new Element()},AbortController,
    setTimeout:(fn)=>{timers.set(++n,fn);return n;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,request)=>{
      calls.push([url,request]);
      const data=url.endsWith('/eligibility')?{revision,eligible}:url.endsWith('/revision')?{revision}:url.endsWith('/preview')?
        {id:'c'.repeat(32),revision,repository:'example/managed-'+project,files:['index.html','<script>bad</script>']}:
        url.endsWith('/confirm')?{}:rows;
      return {ok:true,json:async()=>data};
    }});
  vm.runInContext(fs.readFileSync('app/static/project-publications.js','utf8'),context);
  const ui=new context.ProjectPublishUI(new Element(),project,'Fixture');
  await new Promise(setImmediate);
  assert.equal(ui.start.disabled,true);
  eligible=true; await ui.refreshEligibility();
  assert.equal(ui.start.disabled,false);
  assert.equal(calls.filter(c=>c[1].method==='POST').length,0);
  ui.owner.value='example'; await ui.preview();
  assert.equal(calls.filter(c=>c[0].endsWith('/confirm')).length,0);
  assert.ok(ui.output.textContent.includes(revision));assert.ok(ui.output.textContent.includes('<script>bad</script>'));
  eligible=false; await ui.refreshEligibility();
  assert.equal(ui.plan,null); assert.equal(ui.confirm.hidden,true); assert.equal(ui.start.disabled,true);
  assert.equal(calls.filter(c=>c[0].endsWith('/confirm')).length,0);
  eligible=true; await ui.refreshEligibility(); await ui.preview();
  await Promise.all([ui.publish(),ui.publish()]);
  assert.equal(calls.filter(c=>c[0].endsWith('/confirm')).length,1);
  rows=[{status:'completed',repository:'example/test',revision,commit_sha:'d'.repeat(40),events:[],public_url:'javascript:bad()'}];
  await ui.refresh(); assert.equal(ui.history.children.length,1);
  rows[0].public_url='https://example.github.io/managed-'+project+'/';
  await ui.refresh(); assert.equal(ui.history.children.length,2);
  rows[0].status='deploying'; await ui.refresh(); assert.equal(timers.size,1);
  ui.dispose();assert.equal(timers.size,0);
  console.log('Publication UI: explicit confirmation, duplicate guard, inert text, URL safety, polling/disposal passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
