"""Offline tool-boundary path regression; never touches existing projects."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import project_workspace as ws
from hermes_project_worker import dispatch, project_tool_path


class ProjectToolPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = patch.object(ws, 'ROOT', Path(temporary.name) / 'generated_projects')
        root.start()
        self.addCleanup(root.stop)
        self.project = 'a' * 32
        self.other = 'b' * 32
        self.base = ws.workspace(self.project, create=True)
        ws.workspace(self.other, create=True)
        self.prefix = 'generated_projects/' + self.project + '/'

    def call(self, name, **arguments):
        return json.loads(dispatch(self.project, name, arguments))

    def test_root_relative_write_read_and_list(self):
        for name in ('index.html', 'script.js', 'styles.css', 'assets/logo.svg'):
            result = self.call('project_write_file', path=name, content='fixture')
            self.assertEqual(result['path'], name)
            self.assertEqual((self.base / name).read_text(), 'fixture')
            self.assertEqual(self.call('project_read_file', path=name), {'content': 'fixture'})
        self.assertEqual(self.call('project_list_files')['files'],
                         ['assets/logo.svg', 'index.html', 'script.js', 'styles.css'])

    def test_same_project_and_duplicate_prefixes_are_canonical(self):
        for prefix in (self.prefix, self.prefix * 2, self.prefix * 3):
            with self.subTest(prefix=prefix):
                result = self.call('project_write_file', path=prefix + 'index.html', content=prefix)
                self.assertEqual(result['path'], 'index.html')
                self.assertEqual(self.call('project_read_file', path=prefix + 'index.html'), {'content': prefix})
                self.assertEqual(self.call('project_read_file', path='index.html'), {'content': prefix})
                self.assertEqual(self.call('project_list_files'), {'files': ['index.html']})
                self.assertFalse((self.base / 'generated_projects').exists())

    def test_current_directory_does_not_change_selected_workspace(self):
        original = Path.cwd()
        try:
            os.chdir(self.base)
            self.call('project_write_file', path=self.prefix + 'index.html', content='root')
            self.assertEqual((self.base / 'index.html').read_text(), 'root')
            os.chdir(ws.workspace(self.other))
            self.call('project_write_file', path='styles.css', content='root')
            self.assertEqual((self.base / 'styles.css').read_text(), 'root')
            self.assertEqual(ws.list_files(self.other), [])
        finally:
            os.chdir(original)

    def assert_rejected(self, paths):
        for path in paths:
            with self.subTest(path=path):
                for name in ('project_write_file', 'project_read_file'):
                    self.assertIn('error', self.call(name, path=path, content='denied'))
        self.assertEqual(ws.list_files(self.project), [])
        self.assertEqual(ws.list_files(self.other), [])

    def test_traversal_is_rejected_before_and_after_prefix(self):
        paths = ['../index.html', 'a/../index.html', './index.html', 'a//index.html',
                 '../', '%2e%2e/index.html', 'index.html\x00', 'a\\..\\index.html']
        self.assert_rejected(paths + [self.prefix + path for path in paths])

    def test_absolute_drive_and_unc_paths_are_rejected(self):
        paths = ['/index.html', 'C:/index.html', 'C:\\index.html', 'C:index.html',
                 '\\\\server\\share\\index.html', '//server/share/index.html',
                 '\\index.html', '\\\\?\\C:\\index.html']
        self.assert_rejected(paths + [self.prefix + path for path in paths])

    def test_other_project_prefix_is_rejected_even_after_same_project_prefix(self):
        other = 'generated_projects/' + self.other + '/index.html'
        self.assert_rejected([other, self.prefix + other,
                              'generated_projects/not-a-project/index.html',
                              'GENERATED_PROJECTS/' + self.other + '/index.html'])

    def test_existing_security_limits_and_canonical_hardlinks_remain_enforced(self):
        self.assert_rejected([self.prefix + name for name in
            ('CON.txt', 'auth.json', 'index.html:stream', 'x.txt.', 'x.txt ', 'binary.exe')])
        with self.assertRaises(ValueError):
            project_tool_path(self.project, self.prefix * 6 + 'index.html')
        outside = ws.ROOT / 'outside.txt'
        outside.write_text('protected')
        os.link(outside, self.base / 'index.html')
        for tool in ('project_read_file', 'project_write_file'):
            self.assertIn('error', self.call(tool, path=self.prefix + 'index.html', content='denied'))
        self.assertEqual(outside.read_text(), 'protected')

    def test_shared_workspace_api_and_legacy_files_remain_literal(self):
        # Reproduce the old bug without altering the shared validator/filesystem API.
        legacy = self.prefix + 'index.html'
        ws.write_file(self.project, legacy, 'legacy fixture')
        self.assertFalse((self.base / 'index.html').exists())
        self.assertEqual(ws.list_files(self.project), [legacy])
        self.assertEqual(ws.read_file(self.project, legacy), 'legacy fixture')
        self.call('project_write_file', path=legacy, content='new root file')
        self.assertEqual((self.base / 'index.html').read_text(), 'new root file')
        self.assertEqual(ws.read_file(self.project, legacy), 'legacy fixture')
        self.assertEqual(self.call('project_list_files')['files'], sorted([legacy, 'index.html']))


if __name__ == '__main__':
    unittest.main()
