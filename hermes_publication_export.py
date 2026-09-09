"""Host-side snapshot handoff using the existing exclusive repair lock.

No Hermes invocation and no GitHub credentials in this process.
"""
from app import project_workspace as ws
from app.publication_export import validate_artifact


def export_project(project, revision):
    from hermes_repair import RUNTIME
    ws.validate_id(project)
    for path in (RUNTIME.parent, RUNTIME):
        ws.reject_link(path)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = RUNTIME / (project + '.lock')
    ws.reject_link(lock)
    with lock.open('x') as stream:
        stream.write('publication-export')
    try:
        current, files = ws.capture_revision(project)
        if current != revision:
            raise ValueError('invalid_revision')
        artifact = validate_artifact(project, revision, files)
        if ws.capture_revision(project)[0] != revision:
            raise ValueError('invalid_revision')
        return artifact
    finally:
        lock.unlink()
