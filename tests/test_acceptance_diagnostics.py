import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import hermes_diagnostics as diagnostics


class AcceptanceDiagnosticsTests(unittest.TestCase):
    def test_persisted_snapshot_contains_only_allowed_scalars(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(diagnostics,'DIAGNOSTIC_ROOT',Path(folder)/'diagnostics'):
            diagnostics.persist_worker_diagnostics('a'*32,{'api_attempts':1,'effective_watchdog_seconds':90,
                'write_file_invoked':False,'authorization':'sensitive','token':'sensitive',
                'final_response':'sensitive','stream_events':2})
            content=(diagnostics.DIAGNOSTIC_ROOT/('a'*32+'.json')).read_text()
            self.assertNotIn('sensitive',content)
            self.assertEqual(json.loads(content)['effective_watchdog_seconds'],90)
            self.assertFalse(list(diagnostics.DIAGNOSTIC_ROOT.glob('*.tmp')))
