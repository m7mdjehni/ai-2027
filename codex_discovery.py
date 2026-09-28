"""Locate native Codex without executing shell shims or reading login files."""
import os
from pathlib import Path
import re
import shutil


def _native(path):
    candidate = Path(path).expanduser()
    return (candidate.is_absolute() and candidate.is_file()
            and (candidate.suffix.lower() == '.exe' if os.name == 'nt'
                 else os.access(candidate, os.X_OK)))


def _candidates():
    home = Path.home()
    # The native install's stable pointer survives package upgrades.
    yield home / '.codex/packages/standalone/current/bin/codex.exe'
    yield home / 'AppData/Local/Programs/OpenAI/Codex/bin/codex.exe'
    yield home / '.local/bin/codex.exe'
    yield home / '.cargo/bin/codex.exe'
    # npm's .cmd/.ps1 wrappers cannot be used with our native-only boundary.
    roots = [Path(os.environ.get('APPDATA', home / 'AppData/Roaming')) / 'npm']
    located = shutil.which('codex')
    if located:
        roots.insert(0, Path(located).parent)
    for root in roots:
        for pattern in ('node_modules/@openai/codex/vendor/*/codex/codex.exe',
                        'node_modules/@openai/codex/node_modules/@openai/codex-*/vendor/*/codex/codex.exe',
                        'node_modules/@openai/codex-*/vendor/*/codex/codex.exe'):
            yield from sorted(root.glob(pattern))
    # Versioned extension directories change on update: discover on each call.
    for extensions in (home / '.vscode/extensions', home / '.vscode-insiders/extensions'):
        packages = sorted(extensions.glob('openai.chatgpt-*'),
                          key=lambda p: tuple(map(int, re.findall(r'\d+', p.name))), reverse=True)
        for package in packages:
            yield from sorted(package.glob('bin/windows-*/codex.exe'))


def resolve_codex():
    configured = os.environ.get('PROJECT_CODEX_EXECUTABLE', '').strip()
    if configured:
        # An invalid explicit setting must fail closed, never select another CLI.
        if not _native(configured):
            raise ValueError('PROJECT_CODEX_EXECUTABLE must be an absolute native executable path')
        return str(Path(configured).expanduser().resolve())
    located = shutil.which('codex')
    if located and _native(located):
        return str(Path(located).resolve())
    if os.name == 'nt':
        for candidate in _candidates():
            if _native(candidate):
                return str(candidate.resolve())
    raise ValueError('Codex native executable unavailable; set PROJECT_CODEX_EXECUTABLE')
