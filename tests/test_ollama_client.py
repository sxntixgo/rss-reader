import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app import ollama_client


def _mock_response(text: str, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"response": text}
    r.raise_for_status = MagicMock()
    return r


@patch("app.ollama_client.httpx.post")
def test_generate_text_success(mock_post):
    mock_post.return_value = _mock_response("Here is the summary.")
    result = ollama_client.generate("llama3.2:3b", "summarize this")
    assert result == "Here is the summary."


@patch("app.ollama_client.httpx.post")
def test_generate_json_success(mock_post):
    payload = {"score": 0.8, "reason": "relevant"}
    mock_post.return_value = _mock_response(json.dumps(payload))
    result = ollama_client.generate("llama3.2:3b", "score this", expect_json=True)
    assert result == payload


@patch("app.ollama_client.httpx.post")
def test_generate_json_invalid_returns_none(mock_post):
    mock_post.return_value = _mock_response("not json at all")
    result = ollama_client.generate("llama3.2:3b", "score this", expect_json=True)
    assert result is None


@patch("app.ollama_client.httpx.post")
def test_generate_json_non_dict_returns_none(mock_post):
    mock_post.return_value = _mock_response("[1, 2, 3]")
    result = ollama_client.generate("llama3.2:3b", "score this", expect_json=True)
    assert result is None


@patch("app.ollama_client.time.sleep")
@patch("app.ollama_client.httpx.post")
def test_generate_retries_on_connect_error(mock_post, mock_sleep):
    mock_post.side_effect = [
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        _mock_response("ok"),
    ]
    result = ollama_client.generate("llama3.2:3b", "test")
    assert result == "ok"
    assert mock_sleep.call_count == 2


@patch("app.ollama_client.time.sleep")
@patch("app.ollama_client.httpx.post")
def test_generate_exhausts_retries(mock_post, mock_sleep):
    mock_post.side_effect = httpx.ConnectError("refused")
    result = ollama_client.generate("llama3.2:3b", "test")
    assert result is None
    assert mock_post.call_count == ollama_client.MAX_RETRIES


@patch("app.ollama_client.time.sleep")
@patch("app.ollama_client.httpx.post")
def test_generate_retries_on_timeout(mock_post, mock_sleep):
    mock_post.side_effect = [
        httpx.TimeoutException("slow"),
        _mock_response("done"),
    ]
    assert ollama_client.generate("m", "p") == "done"


@patch("app.ollama_client.httpx.post")
def test_generate_http_error_returns_none(mock_post):
    r = MagicMock()
    r.status_code = 500
    r.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=r
    )
    mock_post.return_value = r
    result = ollama_client.generate("llama3.2:3b", "test")
    assert result is None


@patch("app.ollama_client.httpx.post")
def test_generate_unexpected_error_returns_none(mock_post):
    mock_post.side_effect = RuntimeError("???")
    assert ollama_client.generate("m", "p") is None


# ── list_models ────────────────────────────────────────────────────────────────

@patch("app.ollama_client.httpx.get")
def test_list_models_success(mock_get):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:7b"}]
    }
    mock_get.return_value = resp
    assert ollama_client.list_models() == ["llama3.1:8b", "qwen2.5:7b"]


@patch("app.ollama_client.httpx.get")
def test_list_models_failure_returns_empty(mock_get, caplog):
    mock_get.side_effect = RuntimeError("down")
    assert ollama_client.list_models() == []
    assert "list_models failed" in caplog.text


@patch("app.ollama_client.httpx.get")
def test_list_models_skips_blank_names(mock_get):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"models": [{"name": ""}, {"name": "ok:1"}, {}]}
    mock_get.return_value = resp
    assert ollama_client.list_models() == ["ok:1"]
