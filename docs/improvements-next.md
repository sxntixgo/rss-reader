# Planned Improvements — Next

Successor to `docs/improvements.md`. Items here are scoped, not yet started.

## Mobile / Touch UX

- [x] **Swipe-right on an article row → like.** Touch handler on `.article-row` translates the row; ratio >40% of width fires `POST /vote/<id>/1`. Visual: green tint via `.swipe-like-active`.
- [x] **Swipe-left on an article row → dismiss (single article).** Same handler; ratio >40% fires `POST /article/<id>/dismiss` (new single-article dismiss endpoint, gesture-only, no button). Red tint via `.swipe-dismiss-active`.
- [x] **Swipe-down inside the reader modal → close.** `touchstart`/`touchend` on the `<dialog>` dismisses when `dy>80 && |dx|<60`. Coarse-pointer only.
- [x] **Edge-swipe right → "back" out of the reader modal.** `touchstart` near `clientX<=24` arms; `dx>80 && |dy|<60` on release closes the modal. Only when the modal is open.
- [x] **Sidebar collapses to a drawer below ~720px.** Hamburger button toggles a fixed-position sidebar with scrim. Picking a feed auto-closes the drawer.
- [x] **Pull-to-refresh on the article list.** When scrolled to top and not on a row/modal/sidebar, downward drag inflates `.pull-indicator`; release with `dy>80` clicks the Refresh button (reuses the existing poll-watcher loop).
- [x] **Tap-target audit.** Buttons, sidebar feed rows and the collapse caret get a 40×40 minimum on `pointer:coarse` devices.
- [x] **Haptic feedback on like/dismiss** (`navigator.vibrate(10)` — best-effort, Android only). Fires from swipe handlers and pull-to-refresh release.

## Frontend / Backend Split — Should We?

Current stack is Flask SSR + HTMX with a CDN script tag. There is no build step, no JS framework, and no JSON API for the article list. Splitting introduces real cost; only do it if it unlocks something we can't get otherwise.

### Verdict
**Don't split yet.** Add swipe gestures and a drawer sidebar inside the existing HTMX shell first. If after that the UI still feels janky on mobile (mid-swipe full-row HTML swaps causing flicker, modal animations stuttering, no offline support), revisit.

### If we *do* split later, these are the realistic shapes

1. **Hybrid (recommended first step if mobile feels off after gesture work).**
   - Keep Flask SSR for `/`, `/settings`, `/manage-feeds` (fast TTFB, no build).
   - Introduce a JSON API: `GET /api/articles`, `POST /api/vote/<id>/<v>`, `POST /api/dismiss-all`, `GET /api/sidebar/feeds`.
   - Hydrate just `#article-list` and the sidebar with a tiny reactive layer (Preact ~3kB or Solid ~7kB). Modals, vote optimistic-update, swipe state all live client-side.
   - Cost: Vite/esbuild added to the dev loop, maybe 200 lines of TS, two weeks of polish.
   - Win: instant transitions, smooth gestures, easy to wire a service worker for offline read-later.

2. **Full SPA (Svelte/Solid/Preact).** Treat Flask purely as an API. Lose the no-build-step ergonomics. Only justified if we want a "real" PWA with offline-first, push notifications, install prompts on Android. For a single-user self-hosted reader, probably overkill.

3. **Native wrapper (Capacitor).** Wrap the existing site, get App Store distribution and native gesture handlers. Heavy lift; only worth it if the user wants this on their lock screen.

### What splitting buys us
- Optimistic updates (like a vote that animates instantly, reverts on error).
- True gesture animations (swipe with the finger, not after release).
- Service-worker-backed offline read of already-summarized articles.
- One JSON API also makes a future iOS/Android client cheap.

### What splitting costs us
- Dev loop: Vite, source maps, an `npm install` step.
- Two ways to render the same data (server template + client component) — small risk of drift.
- More moving parts to debug for a single-user app.

## Other Improvements

### Polling reliability
- [x] **Conditional GET on poll.** `etag` and `last_modified` columns on `feeds` are forwarded to feedparser and refreshed from each response. A 304 short-circuits to "no new articles" and resets the failure counter.
- [x] **Per-feed health columns.** `last_error`, `last_success_at`, `consecutive_failures` on the `feeds` row, surfaced in `/manage-feeds` with badges and the most recent error message.
- [x] **Auto-pause feeds after N consecutive failures.** Threshold is `AUTO_PAUSE_AFTER_FAILURES = 5`. Pause/Resume buttons in `/manage-feeds`; resuming clears the counter.

