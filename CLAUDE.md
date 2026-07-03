# Better Read — Codebase Guide

## What this is
Single-user, self-hosted RSS reader with LLM-powered relevance ranking.
Flask app in Docker; Ollama on Windows host; SQLite in ./data/rss.db.

## Key files
- `app/__init__.py` — Flask app factory (`create_app`). Start here.
- `app/db.py` — DB connection + `get_setting`/`set_setting` helpers. Use `get_db()` inside request context; `get_db_direct()` outside.
- `app/pipeline.py` — LLM scoring + summarization. Pipeline runs are serialized via a process-wide `_PIPELINE_LOCK`.
- `app/ollama_client.py` — All Ollama HTTP calls. `generate()` (with retries) and `list_models()`.
- `app/prompts.py` — The three LLM prompts. Edit here to tune behavior.
- `app/routes.py` — Flask routes. HTMX-first: most return HTML fragments.
- `app/feeds.py` — feedparser polling. `poll_all_feeds(app)` is the entry point.
- `app/scheduler.py` — APScheduler wiring. Jobs registered here.

## Running locally
```
docker compose up --build
```
Requires: Ollama running on host with `OLLAMA_HOST=0.0.0.0:11434` (env var set before starting Ollama).

## Environment variables
See `.env.example`. Copy to `.env` before first run. Required vars:
- `OLLAMA_HOST` — default `http://host.docker.internal:11434`
- `OLLAMA_TIMEOUT` — seconds per Ollama call, default `180`. Bump for 8b+ models on remote/slower hardware.
- `SCORING_MODEL` — default `llama3.2:3b`. Overridable at runtime via Settings (`settings.scoring_model`).
- `SUMMARY_MODEL` — default `llama3.2:3b`. Overridable at runtime via Settings (`settings.summary_model`).
- `SCORE_THRESHOLD` — float 0-1, default `0.35`
- `SCORING_SNIPPET_CHARS` — chars of article text fed to scorer, default `2000`
- `FLASK_SECRET_KEY` — set to a random string
- `LOG_FORMAT` — `json` switches root logger to single-line JSON; default is human-readable.
- `DB_PATH` / `BACKUP_DIR` / `KEEP` — read by `scripts/backup.py` for the SQLite backup helper.

## DB schema
See `app/schema.sql`. Auto-applied on startup via `init_db()`, which also runs idempotent `ALTER TABLE` migrations for newer columns (`thumbnail_url`, `read_at`, `feed_content`).

Tables: `feeds`, `articles`, `votes`, `preferences` (single row), `settings` (key/value).

Article status flow:
```
new → scored → summarized → liked | disliked | dismissed
           └→ hidden  (score < threshold, skip summarization)
```
`dismissed` is only set in bulk via `POST /dismiss-all` (the per-article dismiss button is removed). Votes are kept in the `votes` table even after dismiss.

`articles.feed_content` stores the feed-provided body (`<content:encoded>` for RSS, `<content>` for Atom — feedparser unifies both into `entry.content[0].value`). Used as a fallback when trafilatura's HTTP fetch returns nothing. `articles.read_at` is set when the user first opens the reader modal.

## Routes
- `GET /` `GET /settings` — pages
- `GET /articles` `GET /feeds` `GET /count` — HTML fragments
- `POST /vote/<id>/<1|-1>` — like/dislike per article
- `POST /dismiss-all` — bulk-mark every currently-listed article as `dismissed`. Honors `?feed=<id>`.
- `POST /article/<id>/dismiss` — single-article dismiss; gesture-only (no UI button), fired by swipe-left on `.article-row`.
- `GET /article/<id>/content` — reader modal fragment (marks `read_at`)
- `POST /poll` — kicks off poll + pipeline in a background thread
- `GET /status` — pipeline status (HTML fragment, or JSON if `Accept: application/json`)
- `GET|POST /preferences` — profile text view/edit
- `POST /preferences/regenerate` — rebuild profile from votes (background thread)
- `GET|POST /settings/models` — choose scoring/summary models from `ollama_client.list_models()`
- `GET|POST /feeds/opml` — OPML export (GET) / import (POST file upload)
- `POST /feeds/<id>/tags` — save comma-separated tags; sidebar groups feeds by tag.

## LLM notes
- Scoring uses `format:"json"` Ollama param to constrain output to valid JSON.
- Article content is wrapped in XML delimiters (`<article_snippet>`, `<article_content>`) to mitigate prompt injection.
- `generate()` returns `None` on failure — all callers must handle `None` gracefully (skip, log, continue). It retries on `ConnectError`/`TimeoutException` up to `MAX_RETRIES`.
- Ollama calls are serialized intentionally — concurrent requests contend for GPU VRAM.

## Frontend
- Single HTML page + HTMX (CDN). No build step, no React.
- `GET /articles` and `GET /feeds` return HTML fragments, not full pages.
- Vote uses `hx-post` / `hx-swap` — no page reloads.
- After clicking Refresh, the index page polls `/status` every 3s and re-fetches `/articles` when `last_pipeline_run_at` advances.
- Reader modal is a `<dialog>`: title (large) → description (medium) → content (regular). `_clean_content` strips a leading body line that duplicates the title or description.

## Tests
```
pytest tests/ --cov=app
```
Ollama and feedparser are mocked — no live services needed. Coverage target is 100%.
