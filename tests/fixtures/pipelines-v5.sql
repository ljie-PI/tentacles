-- Frozen schema/data generated via Service at a5c3224 (before independent consumption).
BEGIN TRANSACTION;
CREATE TABLE evaluations (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        passed INTEGER NOT NULL, score REAL NOT NULL, tags TEXT NOT NULL, reason TEXT NOT NULL,
        stage TEXT NOT NULL, stage_scores TEXT NOT NULL, evaluated_at TEXT NOT NULL, reported_at TEXT,
        PRIMARY KEY (pipeline_id, item_id, persona_version)
    );
INSERT INTO "evaluations" VALUES('sink','6c4139303a0239f9',1,1,0.5,'[]','no keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 0.5, "tags": [], "reason": "no keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00');
INSERT INTO "evaluations" VALUES('sink','f6c0d20a1b7c5f92',1,1,1.0,'[]','include keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 1.0, "tags": [], "reason": "include keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00');
INSERT INTO "evaluations" VALUES('daily','6c4139303a0239f9',1,1,0.5,'[]','no keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 0.5, "tags": [], "reason": "no keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00',NULL);
INSERT INTO "evaluations" VALUES('daily','f6c0d20a1b7c5f92',1,1,1.0,'[]','include keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 1.0, "tags": [], "reason": "include keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00',NULL);
INSERT INTO "evaluations" VALUES('daily','6c4139303a0239f9',2,1,0.5,'[]','no keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 0.5, "tags": [], "reason": "no keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00',NULL);
INSERT INTO "evaluations" VALUES('daily','f6c0d20a1b7c5f92',2,1,1.0,'[]','include keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 1.0, "tags": [], "reason": "include keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00',NULL);
INSERT INTO "evaluations" VALUES('sink','6c4139303a0239f9',2,1,0.5,'[]','no keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 0.5, "tags": [], "reason": "no keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00',NULL);
INSERT INTO "evaluations" VALUES('sink','f6c0d20a1b7c5f92',2,1,1.0,'[]','include keyword matched','keyword','[{"index": 0, "type": "keyword", "score": 1.0, "tags": [], "reason": "include keyword matched", "error": null}]','2030-01-01T00:00:00.000000+00:00',NULL);
CREATE TABLE items (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', published TEXT,
    fetched_at TEXT NOT NULL, fingerprint TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, external_id)
);
INSERT INTO "items" VALUES('f6c0d20a1b7c5f92','news','rss','https://fixture.test/a','https://fixture.test/a','AI research','','Research','Research',NULL,'2030-01-01T00:00:00.000000+00:00','a67dfddb8e834e2346defa0f265c6abb9e99fbf8','{}');
INSERT INTO "items" VALUES('6c4139303a0239f9','news','rss','https://fixture.test/b','https://fixture.test/b','Garden weather','','Forecast','Forecast',NULL,'2030-01-01T00:00:00.000000+00:00','98ebb4b24530c21a0fca3c9e21f50c8240298b19','{}');
CREATE TABLE persona_versions (
        name TEXT NOT NULL, version INTEGER NOT NULL, profile TEXT NOT NULL,
        tags TEXT NOT NULL, include_keywords TEXT NOT NULL, exclude_keywords TEXT NOT NULL,
        source TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
        tag_status TEXT NOT NULL, tag_error TEXT, PRIMARY KEY (name, version)
    );
INSERT INTO "persona_versions" VALUES('default',1,'AI first','[]','["AI"]','[]','seed','','2030-01-01T00:00:00.000000+00:00','skipped',NULL);
INSERT INTO "persona_versions" VALUES('default',2,'AI first','[]','["AI"]','[]','manual','version 2','2030-01-01T00:00:00.000000+00:00','unchanged',NULL);
CREATE TABLE reports (
        id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, markdown TEXT NOT NULL, item_ids TEXT NOT NULL,
        model TEXT, usage TEXT NOT NULL, cost REAL, path TEXT NOT NULL, created_at TEXT NOT NULL
    );
