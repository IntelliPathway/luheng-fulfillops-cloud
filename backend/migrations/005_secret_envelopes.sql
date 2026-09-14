-- 履衡 AI FulfillOps v0.6.0: tenant-scoped envelope encrypted secrets.

CREATE TABLE IF NOT EXISTS managed_secrets (
  id varchar(40) PRIMARY KEY,
  reference varchar(255) NOT NULL UNIQUE,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  service_type varchar(24) NOT NULL,
  ciphertext text NOT NULL,
  nonce varchar(64) NOT NULL,
  algorithm varchar(24) NOT NULL DEFAULT 'A256GCM',
  key_version varchar(80) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'active',
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  retired_at timestamp
);

CREATE INDEX IF NOT EXISTS ix_managed_secrets_tenant_service
  ON managed_secrets (tenant_id, service_type, status);
