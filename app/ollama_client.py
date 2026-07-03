import json
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
DEFAULT_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))
MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 3, 7]


def generate(
    model: str,
    prompt: str,
    expect_json: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict | str | None:
    """Call Ollama /api/generate. Returns dict if expect_json=True, str otherwise, None on failure."""
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if expect_json:
        payload["format"] = "json"

    for attempt in range(MAX_RETRIES):
        try:
            r = httpx.post(
                f"{OLLAMA_BASE}/api/generate",
                json=payload,
                timeout=timeout,
            )
            r.raise_for_status()
            text: str = r.json()["response"].strip()
            if expect_json:
                return _validate_json(text)
            return text
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            log.warning("Ollama attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
        except httpx.HTTPStatusError as exc:
            log.error("Ollama HTTP error %s: %s", exc.response.status_code, exc)
            return None
        except Exception as exc:
            log.error("Ollama unexpected error: %s", exc)
            return None

    log.error("Ollama: all %d retries exhausted for model=%s", MAX_RETRIES, model)
    return None


def list_models() -> list[str]:
    """Return installed model names from Ollama /api/tags. Empty list on failure."""
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
        r.raise_for_status()
        data = r.json()
        return sorted(m["name"] for m in data.get("models", []) if m.get("name"))
    except Exception as exc:
        log.warning("Ollama list_models failed: %s", exc)
        return []


def _validate_json(text: str) -> dict | None:
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"expected dict, got {type(data).__name__}")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Ollama JSON parse failed: %s | raw: %.300s", exc, text)
        return None
