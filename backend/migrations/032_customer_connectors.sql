CREATE TABLE IF NOT EXISTS customer_connectors (
 id VARCHAR(40) PRIMARY KEY, tenant_id VARCHAR(80) NOT NULL REFERENCES tenants(id),
 name VARCHAR(80) NOT NULL, endpoint VARCHAR(500) NOT NULL, mapping JSON NOT NULL,
 secret_ref VARCHAR(500) NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 enabled BOOLEAN NOT NULL DEFAULT FALSE, interval_minutes INTEGER NOT NULL DEFAULT 60,
 cursor VARCHAR(500) NOT NULL DEFAULT '', tested_at TIMESTAMP, next_sync_at TIMESTAMP,
 active_job_id VARCHAR(40) REFERENCES async_jobs(id), created_by VARCHAR(80) NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_customer_connector_due ON customer_connectors(enabled,next_sync_at);
