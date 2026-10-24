-- Frozen database schema from e2cfc1c, before persistent suppression.

CREATE TABLE items (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', published TEXT,
    fetched_at TEXT NOT NULL, fingerprint TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, external_id)
);
CREATE INDEX idx_items_url ON items (url) WHERE url != '';
CREATE INDEX idx_items_fp ON items (fingerprint);
CREATE INDEX idx_items_time ON items (COALESCE(published, fetched_at));
CREATE INDEX idx_items_fetched ON items (fetched_at);
CREATE TABLE source_state (
    source_id TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '{}', last_fetch_at TEXT,
    last_status TEXT, last_error TEXT, total_items INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL,
    status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    stats TEXT NOT NULL DEFAULT '{}', cost REAL, error TEXT
);

INSERT INTO items (id, source_id, kind, external_id, url, title, fetched_at, fingerprint)
VALUES ('old', 'other', 'rss', 'old', 'https://other.example.org/old', 'Old history',
        '2026-10-01T08:00:00.000000+00:00', 'old-fingerprint');
INSERT INTO source_state (source_id, cursor, total_items)
VALUES ('news', '{"url":"https://news.example.com/rss","_suppressed":["remembered-guid"]}', 0),
       ('other', '{}', 999);
PRAGMA user_version = 1;
