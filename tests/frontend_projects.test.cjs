// Minimal DOM harness checks safe rendering and the submit/file-viewer flows.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.children = []; this.textContent = ''; this.handlers = {}; this.value = ''; }
  appendChild(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  set innerHTML(_) { throw new Error('Generated content must never use innerHTML'); }
}
const ids = Object.fromEntries(['projectStatus', 'projectHistory', 'projectResult', 'projectForm', 'projectPrompt', 'buildProject', 'refreshProjects'].map(id => [id, new Element()]));
const project = {project_id: 'a'.repeat(32), project_name: '<script>bad()</script>', status: 'completed', success: true,
  model: 'mimo-v2.5-free', provider: 'opencode-free', fallback_used: false,
  files: ['index.html'], preview_pending: true, message: 'Project created successfully'};
const calls = [];
const runUIs = [], publishUIs = [];
const projectNavigation = new Element();
let htmlResponse = false;
const context = vm.createContext({
  ProjectRunUI: class {
    constructor(root, project, open, changed) { this.changed=changed; runUIs.push(this); }
    dispose() {}
  },
  ProjectPublishUI: class {
    constructor(root, project) { this.project=project; this.refreshes=0; publishUIs.push(this); }
    async refreshEligibility() { this.refreshes++; }
    dispose() {}
  },
  document: {getElementById: id => ids[id], createElement: () => new Element(), querySelector: () => projectNavigation},
  modelDisplayNames: {'mimo-v2.5-free': 'MiMo V2.5'}, providerDisplayNames: {'opencode-free': 'OpenCode'},
  updateModelStatus: () => {}, encodeURIComponent,
  fetch: async (url, options) => {
    calls.push({url, options});
    const data = url.includes('/file?') ? {content: '<script>stealCredentials()</script>'}
      : url === '/api/projects' && !options ? [project] : project;
    return {ok: true, status: 200, headers: {get: () => htmlResponse ? 'text/html;charset=utf-8' : 'application/json'}, json: async () => data};
  }
});
vm.runInContext(fs.readFileSync('app/static/projects.js', 'utf8'), context);
(async () => {
  ids.projectPrompt.value = 'Build a coffee shop';
  await ids.projectForm.handlers.submit({preventDefault() {}});
  assert.equal(ids.buildProject.disabled, false);
  assert.equal(ids.projectStatus.textContent, project.message);
  assert.equal(ids.projectResult.children[0].textContent, project.project_name);
  const preview = ids.projectResult.children.flatMap(element => [element, ...element.children]).find(element => element.href);
  assert.equal(preview.href, `http://127.0.0.1:8050/${project.project_id}/index.html`);
  assert.equal(ids.projectHistory.children.length, 1);
  const fileList = ids.projectResult.children.find(element => element.className === 'project-file-list');
  await fileList.children[0].handlers.click();
  const viewer = ids.projectResult.children.find(element => element.className === 'project-code');
  assert.equal(viewer.textContent, '<script>stealCredentials()</script>');
  assert.equal(calls.filter(call => call.options?.method === 'POST').length, 1);
  assert.ok(calls.some(call => call.url.endsWith('/file?path=index.html')));
  assert.equal(publishUIs.length, 0);
  htmlResponse = true;
  await ids.refreshProjects.handlers.click();
  assert.match(ids.projectHistory.children[0].textContent, /text\/html.*instead of JSON.*FastAPI UI/s);
  console.log('PASS: project submit, history, metadata, file click, and inert HTML rendering');
})().catch(error => { console.error(error); process.exitCode = 1; });