INSERT INTO "reports" VALUES('legacy-report','daily',1,'digest','daily 2030-01-01','# daily 2030-01-01

Research [1]. Forecast [2].

## Sources

[1] news: AI research https://fixture.test/a
[2] news: Garden weather https://fixture.test/b
','["f6c0d20a1b7c5f92", "6c4139303a0239f9"]','fixture','{"input_tokens":10,"output_tokens":5,"total_tokens":15}',0.01,'/private/legacy/reports/daily/20300101-000000-000000-digest-daily-2030-01-01-legacy-report.md','2030-01-01T00:00:00.000000+00:00');
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL,
    status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    stats TEXT NOT NULL DEFAULT '{}', cost REAL, error TEXT
);
INSERT INTO "runs" VALUES(1,'fetch','news','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"entries": 2, "too_old": 0, "fetched": 2, "new": 2, "dup": 0}',NULL,NULL);
INSERT INTO "runs" VALUES(2,'pipeline','daily','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, "filter_status": "ok", "processor_candidates": 2, "consumed": 2, "model": "fixture"}',0.01,NULL);
INSERT INTO "runs" VALUES(3,'filter','daily','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
INSERT INTO "runs" VALUES(4,'pipeline','sink','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "filter_status": "ok", "processor_candidates": 2, "consumed": 2}',NULL,NULL);
INSERT INTO "runs" VALUES(5,'filter','sink','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
INSERT INTO "runs" VALUES(6,'filter','daily','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
INSERT INTO "runs" VALUES(7,'persona','default','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"version": 2, "tag_status": "unchanged", "tag_error": null}',NULL,NULL);
INSERT INTO "runs" VALUES(8,'filter','daily','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
INSERT INTO "runs" VALUES(9,'filter','sink','ok','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','{"candidates": 2, "evaluated": 2, "stages": [{"index": 0, "type": "keyword", "passed": 2, "rejected": 0, "forwarded": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "cost": null}], "passed": 2, "rejected": 0, "errors": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}',NULL,NULL);
CREATE TABLE source_state (
    source_id TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '{}', last_fetch_at TEXT,
    last_status TEXT, last_error TEXT, total_items INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "source_state" VALUES('news','{"url": "https://fixture.test/rss"}','2030-01-01T00:00:00.000000+00:00','ok',NULL,2);
CREATE TABLE suppressed_items (source_id TEXT NOT NULL, external_id TEXT NOT NULL, PRIMARY KEY (source_id, external_id));
CREATE TABLE task_state (
        kind TEXT NOT NULL, target TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_run_at TEXT, last_status TEXT, last_error TEXT, token TEXT,
        lease_until TEXT, started_at TEXT, run_id INTEGER,
        PRIMARY KEY (kind, target)
    );
INSERT INTO "task_state" VALUES('filter','daily','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('pipeline','daily','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('filter','sink','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('pipeline','sink','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "task_state" VALUES('fetch','news','2030-01-01T00:00:00.000000+00:00','2030-01-01T00:00:00.000000+00:00','ok',NULL,NULL,NULL,NULL,NULL);
CREATE INDEX idx_items_url ON items (url) WHERE url != '';
CREATE INDEX idx_items_fp ON items (fingerprint);
CREATE INDEX idx_items_time ON items (COALESCE(published, fetched_at));
CREATE INDEX idx_items_fetched ON items (fetched_at);
CREATE INDEX idx_evaluations_pending ON evaluations (pipeline_id, persona_version, passed, reported_at, score DESC);
CREATE INDEX idx_reports_time ON reports (created_at DESC, id DESC);
CREATE INDEX idx_reports_pipeline ON reports (pipeline_id, created_at DESC, id DESC);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('runs',9);
COMMIT;
PRAGMA user_version = 5;