### Search & discovery
- [x] **SQLite FTS5 index over `title`, `summary`, `full_text`.** Virtual table + INSERT/UPDATE/DELETE triggers keep it in sync. Search input on the index hits `GET /search?q=...` (debounced 250ms, server-side).
- [x] **Saved / read-later tag.** `articles.saved_at` column, star button on each card (`POST /article/<id>/save` toggles), and a "Saved" group in the sidebar. Doesn't feed the preference profile.
- [x] **Tag system for feeds** (e.g., "tech", "news"). `feeds.tags` stores a normalized comma-separated string; `POST /feeds/<id>/tags` saves inline from `/manage-feeds`. Sidebar `_sidebar_feeds.html` renders one collapsible group per tag (alphabetical), with untagged feeds in their own group at the bottom. Feeds tagged with multiple tags appear under each.

### Personalization
- [x] **Per-feed score threshold override.** `feeds.score_threshold` (nullable) — when set, replaces the global `SCORE_THRESHOLD` for articles from that feed. Editable inline in `/manage-feeds`.
- [x] **"Why was this hidden?"** — `score_reason` is rendered as italic muted text on hidden cards (visible at all times in the Hidden view, not just on hover).
- [ ] **Notification when a high-score article (≥0.8) lands.** Web Notification API; opt-in.

### Visual polish
- [x] **Dark-mode button borders.** Bumped `--border` from `#2a2a2a` to `#3d3d3d` and added explicit transparent borders on `.btn-icon` / `.sidebar-feed` / `.sidebar-collapse` that fill in on hover under `[data-theme="dark"]`.
- [x] **Open-graph thumbnail fallback.** When summarizing, the same HTTP fetch trafilatura uses also extracts `<meta property="og:image">`. If the article had no thumbnail from the feed, the OG image is written to `articles.thumbnail_url`.
- [x] **Favicon badge with unread count.** Two layers: (1) the actual browser-tab favicon is a 32×32 canvas drawn in `base.html` — rounded brand square + "B" mark, plus a red badge circle with the unread count when >0 (renders "99+" past 99). The result is set as a PNG dataURL on `<link id="favicon">`, replacing the inline SVG fallback. (2) For installed PWAs, `navigator.setAppBadge(n)` mirrors the count. Both refresh on initial load, theme toggle, and after vote/poll/dismiss/article-content htmx requests.
- [x] **Render Twitter/X and Instagram embeds in the reader.** Articles often include `<blockquote class="twitter-tweet">` / `<blockquote class="instagram-media">` markers that trafilatura strips to plain text, losing the embedded post. In the reader modal, detect these patterns (or the original `<blockquote>` URLs to `twitter.com|x.com|instagram.com`) and either: (a) inject the official embed scripts (`platform.twitter.com/widgets.js`, `instagram.com/embed.js`) so the blockquote renders as the original card, or (b) call the platform oEmbed endpoints server-side during summarization and store the resulting HTML so the modal renders it without third-party scripts. Option (b) keeps things offline-friendly but Twitter's oEmbed now requires auth — start with (a) gated behind a setting.
- [x] **Animated transition** when an article moves out of the list. `.article-row` has a transition on `background-color` + `opacity`; `dismissAll` adds a `leaving` class that translates and fades the rows before the list refreshes.
- [x] **`prefers-color-scheme` auto-detect.** The inline boot script now respects the OS preference on first visit; an explicit toggle still wins thereafter.

### Sidebar layout
- [x] **Manage Feeds icon next to "All feeds".** Inline SVG pencil icon in the group header.
- [x] **Footer icon row.** Settings (gear) + theme toggle (sun/moon) live side-by-side in `.sidebar-footer` with `aria-label`s. Text "Manage Feeds" / "Settings" nav links removed.
- [x] **Inline SVG icons** — no build step, no CDN, no font dependency.

### Infra
- [x] **Backup script.** `scripts/backup.py` uses Python's `sqlite3 .backup` API (safe with WAL + open writers). Honours `DB_PATH`, `BACKUP_DIR` (default `<DB_PATH dir>/backups`), `KEEP` (default 7). Wire into cron, e.g. `0 4 * * * python3 /app/scripts/backup.py`.
- [x] **Structured logs** (JSON) so the user can grep poll/score timings later. Set `LOG_FORMAT=json` to switch the root logger to single-line JSON via `app.JsonFormatter`. Defaults to the existing human-readable format.
