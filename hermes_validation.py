"""Trusted host supervisor. No model calls; no user-controlled Docker arguments."""
import json
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from app.project_workspace import capture_revision, reject_link
from validation.profiles import WEB, container_command

RUNTIME = Path(__file__).resolve().parent / 'runtime' / 'validation'
CONTROL_TIMEOUT = 10


class ValidationProcessError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def docker_call(arguments, timeout=CONTROL_TIMEOUT):
    """Bound output in memory while draining pipes; kill client on limit/deadline.

    Container cleanup is separate: killing a Docker client does not kill its container.
    """
    chunks = bytearray()
    overflow = threading.Event()
    with subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, shell=False) as process:
        def drain():
            while True:
                block = process.stdout.read(4096)
                if not block:
                    break
                remaining = WEB.output_bytes - len(chunks)
                chunks.extend(block[:max(remaining, 0)])
                if len(block) > remaining:
                    overflow.set()
                    break
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        reason = None
        while process.poll() is None:
            if overflow.is_set():
                reason = 'output_limit'; break
            if time.monotonic() >= deadline:
                reason = 'timeout'; break
            time.sleep(0.02)
        if reason:
            process.kill()
        process.wait(timeout=5)
        reader.join(timeout=5)
        if reader.is_alive():
            raise ValidationProcessError('output_unavailable')
        if overflow.is_set():
            raise ValidationProcessError('output_limit')
        if reason:
            raise ValidationProcessError(reason)
        return process.returncode, chunks.decode('utf-8', errors='replace')


def _failure(revision, status, code):
    return {'schema_version': 1, 'profile': WEB.name, 'revision': revision, 'passed': False,
            'status': status, 'errors': [], 'warnings': [], 'error_code': code}


def _check_result(data, revision, files):
    """Do not trust stdout as an authoritative result without validating its contract."""
    if (not isinstance(data, dict) or data.get('schema_version') != 1 or data.get('profile') != WEB.name
            or data.get('revision') != revision or type(data.get('passed')) is not bool
            or data.get('status') not in {'passed', 'failed'}):
        raise ValueError('Invalid validator result')
    errors = data.get('errors')
    if not isinstance(errors, list) or len(errors) > WEB.max_errors or data['passed'] != (len(errors) == 0):
        raise ValueError('Invalid validator errors')
    if data['status'] != ('passed' if data['passed'] else 'failed'):
        raise ValueError('Invalid validation status')
    for error in errors:
        if (not isinstance(error, dict) or error.get('file') not in set(files) | {'index.html'}
                or not isinstance(error.get('line'), int) or error['line'] < 1
                or not isinstance(error.get('column'), int) or error['column'] < 1
                or any(not isinstance(error.get(k), str) or len(error[k]) > 200 for k in ['check', 'code', 'message'])):
            raise ValueError('Invalid structured error')
    return data


def validate_project(project_id, expected_revision):
    started = time.monotonic()
    if not re.fullmatch(r'[a-f0-9]{64}', expected_revision):
        raise ValueError('Invalid revision')
    revision, files = capture_revision(project_id)
    if revision != expected_revision or capture_revision(project_id)[0] != revision:
        return {**_failure(expected_revision, 'stale', 'revision_changed_before_validation'),
                'project_id': project_id, 'current_revision': revision, 'snapshot_passed': False,
                'cleanup_confirmed': True, 'duration_ms': round((time.monotonic() - started) * 1000)}
    # Copy only validated bytes. Never mount the live project, repository, or secrets.
    reject_link(RUNTIME.parent)
    reject_link(RUNTIME)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    name = 'hermes-validation-' + uuid.uuid4().hex
    result = _failure(revision, 'infrastructure_error', 'validator_unavailable')
    attempted = False
    cleanup_ok = True
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=str(RUNTIME)) as temporary:
        snapshot = Path(temporary).resolve()
        if snapshot.parent != RUNTIME.resolve() or ',' in str(snapshot):
            raise ValueError('Invalid snapshot location')
        for relative, content in files.items():
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode('utf-8'))
        try:
            code, image_id = docker_call(['docker', 'image', 'inspect', '--format', '{{.Id}}', WEB.image])
            image_id = image_id.strip()
            if code or not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
                raise ValidationProcessError('validator_image_missing')
            attempted = True
            code, _ = docker_call(container_command(name, snapshot, image_id))
            if code:
                raise ValidationProcessError('container_create_failed')
            code, output = docker_call(['docker', 'start', '--attach', name], WEB.timeout_seconds)
            if code:
                raise ValidationProcessError('validator_process_failed')
            result = _check_result(json.loads(output), revision, files)
            result['validator_image'] = image_id
        except ValidationProcessError as error:
            result = _failure(revision, 'timeout' if error.reason == 'timeout' else 'infrastructure_error', error.reason)
        except (OSError, ValueError, subprocess.SubprocessError):
            result = _failure(revision, 'infrastructure_error', 'invalid_validator_response_or_runtime')
        finally:
            if attempted:
                try:
                    code, _ = docker_call(['docker', 'rm', '--force', '--volumes', name])
                    if code:
                        # A create failure may leave no container. Confirm via a daemon query.
                        code, remaining = docker_call(['docker', 'ps', '--all', '--quiet', '--filter', 'name=^/' + name + '$'])
                        cleanup_ok = code == 0 and not remaining.strip()
                except (OSError, ValidationProcessError, subprocess.SubprocessError):
                    cleanup_ok = False
    if not cleanup_ok:
        result = _failure(revision, 'infrastructure_error', 'container_cleanup_unconfirmed')
    try:
        current = capture_revision(project_id)[0]
    except (ValueError, OSError):
        current = None
    result['current_revision'] = current
    result['snapshot_passed'] = result['passed']
    if current != revision and cleanup_ok:
        result.update(passed=False, status='stale', error_code='revision_changed_during_validation')
    result.update(project_id=project_id, cleanup_confirmed=cleanup_ok,
                  duration_ms=round((time.monotonic() - started) * 1000))
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validate a managed static website; never calls Hermes.')
    parser.add_argument('project_id')
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    print(json.dumps(validate_project(args.project_id, args.revision), indent=2))
