/* Project content is always rendered as text, never as executable HTML. */
const projectStatus = document.getElementById('projectStatus');
const projectHistory = document.getElementById('projectHistory');
const projectResult = document.getElementById('projectResult');
let buildingProject = false;
let projectRunUI = null;

async function projectApi(url, options) {
  const response = await fetch(url, options);
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.toLowerCase().includes('application/json')) {
    throw new Error(`Project API returned ${contentType || 'an unknown content type'} instead of JSON (HTTP ${response.status}). Open this workspace through the FastAPI UI, not a static file server.`);
  }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Project request failed.');
  return data;
}

function projectElement(tag, text, parent) {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.appendChild(element);
  return element;
}

async function loadProjects() {
  try {
    const projects = await projectApi('/api/projects');
    projectHistory.replaceChildren();
    if (!projects.length) projectElement('p', 'No builds yet. Your generated projects will be saved here.', projectHistory).className = 'history-empty';
    for (const project of projects) {
      const button = projectElement('button', `${project.project_name} — ${project.status}`, projectHistory);
      button.className = 'project-history-item';
      button.addEventListener('click', async () => {
        try { showProject(await projectApi(`/api/projects/${project.project_id}`)); }
        catch (error) { projectStatus.textContent = error.message; }
      });
    }
  } catch (error) {
    projectHistory.replaceChildren();
    projectElement('p', error.message, projectHistory).className = 'history-empty history-error';
  }
}

function showProject(project) {
  projectRunUI?.dispose();
  projectResult.replaceChildren();
  projectElement('h3', project.project_name, projectResult);
  projectElement('p', `Status: ${project.status}`, projectResult);
  projectElement('p', `Model: ${modelDisplayNames[project.model] || project.model || 'Unavailable'}`, projectResult);
  projectElement('p', `Provider: ${providerDisplayNames[project.provider] || project.provider || 'Unavailable'}`, projectResult);
  projectElement('p', `Fallback used: ${project.fallback_used ? 'Yes' : 'No'}`, projectResult);
  projectElement('p', project.message || '', projectResult);
  if (project.preview_pending && /^[a-f0-9]{32}$/.test(project.project_id || '')) {
    const actions = projectElement('div', '', projectResult);
    actions.className = 'project-actions';
    const preview = projectElement('a', 'Open local preview', actions);
    preview.className = 'preview-link';
    preview.href = `http://127.0.0.1:8050/${project.project_id}/index.html`;
    preview.target = '_blank';
    preview.rel = 'noopener noreferrer';
  }
  projectElement('h4', 'Generated files', projectResult);
  const files = projectElement('div', '', projectResult);
  files.className = 'project-file-list';
  const viewerTitle = projectElement('h4', 'Select a file to view its contents', projectResult);
  const viewer = projectElement('pre', '', projectResult);
  viewer.className = 'project-code';
  const openFile = async path => {
    viewerTitle.textContent = path;
    viewer.textContent = 'Loading file...';
    try {
      const data = await projectApi(`/api/projects/${project.project_id}/file?path=${encodeURIComponent(path)}`);
      if (viewerTitle.textContent === path) viewer.textContent = data.content;
    } catch (error) { viewer.textContent = error.message; }
  };
  for (const path of project.files || []) {
    const button = projectElement('button', path, files);
    button.className = 'project-file-button';
    button.addEventListener('click', () => openFile(path));
  }
  if (!project.files?.length) projectElement('p', 'No generated files detected.', files);
  if (project.preview_pending) projectElement('p', 'Preview runs on a separate local origin; generated files remain isolated from this application.', projectResult);
  if (globalThis.ProjectRunUI) {
    const runRoot = projectElement('div', '', projectResult);
    projectRunUI = new globalThis.ProjectRunUI(runRoot, project.project_id, openFile);
  }
}

document.getElementById('projectForm').addEventListener('submit', async event => {
  event.preventDefault();
  const prompt = document.getElementById('projectPrompt').value.trim();
  if (!prompt || buildingProject) return;
  buildingProject = true;
  const button = document.getElementById('buildProject');
  button.disabled = true;
  projectStatus.textContent = 'Building project files with AI. This may take a few minutes...';
  // Stages describe the pending operation; no timer claims unobserved agent progress.
  try {
    const project = await projectApi('/api/projects', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt})
    });
    projectStatus.textContent = 'Checking generated files...';
    const verified = await projectApi(`/api/projects/${project.project_id}`);
    showProject(verified);
    projectStatus.textContent = verified.message;
    if (verified.success) updateModelStatus(verified);
    await loadProjects();
  } catch (error) {
    projectStatus.textContent = `${error.message} Check project history before retrying if the connection was interrupted.`;
    await loadProjects();
  } finally {
    buildingProject = false;
    button.disabled = false;
  }
});

document.getElementById('refreshProjects').addEventListener('click', loadProjects);
loadProjects();

// Reload reconstructs the selected monitor from the persisted run, not local stages.
if (globalThis.location?.hash) {
  const params = new URLSearchParams(globalThis.location.hash.slice(1));
  const selected = params.get('project');
  if (/^[a-f0-9]{32}$/.test(selected || '')) {
    loadProjects();
    projectApi(`/api/projects/${selected}`).then(showProject).catch(() => {
      projectStatus.textContent = 'The saved project could not be loaded. Reopen it from project history.';
    });
  }
}
