CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS prompt_cases (code TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
 expected_runs INTEGER NOT NULL, config TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS prompt_runs (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 data TEXT NOT NULL, task_violated INTEGER DEFAULT NULL CHECK(task_violated IN (0,1)),
 judged_by TEXT DEFAULT NULL CHECK(judged_by IN ('rule','human'))
);
CREATE INDEX IF NOT EXISTS ix_prompt_runs_experiment ON prompt_runs(experiment_id);
CREATE TABLE IF NOT EXISTS api_lab_runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
INSERT OR IGNORE INTO schema_migrations VALUES (1);
