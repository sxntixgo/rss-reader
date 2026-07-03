"""Tests for the structured-logging configuration."""
import json
import logging

from app import JsonFormatter, _configure_logging


def _record(level=logging.INFO, name="app.test", msg="hello"):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


def test_json_formatter_emits_valid_json():
    out = JsonFormatter().format(_record(msg="line1"))
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["msg"] == "line1"
    assert payload["ts"].endswith("Z")


def test_json_formatter_includes_exception():
    try:
        raise ValueError("nope")
    except ValueError:
        import sys
        rec = _record(level=logging.ERROR, msg="boom")
        rec.exc_info = sys.exc_info()
    out = JsonFormatter().format(rec)
    payload = json.loads(out)
    assert "ValueError" in payload["exc"]
    assert "nope" in payload["exc"]


def test_configure_logging_uses_json_when_env_set(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    _configure_logging()
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)


def test_configure_logging_falls_back_to_text(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    _configure_logging()
    handler = logging.getLogger().handlers[0]
    assert not isinstance(handler.formatter, JsonFormatter)
