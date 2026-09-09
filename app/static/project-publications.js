/* Explicit preview + confirmation. All generated/server strings remain inert text. */
(() => {
  class ProjectPublishUI {
    constructor(root, project, name) {
      this.root = root; this.project = project; this.name = name; this.disposed = false;
      root.className = 'project-publication';
      const node = (tag, text) => { const e = document.createElement(tag); e.textContent = text; root.appendChild(e); return e; };
      node('h4', 'Publish to Cloud');
      this.owner = node('input', ''); this.owner.placeholder = 'GitHub account name'; this.owner.setAttribute('aria-label', 'GitHub account name');
      this.start = node('button', 'Preview publication');
      this.recheck = node('button', 'Refresh eligibility');
      this.confirm = node('button', 'Confirm public publication'); this.confirm.hidden = true;
      this.output = node('pre', 'Only a validated current revision can be published.');
      this.output.className = 'project-code';
      this.history = node('div', '');
      this.start.addEventListener('click', () => this.preview());
      this.confirm.addEventListener('click', () => this.publish());
      this.recheck.addEventListener('click', () => this.refreshEligibility());
      this.refresh();
      this.refreshEligibility();
    }
    async api(path, body) {
      const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(`/api/projects/${this.project}${path}`, {
          method: body === undefined ? 'GET' : 'POST', signal: controller.signal,
          ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
        if (!response.ok) throw new Error('Publication request rejected. Check validation, active runs and publication configuration.');
        return await response.json();
      } finally { clearTimeout(timeout); }
    }
    async preview() {
      if (this.busy || this.disposed) return; this.busy = true; this.start.disabled = true; this.confirm.hidden = true;
      this.plan = null;
      const epoch = this.eligibilityEpoch;
      try {
        const revision = await this.api('/revision');
        const plan = await this.api('/publications/preview', {revision: revision.revision, owner: this.owner.value.trim()});
        if (this.disposed || epoch !== this.eligibilityEpoch) return;
        this.plan = plan;
        this.output.textContent = `${this.name}\nValidated revision: ${plan.revision}\nCleanup confirmed\nPublic repository: ${plan.repository}\nFiles:\n${plan.files.join('\n')}\nApplication adds .nojekyll. Website files become public.`;
        this.confirm.hidden = false;
      } catch (error) { this.output.textContent = error.message; }
      finally { this.busy = false; this.start.disabled = this.eligible !== true; }
    }
    async refreshEligibility() {
      const epoch = this.eligibilityEpoch = (this.eligibilityEpoch || 0) + 1;
      this.plan = null; this.confirm.hidden = true; this.eligible = false; this.start.disabled = true;
      try {
        const state = await this.api('/publications/eligibility');
        if (this.disposed || epoch !== this.eligibilityEpoch) return;
        this.eligible = state.eligible === true;
        this.output.textContent = this.eligible
          ? `Durable validation recorded for revision ${state.revision}. Preview will recheck the current files and publication gates.`
          : 'Publication eligibility is not established. Validate the current revision and resolve any active run before previewing.';
      } catch (_) {
        if (!this.disposed && epoch === this.eligibilityEpoch) this.output.textContent = 'Eligibility unavailable. Refresh eligibility to check durable backend state.';
      } finally {
        if (!this.disposed && epoch === this.eligibilityEpoch) this.start.disabled = this.busy || !this.eligible;
      }
    }
    async publish() {
      if (this.busy || !this.plan) return;
      this.busy = true; this.confirm.disabled = true;
      try {
        await this.api(`/publications/${this.plan.id}/confirm`, {});
        this.confirm.hidden = true; this.plan = null; this.failures = 0; await this.refresh();
      } catch (error) { this.output.textContent = error.message + ' Refresh history before creating a fresh preview.'; await this.refresh(); }
      finally { this.busy = false; this.confirm.disabled = false; }
    }
    async refresh() {
      clearTimeout(this.timer);
      try {
        const rows = await this.api('/publications'); if (this.disposed) return;
        this.failures = 0; this.history.replaceChildren();
        for (const row of rows.filter(row => row.status !== 'preview')) {
          const text = document.createElement('pre');
          text.className = 'project-code';
          text.textContent = `${row.status} — ${row.repository}\nRevision: ${row.revision}\nCommit: ${row.commit_sha || 'Pending'}\n${row.failure_class || row.safe_summary || ''}\n${row.events.map(e => e.state).join(' → ')}`;
          this.history.appendChild(text);
          if (row.status === 'completed' && /^https:\/\/[a-z0-9-]+\.github\.io\/managed-[a-f0-9]{32}\/$/.test(row.public_url || '')) {
            const link = document.createElement('a'); link.textContent = row.public_url; link.href = row.public_url;
            link.target = '_blank'; link.rel = 'noopener noreferrer'; this.history.appendChild(link);
          }
        }
        if (rows.some(row => !['preview', 'completed', 'failed'].includes(row.status))) this.timer = setTimeout(() => this.refresh(), 3000);
      } catch (_) {
        this.failures = (this.failures || 0) + 1;
        if (!this.disposed && this.failures < 5) this.timer = setTimeout(() => this.refresh(), 5000);
        else this.output.textContent = 'Publication status unavailable. Reopen this project to refresh history.';
      }
    }
    dispose() { this.disposed = true; clearTimeout(this.timer); }
  }
  globalThis.ProjectPublishUI = ProjectPublishUI;
})();
