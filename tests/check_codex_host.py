"""Credential-safe preflight. Run in the same terminal/account as the host."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from codex_adapter import environment
from codex_discovery import resolve_codex


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    try:
        executable = resolve_codex()
        print('Codex executable: ' + executable, flush=True)
        result = subprocess.run([executable, '--version'], capture_output=True,
                                timeout=15, shell=False, env=environment())
        if result.returncode:
            print('FAIL: native executable could not start')
            return 1
        result = subprocess.run([executable, 'login', 'status'], capture_output=True,
                                timeout=15, shell=False, env=environment())
        # Never print raw CLI output: status may include account information.
        if result.returncode:
            print('BLOCKED: CLI login is unavailable in this host environment. '
                  'Run the discovered executable with login in your normal terminal.')
            return 1
        print('PASS: native CLI starts and reports an existing login')
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired):
        print('BLOCKED: native CLI unavailable; configure PROJECT_CODEX_EXECUTABLE '
              'as an absolute path to codex.exe')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
