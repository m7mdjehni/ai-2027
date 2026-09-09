"""Installed Hermes repair-mode compatibility; all network calls blocked."""
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import project_workspace as ws
from hermes_project_worker import run, RepairStop


def conversation(self, prompt):
    assert self.max_iterations == 8
    assert len(self.tools) == 3
    assert not self._fallback_chain
    from run_agent import AIAgent
    with patch.object(AIAgent, '_interruptible_api_call', return_value={}) as api:
        for _ in range(24): self._interruptible_api_call({})
        try: self._interruptible_api_call({})
        except RepairStop: pass
        else: raise AssertionError('API attempt gate failed')
        assert api.call_count == 24
    self.repair_api_calls = 0
    result = json.loads(self._invoke_tool('project_read_file', {'path':'script.js'}))
    assert 'broken' in result['content']
    self._invoke_tool('project_write_file', {'path':'script.js','content':'const repaired = 1;'})
    return {'completed':True,'failed':False}


if __name__ == '__main__':
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        try:
            with patch.dict(os.environ, {'HERMES_HOME':str(Path(directory)/'hermes')}), \
                 patch.object(ws,'ROOT',Path(directory)/'projects'), \
                 patch('socket.socket.connect',side_effect=RuntimeError('Network disabled in test')):
                from run_agent import AIAgent
                project = 'd'*32
                ws.workspace(project,create=True)
                ws.write_file(project,'script.js','const broken = ;')
                with patch('hermes_cli.runtime_provider.resolve_runtime_provider',return_value={
                    'provider':'openrouter','api_key':'offline-test-only','base_url':'http://127.0.0.1:1/v1'}), \
                     patch.object(AIAgent,'run_conversation',conversation):
                    result = run({'project_id':project,'revision':ws.capture_revision(project)[0],
                        'repair':True,'prompt':'offline repair','model':'offline-model','provider':'openrouter'})
                assert result['success'],result
                assert result['revision'] == ws.capture_revision(project)[0]
                print('PASS: installed Hermes repair construction, API gate, restricted edits, final revision; no provider calls')
        finally:
            os.chdir(original)
            logging.shutdown()
