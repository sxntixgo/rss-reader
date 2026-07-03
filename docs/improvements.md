# Planned Improvements

## Bug Fixes
- [x] Fix `poll_all_feeds` and `run_pipeline` running synchronously in request handler (blocks Flask worker)
- [x] Ollama timeout (currently 60s) too short for 8b+ models on a remote machine — make it configurable via env var
- [x] Concurrent `/poll` clicks could race the pipeline and re-summarize the same articles — added a process-wide lock
- [x] After Refresh, new articles never appeared without a manual reload — UI now polls `/status` and reloads the article list when a pipeline run completes

## UI / UX
- [x] Add dark mode toggle (☀/☽ button in header, persisted to localStorage)
- [x] Compact article list: flat list with single border-line between items
- [x] In-reader full article view: Read button opens modal with full text
- [x] Unread count showing how many articles are available
- [x] Search / keyword filter (client-side, instant)
- [x] Keyboard shortcuts: `j/k` navigate, `l` like, `x` dismiss, `o` open URL, `r` read
- [x] Read-state tracking — opening an article in the reader marks it `read_at` and the row is visually muted
- [x] Move all controls (Refresh, Mark all read, filter, count, sort toggle) into the sticky header

## Features
- [x] Settings page: select Ollama model from the backend (scoring and summary models)
- [x] Pipeline status endpoint (`/status`) showing last-run time and article counts
- [x] OPML import/export for feeds
- [x] Show current preference profile text in settings so you can verify it reflects your taste

## LLM Quality
- [x] Increase scoring snippet beyond 500 chars (now 2000) — `SCORING_SNIPPET_CHARS` env var
- [x] Preference profile visibility — editable on the settings page; manual `Regenerate from votes` button

## Feed format handling
- [x] Capture full feed body at poll time (handles Atom `<content>` and RSS `<content:encoded>` uniformly via `feedparser.entry.content`) and store in `articles.feed_content`. The summarizer and the reader modal fall back to it when trafilatura can't fetch the live URL.
