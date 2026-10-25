-- Frozen Service snapshot from master 1ed40e1f4d5f66fe648caaa9e3522b6e2f62e552 (schema v8).
BEGIN TRANSACTION;
CREATE TABLE evaluations (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        passed INTEGER NOT NULL, score REAL NOT NULL, tags TEXT NOT NULL, reason TEXT NOT NULL,
        stage TEXT NOT NULL, stage_scores TEXT NOT NULL, evaluated_at TEXT NOT NULL, reported_at TEXT,
        PRIMARY KEY (pipeline_id, item_id, persona_version)
    );
INSERT INTO "evaluations" VALUES('old-news','20d2aa73101d4589',1,1,1.0,'[]','no filtering stages configured','none','[]','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00');
CREATE TABLE feedback (
        id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, persona TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK (rating IN (-1, 1)), note TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
CREATE TABLE items (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', published TEXT,
    fetched_at TEXT NOT NULL, fingerprint TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, external_id)
);
INSERT INTO "items" VALUES('20d2aa73101d4589','rss','rss','https://feed.example.test/legacy','https://news.example.test/index','Service status 2026-10-25','','Legacy article body','Legacy article body',NULL,'2026-10-25T12:00:00.000000+00:00','4caf14bc12de8aff476750b82ac4e448c91d068e','{}');
CREATE TABLE persona_versions (
        name TEXT NOT NULL, version INTEGER NOT NULL, profile TEXT NOT NULL,
        tags TEXT NOT NULL, include_keywords TEXT NOT NULL, exclude_keywords TEXT NOT NULL,
        source TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
        tag_status TEXT NOT NULL, tag_error TEXT, PRIMARY KEY (name, version)
    );
INSERT INTO "persona_versions" VALUES('default',1,'Legacy interests','[]','[]','[]','seed','','2026-10-25T12:00:00.000000+00:00','skipped',NULL);
INSERT INTO "persona_versions" VALUES('default',2,'Legacy updated interests','[]','[]','[]','manual','','2026-10-25T12:00:00.000000+00:00','skipped',NULL);
CREATE TABLE pipeline_consumption (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, consumed_at TEXT NOT NULL,
        report_id TEXT, PRIMARY KEY (pipeline_id, item_id)
    );
INSERT INTO "pipeline_consumption" VALUES('old-news','20d2aa73101d4589','2026-10-25T12:00:00.000000+00:00',NULL);
CREATE TABLE pipeline_schedule_state (
        kind TEXT NOT NULL CHECK (kind IN ('filter', 'pipeline')), target TEXT NOT NULL,
        active INTEGER NOT NULL CHECK (active IN (0, 1)), PRIMARY KEY (kind, target)
    );
INSERT INTO "pipeline_schedule_state" VALUES('filter','old-news',0);
INSERT INTO "pipeline_schedule_state" VALUES('pipeline','old-news',1);
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
INSERT INTO "runs" VALUES(1,'fetch','rss','ok','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','{"entries": 1, "too_old": 0, "fetched": 1, "new": 1, "dup": 0}',NULL,NULL);
INSERT INTO "runs" VALUES(2,'pipeline','old-news','ok','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','{"candidates": 1, "evaluated": 1, "stages": [], "passed": 1, "rejected": 0, "errors": 0, "cost": null, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "filter_status": "ok", "filter_run_id": 3, "processor_candidates": 1, "consumed": 1}',NULL,NULL);
INSERT INTO "runs" VALUES(3,'filter','old-news','ok','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','{"candidates": 1, "evaluated": 1, "stages": [], "passed": 1, "rejected": 0, "errors": 0, "cost": null, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
INSERT INTO "runs" VALUES(4,'persona','default','ok','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','{"version": 2, "tag_status": "skipped", "tag_error": null}',NULL,NULL);
CREATE TABLE source_state (
    source_id TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '{}', last_fetch_at TEXT,
    last_status TEXT, last_error TEXT, total_items INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "source_state" VALUES('rss','{"url": "https://feed.example.test/rss"}','2026-10-25T12:00:00.000000+00:00','ok',NULL,1);
CREATE TABLE suppressed_items (source_id TEXT NOT NULL, external_id TEXT NOT NULL, PRIMARY KEY (source_id, external_id));
CREATE TABLE task_state (
        kind TEXT NOT NULL, target TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_run_at TEXT, last_status TEXT, last_error TEXT, token TEXT,
        lease_until TEXT, started_at TEXT, run_id INTEGER,
        PRIMARY KEY (kind, target)
    );
INSERT INTO "task_state" VALUES('filter','old-news','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('pipeline','old-news','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('fetch','rss','2026-10-25T12:00:00.000000+00:00','2026-10-25T12:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
CREATE INDEX idx_items_url ON items (url) WHERE url != '';
CREATE INDEX idx_items_fp ON items (fingerprint);
CREATE INDEX idx_items_time ON items (COALESCE(published, fetched_at));
CREATE INDEX idx_items_fetched ON items (fetched_at);
CREATE INDEX idx_evaluations_pending ON evaluations (pipeline_id, persona_version, passed, reported_at, score DESC);
CREATE INDEX idx_reports_time ON reports (created_at DESC, id DESC);
CREATE INDEX idx_reports_pipeline ON reports (pipeline_id, created_at DESC, id DESC);
CREATE INDEX feedback_persona_time ON feedback (persona, created_at, id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('runs',4);
COMMIT;
PRAGMA user_version=8;
