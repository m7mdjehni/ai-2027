import os
import logging
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a helpful personal assistant. Keep answers concise and helpful. Use available tools when requested."


def active_ai_provider() -> str:
    """Return the configured adapter while retaining Ollama support."""
    return os.getenv("AI_PROVIDER", "hermes").strip().lower()


def classify_ollama_error(error: Exception) -> str:
    """Classify Ollama errors for appropriate HTTP status code mapping."""
    error_str = str(error).lower()
    
    # Connection errors
    if isinstance(error, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(error, requests.exceptions.Timeout):
        return "timeout_error"
    
    # HTTP errors
    if isinstance(error, requests.exceptions.HTTPError):
        status_code = getattr(error.response, "status_code", None)
        if status_code == 404:
            return "model_not_found"
        if status_code == 500:
            return "ollama_internal_error"
        if status_code >= 500:
            return "ollama_service_error"
        if status_code == 400:
            return "invalid_request"
        return "http_error"
    
    # Response parsing errors
    if isinstance(error, (ValueError, KeyError)):
        return "invalid_response"
    
    # Other exceptions
    if isinstance(error, requests.exceptions.RequestException):
        return "request_error"
    
    return "unexpected_error"


def safe_error_message(error: Exception) -> str:
    """Safely extract error message without exposing sensitive data."""
    message = str(error) or error.__class__.__name__
    return message.strip()


def build_messages(history: List[Dict], user_message: str):
    """Build message list for Ollama API."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        messages.append({"role": h.get('role', 'user'), "content": h.get('message')})
    messages.append({"role": "user", "content": user_message})
    return messages


def call_ollama(history: List[Dict], user_message: str) -> str:
    """Call local Ollama model via HTTP API."""
    base_url = os.getenv('OLLAMA_BASE_URL')
    model = os.getenv('OLLAMA_MODEL')
    
    if not base_url:
        raise RuntimeError("OLLAMA_BASE_URL not set in environment")
    if not model:
        raise RuntimeError("OLLAMA_MODEL not set in environment")
    
    messages = build_messages(history, user_message)
    endpoint = f"{base_url}/api/chat"
    
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    
    try:
        response = requests.post(endpoint, json=payload, timeout=60)
        response.raise_for_status()
        
        data = response.json()
        
        # Extract the assistant's message from Ollama response
        if "message" in data and "content" in data["message"]:
            reply = data["message"]["content"]
            if not reply or not isinstance(reply, str):
                raise ValueError("Ollama returned empty or invalid message content")
            return reply
        else:
            raise ValueError(f"Unexpected Ollama response format: {data}")
    
    except requests.exceptions.Timeout:
        logger.error(
            "Ollama request timed out: endpoint=%s model=%s",
            endpoint,
            model,
        )
        raise RuntimeError(f"Ollama request timed out after 60 seconds")
    
    except requests.exceptions.ConnectionError as e:
        logger.error(
            "Ollama connection failed: endpoint=%s model=%s error=%s",
            endpoint,
            model,
            safe_error_message(e),
        )
        raise RuntimeError(f"Cannot connect to Ollama at {base_url}. Is Ollama running?")
    
    except requests.exceptions.HTTPError as e:
        status_code = getattr(e.response, "status_code", "unknown")
        logger.error(
            "Ollama HTTP error: endpoint=%s status=%s model=%s error=%s",
            endpoint,
            status_code,
            model,
            safe_error_message(e),
        )
        raise RuntimeError(f"Ollama returned HTTP {status_code}")
    
    except ValueError as e:
        logger.error(
            "Ollama response parsing failed: endpoint=%s model=%s error=%s",
            endpoint,
            model,
            safe_error_message(e),
        )
        raise RuntimeError(f"Ollama returned invalid response: {safe_error_message(e)}")
    
    except Exception as error:
        category = classify_ollama_error(error)
        logger.error(
            "Ollama request failed: category=%s exception_type=%s message=%s",
            category,
            type(error).__name__,
            safe_error_message(error),
        )
        raise RuntimeError(f"Ollama error ({category}): {safe_error_message(error)}")


def call_hermes_router(history: List[Dict], user_message: str) -> Dict:
    """Call the Hermes router running on Windows from the Docker backend."""
    base_url = os.getenv("HERMES_ROUTER_URL", "http://host.docker.internal:8765").rstrip("/")
    try:
        timeout = max(10, int(os.getenv("HERMES_ROUTER_TIMEOUT", "620")))
    except ValueError:
        timeout = 620
    endpoint = f"{base_url}/route"
    payload = {"prompt": user_message, "history": history[-20:]}
    try:
        response = requests.post(endpoint, json=payload, timeout=timeout)
    except requests.exceptions.Timeout as error:
        logger.error("Hermes router timed out: endpoint=%s", endpoint)
        raise RuntimeError(f"Hermes router timed out after {timeout} seconds") from error
    except requests.exceptions.ConnectionError as error:
        logger.error("Hermes router connection failed: endpoint=%s", endpoint)
        raise RuntimeError(
            "Cannot connect to the Windows Hermes router. Start hermes_service.py on the host."
        ) from error
    except requests.exceptions.RequestException as error:
        logger.error("Hermes router request failed: exception_type=%s", type(error).__name__)
        raise RuntimeError("Hermes router communication failed") from error

    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError("Hermes router returned invalid JSON") from error
    if not response.ok or not data.get("success"):
        safe_error = data.get("error") if isinstance(data.get("error"), str) else "routing failed"
        raise RuntimeError(f"Hermes router error: {safe_error}")
    reply = data.get("response")
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError("Hermes router returned an empty response")
    logger.info(
        "Hermes response: task_type=%s model=%s provider=%s fallback_used=%s",
        data.get("task_type"), data.get("model"), data.get("provider"), data.get("fallback_used"),
    )
    return data


def call_assistant(history: List[Dict], user_message: str) -> Dict:
    """Dispatch to Hermes by default, with Ollama retained as an option."""
    provider = active_ai_provider()
    if provider == "ollama":
        return {
            "response": call_ollama(history, user_message),
            "task_type": "general",
            "model": os.getenv("OLLAMA_MODEL"),
            "provider": "ollama",
            "fallback_used": False,
        }
    if provider == "hermes":
        return call_hermes_router(history, user_message)
    raise RuntimeError("AI_PROVIDER must be either 'hermes' or 'ollama'")
