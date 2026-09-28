import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import codex_discovery as discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for target in (patch.dict(os.environ, {'PROJECT_CODEX_EXECUTABLE': '', 'APPDATA': str(self.home / 'roaming')}),
                       patch.object(discovery.Path, 'home', return_value=self.home),
                       patch.object(discovery.shutil, 'which', return_value=None)):
            target.start()
            self.addCleanup(target.stop)

    def executable(self, relative):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.chmod(0o700)
        return path

    def test_absolute_override_with_spaces(self):
        path = self.executable('with spaces/codex.exe')
        with patch.dict(os.environ, {'PROJECT_CODEX_EXECUTABLE': str(path)}):
            self.assertEqual(discovery.resolve_codex(), str(path.resolve()))

    def test_invalid_override_never_falls_back(self):
        self.executable('.local/bin/codex.exe')
        for value in ('missing.exe', str(self.home / 'missing.exe'), 'codex --flag'):
            with patch.dict(os.environ, {'PROJECT_CODEX_EXECUTABLE': value}), self.assertRaises(ValueError):
                discovery.resolve_codex()

    @unittest.skipUnless(os.name == 'nt', 'Windows native discovery')
    def test_standalone_without_path_precedes_extension(self):
        self.executable('.vscode/extensions/openai.chatgpt-99.1.0/bin/windows-x86_64/codex.exe')
        native = self.executable('.codex/packages/standalone/current/bin/codex.exe')
        self.assertEqual(discovery.resolve_codex(), str(native.resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows native discovery')
    def test_native_installer_without_path(self):
        native = self.executable('AppData/Local/Programs/OpenAI/Codex/bin/codex.exe')
        self.assertEqual(discovery.resolve_codex(), str(native.resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows native discovery')
    def test_newest_extension_without_path_and_after_update(self):
        old = self.executable('.vscode/extensions/openai.chatgpt-26.9.1-win32-x64/bin/windows-x86_64/codex.exe')
        self.assertEqual(discovery.resolve_codex(), str(old.resolve()))
        new = self.executable('.vscode/extensions/openai.chatgpt-26.10.1-win32-x64/bin/windows-x86_64/codex.exe')
        self.assertEqual(discovery.resolve_codex(), str(new.resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows native discovery')
    def test_npm_wrapper_resolves_native_payload(self):
        wrapper = self.executable('roaming/npm/codex.cmd')
        native = self.executable('roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/codex/codex.exe')
        with patch.object(discovery.shutil, 'which', return_value=str(wrapper)):
            self.assertEqual(discovery.resolve_codex(), str(native.resolve()))

    @unittest.skipUnless(os.name == 'nt', 'Windows native discovery')
    def test_explicit_shell_wrappers_rejected(self):
        for suffix in ('.cmd', '.bat', '.ps1', '.js'):
            path = self.executable('codex' + suffix)
            with patch.dict(os.environ, {'PROJECT_CODEX_EXECUTABLE': str(path)}), self.assertRaises(ValueError):
                discovery.resolve_codex()

    def test_missing_install_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'PROJECT_CODEX_EXECUTABLE'):
            discovery.resolve_codex()
