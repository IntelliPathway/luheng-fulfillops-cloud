CREATE TABLE IF NOT EXISTS customer_sync_events (
 id varchar(40) PRIMARY KEY,
 tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
 source_system varchar(80) NOT NULL,
 external_event_id varchar(120) NOT NULL,
 payload_digest varchar(64) NOT NULL,
 key_version varchar(80) NOT NULL,
 nonce varchar(32) NOT NULL,
 ciphertext text NOT NULL,
 job_id varchar(40) REFERENCES async_jobs(id),
 created_by varchar(80) NOT NULL,
 created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_customer_sync_event UNIQUE(tenant_id, source_system, external_event_id)
);
