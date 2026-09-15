-- RepayGuard AI v0.15.0: governed asset and case import batches.

ALTER TABLE asset_packages ADD COLUMN IF NOT EXISTS source_import_batch_id varchar(40);
ALTER TABLE asset_packages ADD COLUMN IF NOT EXISTS created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS contact_basis_ref varchar(120);
ALTER TABLE cases ADD COLUMN IF NOT EXISTS source_import_batch_id varchar(40);
ALTER TABLE cases ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS asset_import_batches (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  idempotency_key varchar(120) NOT NULL,
  source_filename varchar(160) NOT NULL,
  source_digest varchar(64) NOT NULL,
  schema_version varchar(24) NOT NULL DEFAULT 'asset-case-v1',
  status varchar(24) NOT NULL DEFAULT 'ready',
  version integer NOT NULL DEFAULT 1,
  row_count integer NOT NULL DEFAULT 0,
  valid_count integer NOT NULL DEFAULT 0,
  invalid_count integer NOT NULL DEFAULT 0,
  duplicate_count integer NOT NULL DEFAULT 0,
  package_count integer NOT NULL DEFAULT 0,
  total_claim_balance_cents integer NOT NULL DEFAULT 0,
  normalized_rows jsonb NOT NULL DEFAULT '[]'::jsonb,
  issues jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_by varchar(80) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  committed_by varchar(80),
  committed_at timestamp,
  review_note text,
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_asset_packages_import_batch ON asset_packages (source_import_batch_id);
CREATE INDEX IF NOT EXISTS ix_cases_import_batch ON cases (source_import_batch_id);
CREATE INDEX IF NOT EXISTS ix_asset_import_batches_tenant_created
  ON asset_import_batches (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS ix_asset_import_batches_tenant_status
  ON asset_import_batches (tenant_id, status, created_at);
