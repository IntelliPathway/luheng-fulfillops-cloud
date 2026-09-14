-- 履衡 AI FulfillOps v0.7.0: auditable deterministic Agent replay acceptance.

CREATE TABLE IF NOT EXISTS model_replay_runs (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  job_id varchar(40) NOT NULL UNIQUE REFERENCES async_jobs(id),
  suite_name varchar(120) NOT NULL,
  suite_version varchar(40) NOT NULL,
  mode varchar(40) NOT NULL DEFAULT 'deterministic-contract',
  provider varchar(120) NOT NULL,
  profile varchar(120) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'queued',
  dataset_digest varchar(64) NOT NULL,
  results jsonb NOT NULL DEFAULT '[]'::jsonb,
  passed_count integer NOT NULL DEFAULT 0,
  failed_count integer NOT NULL DEFAULT 0,
  created_by varchar(80) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at timestamp
);

CREATE INDEX IF NOT EXISTS ix_model_replay_runs_tenant_created
  ON model_replay_runs (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS ix_model_replay_runs_status
  ON model_replay_runs (status);
