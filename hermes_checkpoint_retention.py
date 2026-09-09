"""Prune only extra checkpoints in one validated, completed repair directory."""
import shutil
from pathlib import Path
from app.project_workspace import validate_id, reject_link


def retain(run_id,result):
    from hermes_repair import RUNTIME
    validate_id(run_id)
    root=RUNTIME/run_id
    reject_link(RUNTIME.parent); reject_link(RUNTIME); reject_link(root)
    if not root.exists(): return
    if root.resolve().parent != RUNTIME.resolve(): raise ValueError('Unsafe retention root')
    checkpoints=result.get('checkpoints',[])
    if not checkpoints: return
    keep={0,checkpoints[-1]['checkpoint']}
    validated={v['revision'] for v in result.get('validations',[]) if v.get('cleanup_confirmed')
               and v.get('status') in {'passed','failed'}}
    matches=[c['checkpoint'] for c in checkpoints if c['revision'] in validated]
    if matches: keep.add(max(matches))
    for entry in list(root.iterdir()):
        reject_link(entry)
        if not entry.name.isdigit() or int(entry.name) in keep: continue
        if entry.resolve().parent != root.resolve(): raise ValueError('Unsafe checkpoint')
        for child in entry.rglob('*'): reject_link(child)
        shutil.rmtree(entry)
        metadata=root/(entry.name+'.json')
        reject_link(metadata)
        metadata.unlink(missing_ok=True)
