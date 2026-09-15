-- 履衡 AI FulfillOps v0.12.0: governed repayment plans and installment allocations.

ALTER TABLE asset_packages ADD COLUMN IF NOT EXISTS min_settlement_bps integer NOT NULL DEFAULT 7000;
ALTER TABLE asset_packages ADD COLUMN IF NOT EXISTS max_installments integer NOT NULL DEFAULT 6;
ALTER TABLE asset_packages ADD COLUMN IF NOT EXISTS min_down_payment_bps integer NOT NULL DEFAULT 2000;
ALTER TABLE case_financial_profiles ADD COLUMN IF NOT EXISTS claim_balance_cents integer NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS repayment_plans (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  plan_id varchar(80) NOT NULL,
  case_id varchar(40) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'pending_review',
  version integer NOT NULL DEFAULT 1,
  currency varchar(3) NOT NULL DEFAULT 'CNY',
  claim_balance_cents integer NOT NULL,
  total_cents integer NOT NULL,
  down_payment_cents integer NOT NULL,
  installment_count integer NOT NULL,
  policy_version integer NOT NULL,
  policy_snapshot json NOT NULL DEFAULT '{}',
  agreement_reference varchar(120) NOT NULL,
  agreement_digest varchar(64) NOT NULL,
  signed_at timestamp NOT NULL,
  evidence_digest varchar(64) NOT NULL,
  proposal_reason text NOT NULL,
  proposed_by varchar(80) NOT NULL,
  proposed_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_by varchar(80),
  reviewed_at timestamp,
  review_note text,
  activated_at timestamp,
  completed_at timestamp,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, plan_id)
);

CREATE INDEX IF NOT EXISTS ix_repayment_plans_tenant_case_status
  ON repayment_plans (tenant_id, case_id, status);

CREATE TABLE IF NOT EXISTS repayment_installments (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  plan_row_id varchar(40) NOT NULL REFERENCES repayment_plans(id),
  installment_id varchar(120) NOT NULL,
  installment_no integer NOT NULL,
  due_date date NOT NULL,
  due_cents integer NOT NULL,
  paid_cents integer NOT NULL DEFAULT 0,
  status varchar(24) NOT NULL DEFAULT 'scheduled',
  last_payment_at timestamp,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, installment_id),
  UNIQUE (plan_row_id, installment_no)
);

CREATE INDEX IF NOT EXISTS ix_repayment_installments_tenant_due
  ON repayment_installments (tenant_id, due_date, status);

CREATE TABLE IF NOT EXISTS repayment_allocations (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  allocation_id varchar(120) NOT NULL,
  recovery_entry_id varchar(120) NOT NULL,
  plan_row_id varchar(40) NOT NULL REFERENCES repayment_plans(id),
  installment_row_id varchar(40) NOT NULL REFERENCES repayment_installments(id),
  amount_cents integer NOT NULL,
  source_allocation_id varchar(40),
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, allocation_id),
  UNIQUE (tenant_id, recovery_entry_id, installment_row_id)
);

CREATE INDEX IF NOT EXISTS ix_repayment_allocations_tenant_recovery
  ON repayment_allocations (tenant_id, recovery_entry_id);
CREATE INDEX IF NOT EXISTS ix_repayment_allocations_source
  ON repayment_allocations (source_allocation_id);
