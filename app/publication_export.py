"""Trusted, bounded static artifact export. No code execution."""
import hashlib
import re
from pathlib import PurePosixPath
from app import project_workspace as ws

ALLOWLIST = {'.html', '.css', '.js', '.json', '.svg', '.txt'}
SECRET = re.compile(r'-----BEGIN .*PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})|(?:api[_-]?key|password|secret|access[_-]?token)\s*["\']?\s*[:=]\s*["\'][^"\']{8,}', re.I)


def validate_artifact(project, revision, files):
    if not isinstance(files, dict) or not 0 < len(files) <= ws.MAX_FILES or 'index.html' not in files:
        raise ValueError('security_violation')
    digest = hashlib.sha256(); total = 0
    for name, content in sorted(files.items()):
        ws.file_path(project, name)  # reuse traversal, sensitive-name and link boundary
        if (PurePosixPath(name).suffix.lower() not in ALLOWLIST or
                PurePosixPath(name).name.lower() in {'package.json', 'package-lock.json', 'composer.json'} or
                any(part.lower() in {'runtime', 'checkpoints', 'logs', 'node_modules', 'hermes'} for part in name.split('/'))):
            raise ValueError('security_violation')
        if not isinstance(content, str) or '\0' in content or SECRET.search(content):
            raise ValueError('security_violation')
        data = content.encode('utf-8'); total += len(data)
        if len(data) > ws.MAX_FILE_BYTES or total > ws.MAX_PROJECT_BYTES:
            raise ValueError('security_violation')
        digest.update(name.encode() + b'\0')
        digest.update(hashlib.sha256(data).hexdigest().encode() + b'\n')
    if digest.hexdigest() != revision:
        raise ValueError('invalid_revision')
    return dict(files)

