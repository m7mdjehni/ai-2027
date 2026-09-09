"""Trusted synthetic processes only: verify actual cancellation/deadline tree cleanup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hermes_projects import run_worker


@unittest.skipUnless(os.name == 'nt', 'Windows process-tree supervisor')
class ProcessCleanupTests(unittest.TestCase):
    def check_cleanup(self, cancel):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'child.pid'
            # Fixed trusted fixture code, never generated code.
            source = ('import subprocess,sys,time,pathlib; '
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                      'pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
            event = threading.Event()
            timer = threading.Timer(1,event.set) if cancel else None
            if timer: timer.start()
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_worker([sys.executable,'-c',source,str(marker)],input='',cwd=directory,
                               timeout=10 if cancel else 1,cancel_event=event)
            finally:
                if timer: timer.cancel(); timer.join()
            self.assertTrue(marker.exists(),'Fixture must have spawned its child')
            child = marker.read_text().strip()
            process = subprocess.run(['tasklist','/FI','PID eq '+child,'/FO','CSV','/NH'],
                                     capture_output=True,text=True,timeout=10,shell=False)
            self.assertEqual(process.returncode,0)
            self.assertNotIn('"'+child+'"',process.stdout,'Worker descendant remained alive')

    def test_cancellation_tree_cleanup(self): self.check_cleanup(True)
    def test_deadline_tree_cleanup(self): self.check_cleanup(False)


if __name__ == '__main__': unittest.main(verbosity=2)
