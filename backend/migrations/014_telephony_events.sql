CREATE TABLE IF NOT EXISTS telephony_events (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  provider varchar(120) NOT NULL,
  provider_event_id varchar(120) NOT NULL,
  call_reference varchar(120) NOT NULL,
  event_type varchar(24) NOT NULL,
  status varchar(24) NOT NULL,
  occurred_at timestamp NOT NULL,
  case_id varchar(40),
  activity_id varchar(40),
  failure_code varchar(80),
  payload_digest varchar(64) NOT NULL,
  signature_digest varchar(64) NOT NULL,
  duplicate_count integer NOT NULL DEFAULT 0,
  received_at timestamp NOT NULL,
  CONSTRAINT uq_telephony_provider_event UNIQUE (tenant_id, provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS ix_telephony_events_tenant_call ON telephony_events (tenant_id, call_reference, occurred_at);
CREATE INDEX IF NOT EXISTS ix_telephony_events_tenant_status ON telephony_events (tenant_id, status, occurred_at);
