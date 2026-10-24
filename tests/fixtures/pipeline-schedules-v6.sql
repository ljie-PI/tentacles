-- Frozen schema6 at 7a921a5: a manual filter ran, the cron pipeline never ran.
BEGIN TRANSACTION;
CREATE TABLE evaluations (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        passed INTEGER NOT NULL, score REAL NOT NULL, tags TEXT NOT NULL, reason TEXT NOT NULL,
        stage TEXT NOT NULL, stage_scores TEXT NOT NULL, evaluated_at TEXT NOT NULL, reported_at TEXT,
        PRIMARY KEY (pipeline_id, item_id, persona_version)
    );
CREATE TABLE items (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', published TEXT,
    fetched_at TEXT NOT NULL, fingerprint TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, external_id)
);
CREATE TABLE persona_versions (
        name TEXT NOT NULL, version INTEGER NOT NULL, profile TEXT NOT NULL,
        tags TEXT NOT NULL, include_keywords TEXT NOT NULL, exclude_keywords TEXT NOT NULL,
        source TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
        tag_status TEXT NOT NULL, tag_error TEXT, PRIMARY KEY (name, version)
    );
INSERT INTO "persona_versions" VALUES('default',1,'Legacy interests','[]','[]','[]','seed','','2030-01-01T00:10:00.000000+00:00','skipped',NULL);
CREATE TABLE pipeline_consumption (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, consumed_at TEXT NOT NULL,
        report_id TEXT, PRIMARY KEY (pipeline_id, item_id)
    );
CREATE TABLE reports (
        id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, markdown TEXT NOT NULL, item_ids TEXT NOT NULL,
        model TEXT, usage TEXT NOT NULL, cost REAL, path TEXT NOT NULL, created_at TEXT NOT NULL
    );
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL,
    status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    stats TEXT NOT NULL DEFAULT '{}', cost REAL, error TEXT
);
INSERT INTO "runs" VALUES(1,'filter','daily','ok','2030-01-01T00:10:00.000000+00:00','2030-01-01T00:10:00.000000+00:00','{"candidates": 0, "evaluated": 0, "stages": [], "passed": 0, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
CREATE TABLE source_state (
    source_id TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '{}', last_fetch_at TEXT,
    last_status TEXT, last_error TEXT, total_items INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE suppressed_items (source_id TEXT NOT NULL, external_id TEXT NOT NULL, PRIMARY KEY (source_id, external_id));
CREATE TABLE task_state (
        kind TEXT NOT NULL, target TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_run_at TEXT, last_status TEXT, last_error TEXT, token TEXT,
        lease_until TEXT, started_at TEXT, run_id INTEGER,
        PRIMARY KEY (kind, target)
    );
INSERT INTO "task_state" VALUES('filter','daily','2030-01-01T00:10:00.000000+00:00','2030-01-01T00:10:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('pipeline','daily','2030-01-01T00:10:00.000000+00:00',NULL,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('fetch','news','2030-01-01T00:10:00.000000+00:00',NULL,NULL,NULL,NULL,NULL,NULL,NULL);
CREATE INDEX idx_items_url ON items (url) WHERE url != '';
CREATE INDEX idx_items_fp ON items (fingerprint);
CREATE INDEX idx_items_time ON items (COALESCE(published, fetched_at));
CREATE INDEX idx_items_fetched ON items (fetched_at);
CREATE INDEX idx_evaluations_pending ON evaluations (pipeline_id, persona_version, passed, reported_at, score DESC);
CREATE INDEX idx_reports_time ON reports (created_at DESC, id DESC);
CREATE INDEX idx_reports_pipeline ON reports (pipeline_id, created_at DESC, id DESC);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('runs',1);
COMMIT;
PRAGMA user_version = 6;
