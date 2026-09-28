"""Host-side project execution through the existing multi-model router."""
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from app.project_workspace import workspace, list_files
from hermes_router import route_prompt, detect_provider_error, _command_timeout, PROJECT_MODELS
from hermes_diagnostics import safe_diagnostics, failure_category

logger = logging.getLogger("hermes_projects")


def _project_worker_timeout() -> int:
    try:
        return max(10, int(os.getenv("HERMES_PROJECT_WORKER_TIMEOUT", "900")))
    except ValueError:
        logger.warning("Invalid HERMES_PROJECT_WORKER_TIMEOUT; using 900 seconds")
        return 900


def run_worker(command, *, input, cwd, timeout, **kwargs):
    """On Windows, terminate the Python launcher's entire tree before fallback."""
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=cwd,
                          shell=False, env=kwargs.get('env'),
                          start_new_session=os.name != 'nt') as process:
        try:
            cancel_event = kwargs.get('cancel_event')
            if cancel_event is None:
                stdout, stderr = process.communicate(input=input, timeout=timeout)
            else:
                deadline = time.monotonic() + timeout
                first = True
                while True:
                    if cancel_event.is_set() or time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        stdout, stderr = process.communicate(input=input if first else None,
                            timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        first = False
        except subprocess.TimeoutExpired as timeout_error:
            try:
                if os.name == "nt":
                    cleanup = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                             capture_output=True, timeout=10, shell=False)
                    if cleanup.returncode:
                        raise RuntimeError("Worker tree termination could not be confirmed")
                else:
                    import signal
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                process.kill()
                # Fail closed: a cleanup failure must never activate fallback.
                raise RuntimeError("Worker tree termination could not be confirmed") from error
            raise timeout_error
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def hermes_python():
    configured = os.getenv("HERMES_PYTHON")
    default = Path(os.getenv("LOCALAPPDATA", "")) / "hermes/hermes-agent/venv/Scripts/python.exe"
    executable = Path(configured) if configured else default
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("Set HERMES_PYTHON to the Python executable in your Windows Hermes environment")
    return str(executable)


def build_project(project_id, prompt):
    if os.getenv('PROJECT_AI_ENGINE', 'codex').lower() == 'codex':
        from codex_adapter import project, MODEL
        workspace(project_id, create=True)
        logger.info('Codex project build started | project_id=%s', project_id)
        result = project(project_id, prompt, timeout=min(_project_worker_timeout(), 540))
        result.update(task_type='coding', model=MODEL, provider='openai-codex',
                      fallback_used=False, files=list_files(project_id))
        logger.info('Codex project build finished | project_id=%s success=%s file_count=%d',
                    project_id, result['success'], len(result['files']))
        return result
    executable = hermes_python()
    target = workspace(project_id, create=True)
    logger.info("PROJECT BUILDER | Task: development request | Workspace: generated_projects/%s", project_id)

    def execute(agent_prompt, model, provider):
        try:
            process = run_worker(
                [executable, "-I", str(Path(__file__).with_name("hermes_project_worker.py"))],
                input=json.dumps({"project_id": project_id, "prompt": agent_prompt, "model": model,
                                  "provider": provider, "capture_diagnostics": True}),
                cwd=str(target), capture_output=True, text=True, encoding="utf-8",
                timeout=_project_worker_timeout(), shell=False,
            )
            if process.returncode:
                logger.error("Hermes worker attempt failed | model=%s provider=%s exception_type=ProcessExit http_status=None timeout_reason=None write_file_invoked=False error=Restricted Hermes worker exited unsuccessfully", model, provider)
                return False, "", "Restricted Hermes worker exited unsuccessfully"
            result = json.loads(process.stdout)
            diagnostics = safe_diagnostics(result.get("diagnostics"))
            logger.info("Hermes worker attempt | model=%s provider=%s diagnostics=%s", model, provider, json.dumps(diagnostics, sort_keys=True))
            if not result.get("success"):
                error = str(result.get("error", "Hermes did not create a complete project"))
                return False, "", detect_provider_error(error) or failure_category(error)
            files = list_files(project_id)
            return bool(files), "Project files created", None if files else "Hermes created no files"
        except subprocess.TimeoutExpired:
            logger.error("Hermes worker attempt failed | model=%s provider=%s exception_type=TimeoutExpired http_status=None timeout_reason=worker process timeout write_file_invoked=unknown", model, provider)
            return False, "", "Hermes project build timed out"
        except (OSError, ValueError, TypeError) as error:
            logger.error("Hermes worker attempt failed | model=%s provider=%s exception_type=%s http_status=%s timeout_reason=None write_file_invoked=unknown error=%s",
                         model, provider, type(error).__name__, None, failure_category(error))
            return False, "", "Invalid worker response or unsafe generated files"

    result = route_prompt(prompt, runner=execute, task_type_override="coding", routing_models=PROJECT_MODELS).to_dict()
    result.pop("response", None)
    try:
        result["files"] = list_files(project_id)
    except (ValueError, OSError):
        result.update(success=False, files=[], error="Project workspace validation failed")
    logger.info("Project %s | Files detected: %d", "completed" if result["success"] else "failed", len(result["files"]))
    return result
