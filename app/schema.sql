PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS feeds (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    url                   TEXT    NOT NULL UNIQUE,
    title                 TEXT,
    last_polled_at        TEXT,                          -- ISO-8601 UTC, every attempt
    last_success_at       TEXT,                          -- ISO-8601 UTC, last 200/304
    last_error            TEXT,                          -- short error message from last failure
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    paused                INTEGER NOT NULL DEFAULT 0,    -- 0/1; 1 = skip this feed
    score_threshold       REAL,                          -- per-feed override of SCORE_THRESHOLD
    etag                  TEXT,                          -- conditional-GET token from last response
    last_modified         TEXT,                          -- conditional-GET timestamp from last response
    tags                  TEXT                           -- comma-separated lowercase tags, e.g. "news,tech"
);

CREATE TABLE IF NOT EXISTS articles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id        INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    guid           TEXT    NOT NULL,
    url            TEXT    NOT NULL,
    title          TEXT    NOT NULL,
    published_at   TEXT,                          -- ISO-8601 UTC from feed
    raw_snippet    TEXT,                          -- stripped <description>/<summary>
    feed_content   TEXT,                          -- stripped <content:encoded>/<content> (richer than summary)
    full_text      TEXT,                          -- trafilatura extracted text (or feed_content fallback)
    summary        TEXT,                          -- LLM 2-3 sentence summary
    score          REAL,                          -- 0.0-1.0 from LLM
    score_reason   TEXT,                          -- one-sentence reason from LLM
    thumbnail_url  TEXT,                          -- image extracted from feed entry
    read_at        TEXT,                          -- ISO-8601 UTC when user first opened the reader
    saved_at       TEXT,                          -- ISO-8601 UTC when user marked the article saved/read-later
    status         TEXT    NOT NULL DEFAULT 'new',
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(feed_id, guid)
);

-- status values: new | scored | hidden | summarized | liked | disliked | dismissed

CREATE TABLE IF NOT EXISTS votes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id     INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    value          INTEGER NOT NULL CHECK(value IN (1, -1)),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS preferences (
    id             INTEGER PRIMARY KEY CHECK(id = 1),  -- single row enforced
    profile_text   TEXT    NOT NULL DEFAULT '',
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT OR IGNORE INTO preferences(id, profile_text) VALUES (1, '');

CREATE TABLE IF NOT EXISTS settings (
    key            TEXT    PRIMARY KEY,
    value          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_score  ON articles(score DESC);
CREATE INDEX IF NOT EXISTS idx_articles_feed   ON articles(feed_id);
CREATE INDEX IF NOT EXISTS idx_votes_article   ON votes(article_id);
