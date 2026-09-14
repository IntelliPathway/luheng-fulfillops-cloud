-- 履衡 AI FulfillOps v0.9.0: signed payment receipts and immutable recovery/commission ledgers.

CREATE TABLE IF NOT EXISTS commission_rules (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  rule_id varchar(80) NOT NULL,
  package_id varchar(40) NOT NULL,
  version integer NOT NULL DEFAULT 1,
  rate_bps integer NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'active',
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, rule_id, version)
);

CREATE TABLE IF NOT EXISTS case_financial_profiles (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  case_id varchar(40) NOT NULL,
  commission_rule_id varchar(80) NOT NULL,
  mandate_start date NOT NULL,
  mandate_end date NOT NULL,
  signed_plan_at date,
  signed_plan_last_due date,
  signed_plan_tail_eligible boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, case_id)
);

CREATE TABLE IF NOT EXISTS payment_webhook_configs (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  provider varchar(12) NOT NULL,
  secret_ref varchar(255) NOT NULL,
  credential_last4 varchar(8) NOT NULL,
  version integer NOT NULL DEFAULT 1,
  active boolean NOT NULL DEFAULT true,
  max_amount_cents integer NOT NULL DEFAULT 100000000,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, provider)
);

CREATE TABLE IF NOT EXISTS payment_receipts (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  provider varchar(12) NOT NULL,
  provider_event_id varchar(120) NOT NULL,
  event_type varchar(24) NOT NULL,
  amount_cents integer NOT NULL,
  currency varchar(3) NOT NULL DEFAULT 'CNY',
  occurred_at timestamp NOT NULL,
  case_id varchar(40),
  original_provider_event_id varchar(120),
  payload_digest varchar(64) NOT NULL,
  signature_digest varchar(64) NOT NULL,
  signature_version varchar(12) NOT NULL DEFAULT 'v1',
  signature_verified boolean NOT NULL DEFAULT true,
  status varchar(24) NOT NULL DEFAULT 'accepted',
  failure_code varchar(80),
  recovery_entry_id varchar(120),
  duplicate_count integer NOT NULL DEFAULT 0,
  received_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS recovery_ledger_entries (
  id varchar(40) PRIMARY KEY,
  entry_id varchar(120) NOT NULL,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  receipt_id varchar(40) UNIQUE,
  case_id varchar(40) NOT NULL,
  package_id varchar(40) NOT NULL,
  event_type varchar(24) NOT NULL,
  amount_cents integer NOT NULL,
  eligible_amount_cents integer NOT NULL,
  commission_rule_id varchar(80) NOT NULL,
  commission_rule_version integer NOT NULL,
  rate_bps integer NOT NULL,
  commission_cents integer NOT NULL,
  reason varchar(40) NOT NULL,
  original_entry_id varchar(120),
  allocation varchar(160) NOT NULL,
  source varchar(120) NOT NULL,
  booked_at timestamp NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, entry_id)
);

CREATE TABLE IF NOT EXISTS commission_ledger_entries (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  event_id varchar(120) NOT NULL,
  event_type varchar(24) NOT NULL,
  amount_cents integer NOT NULL,
  source_recovery_entry_id varchar(120),
  reference varchar(160) NOT NULL,
  idempotency_key varchar(120) NOT NULL,
  payload_digest varchar(64) NOT NULL,
  created_by varchar(80) NOT NULL,
  occurred_at timestamp NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, event_id),
  UNIQUE (tenant_id, idempotency_key),
  UNIQUE (tenant_id, source_recovery_entry_id)
);

CREATE INDEX IF NOT EXISTS ix_commission_rules_tenant_package
  ON commission_rules (tenant_id, package_id, status);
CREATE INDEX IF NOT EXISTS ix_case_financial_profiles_tenant_case
  ON case_financial_profiles (tenant_id, case_id);
CREATE INDEX IF NOT EXISTS ix_payment_webhook_configs_tenant_provider
  ON payment_webhook_configs (tenant_id, provider, active);
CREATE INDEX IF NOT EXISTS ix_payment_receipts_tenant_status
  ON payment_receipts (tenant_id, status, received_at);
CREATE INDEX IF NOT EXISTS ix_recovery_ledger_tenant_booked
  ON recovery_ledger_entries (tenant_id, booked_at);
CREATE INDEX IF NOT EXISTS ix_commission_ledger_tenant_occurred
  ON commission_ledger_entries (tenant_id, occurred_at);
