-- 履衡 AI FulfillOps v0.11.0: durable protection incidents and maker-checker resolution.

CREATE TABLE IF NOT EXISTS protection_incidents (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  case_id varchar(40) NOT NULL,
  source_event_id varchar(120) NOT NULL,
  category varchar(40) NOT NULL,
  priority varchar(4) NOT NULL DEFAULT 'P1',
  reason text NOT NULL,
  opening_digest varchar(64) NOT NULL,
  owner varchar(120) NOT NULL,
  release_policy varchar(40) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'open',
  version integer NOT NULL DEFAULT 1,
  previous_case_status varchar(40) NOT NULL,
  sla_due_at timestamp,
  opened_by varchar(80) NOT NULL,
  opened_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolution_note text,
  evidence_refs json NOT NULL DEFAULT '[]',
  evidence_digest varchar(64),
  proposed_by varchar(80),
  proposed_at timestamp,
  reviewed_by varchar(80),
  reviewed_at timestamp,
  review_note text,
  resolved_at timestamp,
  case_released boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, source_event_id)
);

CREATE INDEX IF NOT EXISTS ix_protection_incidents_tenant_status
  ON protection_incidents (tenant_id, status, opened_at);
CREATE INDEX IF NOT EXISTS ix_protection_incidents_tenant_case
  ON protection_incidents (tenant_id, case_id, status);
