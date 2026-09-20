CREATE TABLE IF NOT EXISTS contact_attempts (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  case_id varchar(40) NOT NULL,
  activity_id varchar(40),
  channel varchar(24) NOT NULL DEFAULT 'phone',
  contact_reference varchar(160) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'queued',
  scheduled_at timestamp NOT NULL,
  requested_by varchar(80) NOT NULL,
  handoff_reason text,
  handoff_requested_by varchar(80),
  handoff_requested_at timestamp,
  last_event_at timestamp,
  created_at timestamp NOT NULL,
  CONSTRAINT fk_contact_attempts_tenant_case FOREIGN KEY (tenant_id, case_id) REFERENCES cases(tenant_id, case_id),
  CONSTRAINT ck_contact_attempts_status CHECK (status IN ('queued','initiated','ringing','answered','completed','failed','handoff','blocked'))
);
CREATE INDEX IF NOT EXISTS ix_contact_attempts_tenant_case_scheduled ON contact_attempts(tenant_id, case_id, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_contact_attempts_tenant_status ON contact_attempts(tenant_id, status, scheduled_at);
