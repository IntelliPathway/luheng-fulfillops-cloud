-- 履衡 AI FulfillOps v0.5.0: leased database queue and independent workers.

ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS queue_name varchar(40) NOT NULL DEFAULT 'default';
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 100;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS available_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS lease_owner varchar(120);
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS lease_expires_at timestamp;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS heartbeat_at timestamp;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS recovery_count integer NOT NULL DEFAULT 0;
ALTER TABLE async_jobs ADD COLUMN IF NOT EXISTS cancel_requested_at timestamp;

CREATE TABLE IF NOT EXISTS job_workers (
  id varchar(120) PRIMARY KEY,
  status varchar(24) NOT NULL DEFAULT 'starting',
  queues jsonb NOT NULL DEFAULT '[]'::jsonb,
  current_job_id varchar(40),
  version varchar(24) NOT NULL DEFAULT '0.5.0',
  processed_count integer NOT NULL DEFAULT 0,
  failed_count integer NOT NULL DEFAULT 0,
  last_error text,
  started_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  heartbeat_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  stopped_at timestamp
);

CREATE INDEX IF NOT EXISTS ix_async_jobs_queue_claim
  ON async_jobs (queue_name, status, available_at, priority, created_at);
CREATE INDEX IF NOT EXISTS ix_async_jobs_lease_expiry
  ON async_jobs (status, lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_job_workers_heartbeat
  ON job_workers (status, heartbeat_at);
