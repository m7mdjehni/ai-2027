"""Hermes task classifier and multi-model command router."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("hermes_router")

MODELS = {
    "coding": {
        "primary": {"model": "mimo-v2.5-free", "provider": "opencode-free"},
        "fallback": {"model": "gpt-5.6-sol", "provider": "openai-codex"},
    },
    "reasoning": {
        "primary": {"model": "gpt-5.6-sol", "provider": "openai-codex"},
        "fallback": None,
    },
}

PROJECT_MODELS = {
    "coding": {
        "primary": {"model": "gpt-5.6-sol", "provider": "openai-codex"},
        "fallback": None,
    },
}

ERROR_PATTERNS = (
    r"api call failed",
    r"\bhttp(?: status)?\s*[:=]?\s*(400|401|403|404|408|409|422|429|5\d\d)\b",
    r"\b(400|401|403|404|408|409|422|429|5\d\d)\s+(bad request|unauthorized|forbidden|not found|request timeout|conflict|unprocessable|too many requests|server error)",
    r"rate[ -]?limit", r"too many requests", r"model (?:is )?unavailable",
    r"provider (?:is )?unavailable", r"authentication failed", r"\bunauthorized\b",
    r"insufficient[_ ]quota", r"error from provider", r"timed? out",
    r"connection (?:refused|failed|reset)",
)

CODING_KEYWORDS = (
    "code", "coding", "website", "web site", "webpage", "html", "css",
    "javascript", "typescript", "python", "php", "java", "c++", "fastapi",
    "django", "flask", "laravel", "react", "vue", "angular", "node", "npm",
    "program", "programming", "debug", "bug", "fix code", "create app",
    "build app", "create website", "build website", "frontend", "backend", "api",
    "database", "sql", "docker", "github", "git", "endpoint", "function",
    "class", "script", "source code", "refactor", "compile", "repository",
    "موقع", "برمجة", "كود", "تطبيق", "برنامج", "اصلح الكود", "أصلح الكود",
    "انشئ موقع", "أنشئ موقع", "إنشاء موقع", "سوي موقع", "صمم موقع",
    "قاعدة بيانات", "واجهة", "بايثون", "جافاسكربت", "دالة", "سكريبت",
)


@dataclass
class RouteResult:
    success: bool
    task_type: str
    model: str
    provider: str
    fallback_used: bool
    response: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def classify_task(prompt: str) -> str:
    """Classify the current user prompt as coding or reasoning/general."""
    text = prompt.casefold()
    # Boundaries prevent short terms such as "api", "git", and "class" from
    # matching unrelated words such as "capital", "digital", or "classic".
    for keyword in CODING_KEYWORDS:
        pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
        if re.search(pattern, text, flags=re.IGNORECASE):
            return "coding"
    return "reasoning"


def detect_provider_error(output: str, returncode: int = 0) -> Optional[str]:
    """Return a safe reason without returning potentially sensitive CLI output."""
    if returncode != 0:
        # Still identify 429 when Hermes reports it alongside a non-zero exit.
        status_match = re.search(r"\b(4\d\d|5\d\d)\b", output)
        return f"HTTP {status_match.group(1)}" if status_match else f"Hermes exited with status {returncode}"
    for pattern in ERROR_PATTERNS:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            status_match = re.search(r"\b(4\d\d|5\d\d)\b", match.group(0))
            if status_match:
                return f"HTTP {status_match.group(1)}"
            if re.search(r"rate[ -]?limit|too many requests", match.group(0), re.I):
                return "rate limit"
            if re.search(r"timed? out", match.group(0), re.I):
                return "timeout"
            return "provider error"
    return None


def _command_timeout() -> int:
    try:
        return max(10, int(os.getenv("HERMES_COMMAND_TIMEOUT", "300")))
    except ValueError:
        logger.warning("Invalid HERMES_COMMAND_TIMEOUT; using 300 seconds")
        return 300


def run_hermes(prompt: str, model: str, provider: str) -> Tuple[bool, str, Optional[str]]:
    """Run Hermes without a shell and return success, response, and safe error."""
    command = ["hermes", "-z", prompt, "--model", model, "--provider", provider]
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            timeout=_command_timeout(), shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"Hermes timed out after {_command_timeout()} seconds"
    except FileNotFoundError:
        return False, "", "Hermes command was not found on the Windows host PATH"
    except OSError as error:
        logger.error("Unable to start Hermes: exception_type=%s", type(error).__name__)
        return False, "", "Hermes could not be started"

    stdout = result.stdout.strip()
    combined = "\n".join(part for part in (stdout, result.stderr.strip()) if part)
    failure_reason = detect_provider_error(combined, result.returncode)
    if failure_reason:
        return False, "", failure_reason
    if not stdout:
        return False, "", "Hermes returned an empty response"
    return True, stdout, None


def build_agent_prompt(prompt: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """Add bounded context while keeping classification prompt-local."""
    if not history:
        return prompt
    lines = ["Conversation context (oldest to newest):"]
    for item in history[-20:]:
        role = "Assistant" if item.get("role") == "assistant" else "User"
        message = str(item.get("message", "")).strip()
        if message:
            lines.append(f"{role}: {message}")
    lines.extend(("", "Current user request:", prompt))
    return "\n".join(lines)


def route_prompt(prompt: str, history: Optional[List[Dict[str, str]]] = None, *, runner=None, task_type_override=None, routing_models=None) -> RouteResult:
    """Classify, route, and automatically fall back when configured."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return RouteResult(False, "reasoning", "", "", False, error="Prompt is required")
    task_type = task_type_override or classify_task(clean_prompt)
    execute = runner or run_hermes
    config = (routing_models or MODELS)[task_type]
    primary, fallback = config["primary"], config["fallback"]
    agent_prompt = build_agent_prompt(clean_prompt, history)

    logger.info("Task type: %s", task_type)
    logger.info("Selected model: %s", primary["model"])
    logger.info("Provider: %s", primary["provider"])
    success, response, error = execute(agent_prompt, primary["model"], primary["provider"])
    if success:
        return RouteResult(True, task_type, primary["model"], primary["provider"], False, response=response)

    logger.warning("Primary model failed: %s", error)
    if fallback is None:
        return RouteResult(False, task_type, primary["model"], primary["provider"], False, error=error)
    logger.warning("Activating fallback")
    logger.info("Selected model: %s", fallback["model"])
    logger.info("Provider: %s", fallback["provider"])
    success, response, fallback_error = execute(agent_prompt, fallback["model"], fallback["provider"])
    if not success:
        logger.error("Fallback model failed: %s", fallback_error)
    return RouteResult(success, task_type, fallback["model"], fallback["provider"], True,
                       response=response if success else None,
                       error=None if success else fallback_error)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    prompt = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else input("Enter your task: ").strip()
    result = route_prompt(prompt)
    if result.success:
        print(result.response)
        return 0
    print(f"Request failed: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
