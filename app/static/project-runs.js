/* Persisted state only. All source, events and diagnostics remain inert text. */
(() => {
  const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
  const labels = {queued:'Queued', claimed:'Claimed', baseline_validation:'Baseline Validation',
    repairing:'Repairing', validating:'Validating', cancelling:'Cancelling…', completed:'Completed',
    failed:'Failed', cancelled:'Cancelled', interrupted:'Interrupted'};
  const results = {repaired_successfully:'Validation Passed — Project repaired successfully',
    validation_passed:'Validation Passed — Standalone validation; no repairs performed',
    validation_failed:'Validation Failed — Standalone validation; no repairs performed',
    unresolved_validation_errors:'Validation Failed — Unresolved errors remain',
    provider_failure:'Provider failure — The model request did not complete',
    validation_infrastructure_failure:'Validator unavailable — Code validity is not confirmed',
    security_violation:'Stopped by security policy — Review required',
    cancelled:'Cancelled — Cleanup confirmed', deadline_exceeded:'Run deadline exceeded',
    no_progress:'Validation Failed — Repair stopped without progress',
    interrupted:'Interrupted — Recovery required; the previous operation will not resume'};
  const node = (tag, text, parent, className) => {
    const el = document.createElement(tag); el.textContent = text;
    if (className) el.className = className;
    parent.appendChild(el); return el;
  };
  const stamp = value => value ? new Date(value).toLocaleString() : 'Not started';
  const storage = {
    get(key) { try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch {} },
    remove(key) { try { sessionStorage.removeItem(key); } catch {} }
  };
  async function api(url, options = {}, timeout = 10000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, {...options, signal:controller.signal});
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(response.status === 409 ? 'The revision changed or this project is reserved. Refresh runs; interrupted work may require operator recovery.' : 'The request could not be completed. Check the connection and try again.');
        error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.status) throw error;
      throw new Error('Connection unavailable or invalid response. Reconnect to check persisted state.');
    } finally { clearTimeout(timer); }
  }
  const post = body => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  function newRunId() {
    if (crypto.randomUUID) return crypto.randomUUID().replaceAll('-','');
    // getRandomValues is also available on local HTTP origins without randomUUID.
    const bytes=crypto.getRandomValues(new Uint8Array(16));
    bytes[6]=(bytes[6]&15)|64; bytes[8]=(bytes[8]&63)|128;
    return [...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
  }

  class ProjectRunUI {
    constructor(root, projectId, openFile, onValidationChanged = () => {}) {
      this.root=root; this.projectId=projectId; this.openFile=openFile;
      this.onValidationChanged=onValidationChanged;
      this.base=`/api/projects/${projectId}`; this.key=`project-run-pending:${projectId}`;
      this.epoch=0; this.errors=0; this.events=new Map(); this.cursor=0; this.disposed=false;
      node('h3','Autonomous coding agent',root);
      node('p','Validate this website, repair it with the configured AI provider, and inspect the recorded results.',root);
      const actions=node('div','',root,'run-actions');
      this.validateButton=this.button('Validate',actions,() => this.validate());
      this.fixButton=this.button('Fix Project',actions,() => this.start());
      this.button('View Runs',actions,() => this.list());
      this.notice=node('p','',root,'run-notice'); this.notice.setAttribute('role','status');
      this.listRoot=node('div','',root,'run-list');
      this.monitor=node('section','',root,'run-monitor');
      this.heading=node('h4','Select or start an agent run',this.monitor);
      const controls=node('div','',this.monitor,'run-actions');
      this.cancelButton=this.button('Cancel Run',controls,() => this.cancel());
      this.retryButton=this.button('Retry as New Run',controls,() => this.start());
      this.refreshButton=this.button('Refresh Run',controls,() => this.refresh());
      this.cancelButton.hidden=true; this.retryButton.hidden=true; this.refreshButton.hidden=true;
      this.body=node('div','',this.monitor);
      this.validationRoot=node('section','',root,'run-validation');
      const params=new URLSearchParams(globalThis.location?.hash?.slice(1) || '');
      const selected=params.get('project')===projectId && params.get('run');
      if (selected && /^[a-f0-9]{32}$/.test(selected)) this.open(selected);
      else if (storage.get(this.key)) this.notice.textContent='A previous submission was not confirmed. Fix Project will check its existing run ID before submitting again.';
    }
    button(text,parent,handler) {
      const button=node('button',text,parent,'project-file-button'); button.type='button';
      button.addEventListener('click',handler); return button;
    }
    dispose() { this.disposed=true; this.epoch++; clearTimeout(this.timer); }
    async list() {
      try {
        const rows=await api(`${this.base}/runs?limit=50`);
        if (this.disposed) return;
        this.listRoot.replaceChildren();
        if (!rows.length) node('p','No agent runs yet.',this.listRoot);
        for (const run of rows) this.button(`${run.operation==='validation' ? 'Validation' : 'Repair'} · ${labels[run.status] || run.status} · ${stamp(run.created_at)} · ${run.id}`,this.listRoot,() => this.open(run.id));
      } catch (error) { if (!this.disposed) this.notice.textContent=error.message; }
    }
    async start() {
      if (this.submitting || this.disposed || (this.runId && (!this.run || !terminal.has(this.run.status)))) return;
      this.submitting=true; this.fixButton.disabled=true; this.retryButton.disabled=true;
      this.notice.textContent='Checking the current project revision…';
      try {
        let payload=storage.get(this.key);
        if (payload && /^[a-f0-9]{32}$/.test(payload.run_id || '')) {
          try {
            const existing=await api(`${this.base}/runs/${payload.run_id}`);
            storage.remove(this.key); await this.open(existing.id); return;
          } catch (error) { if (error.status!==404) throw error; }
          const revision=await api(`${this.base}/revision`);
          if (revision.revision!==payload.revision) { storage.remove(this.key); throw new Error('The project changed. Review it and choose Fix Project again.'); }
        } else {
          const revision=await api(`${this.base}/revision`);
          payload={run_id:newRunId(),revision:revision.revision};
          storage.set(this.key,payload);
        }
        if (this.disposed) return;
        const run=await api(`${this.base}/runs`,post(payload));
        storage.remove(this.key); this.notice.textContent='Run accepted.';
        await this.open(run.id);
      } catch (error) {
        if (error.status===409 || error.status===422) storage.remove(this.key);
        if (!this.disposed) this.notice.textContent=`${error.message} Use View Runs to check persisted work.`;
      } finally {
        this.submitting=false;
        this.fixButton.disabled=Boolean(this.runId && (!this.run || !terminal.has(this.run.status))); this.retryButton.disabled=false;
      }
    }
    async open(runId) {
      if (this.disposed || !/^[a-f0-9]{32}$/.test(runId)) return;
      clearTimeout(this.timer); this.epoch++; this.runId=runId; this.run=null;
      this.events.clear(); this.cursor=0; this.errors=0; this.cancelSent=false;
      this.heading.textContent=`Agent Run ${runId}`; this.body.replaceChildren();
      this.monitor.scrollIntoView?.({block:'start'});
      this.refreshButton.hidden=false; this.fixButton.disabled=true;
      this.cancelButton.hidden=true; this.retryButton.hidden=true;
      globalThis.history?.replaceState(null,'',`#project=${this.projectId}&run=${runId}`);
      await this.poll(this.epoch);
    }
    async refresh() {
      if (this.busy || !this.runId) return;
      clearTimeout(this.timer); this.errors=0; await this.poll(this.epoch);
    }
    async poll(epoch) {
      if (this.disposed || epoch!==this.epoch) return;
      this.busy=true;
      try {
        const run=await api(`${this.base}/runs/${this.runId}`);
        const events=await api(`${this.base}/runs/${this.runId}/events?after=${this.cursor}&limit=100`);
        if (this.disposed || epoch!==this.epoch) return;
        this.run=run; this.errors=0;
        for (const event of events) {
          this.events.set(event.sequence,event); this.cursor=Math.max(this.cursor,event.sequence);
        }
        while (this.events.size>200) this.events.delete(Math.min(...this.events.keys()));
        this.notice.textContent=run.cancellation_requested && !terminal.has(run.status) ? 'Cancellation requested. Waiting for backend cleanup confirmation…' : '';
        this.render();
        if (terminal.has(run.status)) await this.onValidationChanged();
        if (!terminal.has(run.status) || this.cursor<run.event_sequence) this.schedule(epoch,2000);
      } catch {
        if (this.disposed || epoch!==this.epoch) return;
        this.errors++;
        this.notice.textContent=this.errors>=5 ? 'Monitoring paused after connection failures. Refresh Run to reconnect; the backend run may still be active.' : 'Connection interrupted. Retrying the persisted run status…';
        if (this.errors<5) this.schedule(epoch,Math.min(15000,2000*2**this.errors));
      } finally { if (epoch===this.epoch) this.busy=false; }
    }
    schedule(epoch,delay) { clearTimeout(this.timer); this.timer=setTimeout(() => this.poll(epoch),delay); }
    async cancel() {
      if (this.cancelSent || !this.run || terminal.has(this.run.status)) return;
      this.cancelSent=true; this.cancelButton.disabled=true;
      const epoch=this.epoch;
      this.notice.textContent='Requesting cancellation…';
      try {
        const update=await api(`${this.base}/runs/${this.runId}/cancel`,post({}));
        if (this.disposed || epoch!==this.epoch) return;
        this.run={...this.run,...update}; this.render();
        this.notice.textContent=update.status==='cancelled' ? 'Backend confirmed cancellation and cleanup.' : 'Cancelling… Waiting for backend cleanup confirmation.';
        await this.refresh();
      } catch {
        if (this.disposed || epoch!==this.epoch) return;
        this.notice.textContent='Cancellation response was not confirmed. Refresh Run to check persisted state.';
        this.cancelSent=false; await this.refresh();
      }
    }
    renderValidation(values,parent) {
      for (const value of values) {
        node('h5',`${value.profile || 'Static validation'} · ${value.status}`,parent);
        if (value.revision) node('p',`Revision: ${value.revision}`,parent);
        if (value.passed && value.status==='passed' && value.cleanup_confirmed===true) node('p','Validation Passed',parent,'run-success');
        if (value.cleanup_confirmed===false) node('p','Cleanup is unconfirmed; this result does not establish publication eligibility.',parent);
        for (const error of (value.errors || []).slice(0,30)) {
          const item=node('div','',parent,'run-error');
          node('strong',error.file || 'Project',item);
          node('p',`Line ${error.line}, Column ${error.column} · ${error.check} · ${error.code}`,item);
          node('p',error.message || 'Static validation check failed.',item);
        }
        if (!value.passed && !value.errors?.length) node('p','Validation did not pass. No structured code errors are available.',parent);
      }
    }
    render() {
      const run=this.run; this.body.replaceChildren();
      const active=!terminal.has(run.status); const attempt=run.attempts?.at(-1);
      this.fixButton.disabled=active || this.submitting; this.validateButton.disabled=active;
      this.cancelButton.hidden=!active; this.cancelButton.disabled=this.cancelSent || run.cancellation_requested;
      this.retryButton.hidden=run.operation==='validation' || !['failed','interrupted'].includes(run.status);
      this.monitor.className=`run-monitor ${run.failure_class==='provider_failure' ? 'run-provider-failure' : run.status==='completed' ? 'run-completed' : ''}`;
      node('p',`Task: ${run.task || 'Repair managed static website'}`,this.body);
      const details=node('dl','',this.body,'run-facts');
      const end=run.finished_at ? Date.parse(run.finished_at) : Date.now();
      const elapsed=run.started_at ? `${Math.max(0,Math.floor((end-Date.parse(run.started_at))/1000))} seconds` : 'Not started';
      for (const [label,value] of Object.entries({'Run ID':run.id,Status:labels[run.status] || run.status,
        Stage:labels[run.status] || run.status,Model:attempt?.model || 'No model attempt recorded',
        Provider:attempt?.provider || 'No provider attempt recorded',Started:stamp(run.started_at),Elapsed:elapsed,
        Repairs:`${run.repair_count} / ${run.max_repairs}`,Validations:`${run.validation_count} / ${run.operation==='validation' ? 1 : 4}`})) {
        node('dt',label,details); node('dd',String(value),details);
      }
      if (terminal.has(run.status)) {
        node('h4','Final Result',this.body);
        node('p',results[run.failure_class] || labels[run.status],this.body,'run-final');
        if (!run.cleanup_confirmed) node('p',run.operation==='validation'
          ? 'Validator cleanup was not confirmed. This validation does not establish publication eligibility.'
          : 'Cleanup is unconfirmed. Operator recovery may be required before a new run.',this.body);
      }
      node('h4','Timeline — persisted events',this.body);
      const timeline=node('ol','',this.body,'run-timeline');
      for (const event of [...this.events.values()].sort((a,b) => a.sequence-b.sequence)) {
        const li=node('li','',timeline);
        node('time',stamp(event.created_at),li); node('span',` ${event.safe_summary} (${event.event_type})`,li);
      }
      node('h4','Validation',this.body); this.renderValidation(run.validations || [],this.body);
      node('h4','Files Changed',this.body);
      for (const item of run.attempts || []) {
        node('h5',`Repair ${item.repair_cycle} · ${item.attempt_result}`,this.body);
        const paths=item.changed_files || [];
        if (!paths.length) node('p','No changed files recorded for this attempt.',this.body);
        for (const path of paths.slice(0,100)) this.button(path,this.body,() => this.openFile(path));
      }
    }
    async validate() {
      if (this.validating || this.disposed) return;
      this.validating=true; this.validateButton.disabled=true;
      this.validationRoot.replaceChildren(); node('p','Requesting static validation…',this.validationRoot);
      try {
        const {revision}=await api(`${this.base}/revision`);
        const result=await api(`${this.base}/validate`,post({revision}),90000);
        if (this.disposed) return;
        this.validationRoot.replaceChildren(); node('h4','Standalone validation',this.validationRoot);
        this.renderValidation([result],this.validationRoot);
        if (result.run_id) this.button('View validation run',this.validationRoot,() => this.open(result.run_id));
        await this.list();
        await this.onValidationChanged();
      } catch (error) { if (!this.disposed) this.validationRoot.textContent=error.message; }
      finally { this.validating=false; this.validateButton.disabled=Boolean(this.run && !terminal.has(this.run.status)); }
    }
  }
  globalThis.ProjectRunUI=ProjectRunUI;
})();
