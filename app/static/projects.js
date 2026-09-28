/* Project content is always rendered as text, never as executable HTML. */
const projectStatus = document.getElementById('projectStatus');
const projectHistory = document.getElementById('projectHistory');
const projectResult = document.getElementById('projectResult');
let buildingProject = false;
let projectRunUI = null;
let projectPublishUI = null;

async function projectApi(url, options) {
  const response = await fetch(url, options);
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
    if (!projects.length) projectElement('p', 'No projects yet. Describe your first project above.', projectHistory);
    for (const project of projects) {
      const button = projectElement('button', `${project.project_name} — ${project.status}`, projectHistory);
      button.className = 'project-history-item';
      button.addEventListener('click', async () => {
        try { showProject(await projectApi(`/api/projects/${project.project_id}`)); }
        catch (error) { projectStatus.textContent = error.message; }
      });
    }
  } catch (error) { projectHistory.textContent = error.message; }
}

function showProject(project) {
  projectRunUI?.dispose();
  projectPublishUI?.dispose();
  projectResult.replaceChildren();
  projectElement('h3', project.project_name, projectResult);
  projectElement('p', `Status: ${project.status}`, projectResult);
  projectElement('p', `Model: ${modelDisplayNames[project.model] || project.model || 'Unavailable'}`, projectResult);
  projectElement('p', `Provider: ${providerDisplayNames[project.provider] || project.provider || 'Unavailable'}`, projectResult);
  projectElement('p', `Fallback used: ${project.fallback_used ? 'Yes' : 'No'}`, projectResult);
  projectElement('p', project.message || '', projectResult);
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
  if (project.preview_pending) projectElement('p', 'Files open as text. Generated websites are not executed or previewed here.', projectResult);
  if (globalThis.ProjectRunUI) {
    const runRoot = projectElement('div', '', projectResult);
    projectRunUI = new globalThis.ProjectRunUI(runRoot, project.project_id, openFile, () => {
      if (projectPublishUI?.project === project.project_id) return projectPublishUI.refreshEligibility();
    });
  }
  if (globalThis.ProjectPublishUI) {
    projectPublishUI = new globalThis.ProjectPublishUI(projectElement('div', '', projectResult), project.project_id, project.project_name);
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

document.querySelector('[data-tab="projects"]').addEventListener('click', loadProjects);

// Reload reconstructs the selected monitor from the persisted run, not local stages.
if (globalThis.location?.hash) {
  const params = new URLSearchParams(globalThis.location.hash.slice(1));
  const selected = params.get('project');
  if (/^[a-f0-9]{32}$/.test(selected || '')) {
    document.querySelector('[data-tab="projects"]').click();
    projectApi(`/api/projects/${selected}`).then(showProject).catch(() => {
      projectStatus.textContent = 'The saved project could not be loaded. Reopen it from project history.';
    });
  }
}
