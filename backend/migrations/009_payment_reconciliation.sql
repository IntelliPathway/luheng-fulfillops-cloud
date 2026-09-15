-- 履衡 AI FulfillOps v0.10.0: maker-checker payment receipt reconciliation.

CREATE TABLE IF NOT EXISTS payment_reconciliations (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  receipt_id varchar(40) NOT NULL UNIQUE REFERENCES payment_receipts(id),
  proposed_case_id varchar(40) NOT NULL,
  candidate_snapshot json NOT NULL DEFAULT '[]',
  evidence_digest varchar(64) NOT NULL,
  reason text NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'pending_review',
  version integer NOT NULL DEFAULT 1,
  proposed_by varchar(80) NOT NULL,
  proposed_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_by varchar(80),
  reviewed_at timestamp,
  review_note text,
  recovery_entry_id varchar(120)
);

CREATE INDEX IF NOT EXISTS ix_payment_reconciliations_tenant_status
  ON payment_reconciliations (tenant_id, status, proposed_at);
CREATE INDEX IF NOT EXISTS ix_payment_reconciliations_tenant_receipt
  ON payment_reconciliations (tenant_id, receipt_id);
