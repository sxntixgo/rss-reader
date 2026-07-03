import json
import logging
import os
import sys

from flask import Flask


class JsonFormatter(logging.Formatter):
    """Single-line JSON log records, suitable for grep/jq/log-shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    """Configure root logging once. LOG_FORMAT=json switches to structured output."""
    fmt = os.environ.get("LOG_FORMAT", "").lower()
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
    root = logging.getLogger()
    # Replace handlers idempotently — pytest re-imports the module.
    root.handlers = [handler]
    root.setLevel(logging.INFO)


_configure_logging()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    from app.db import close_db, init_db
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    from app.routes import bp
    app.register_blueprint(bp)

    # Only start scheduler when running under gunicorn (single worker) or flask dev server.
    # Guard avoids double-start when the module is imported by pytest.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        from app.scheduler import init_scheduler
        scheduler = init_scheduler(app)
        scheduler.start()

    return app
