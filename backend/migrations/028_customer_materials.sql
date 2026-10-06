CREATE TABLE IF NOT EXISTS customer_materials (
 id varchar(40) PRIMARY KEY,
 tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
 filename varchar(120) NOT NULL,
 source_reference varchar(160) NOT NULL,
 source_digest varchar(64) NOT NULL,
 mapping_digest varchar(64) NOT NULL,
 file_kind varchar(12) NOT NULL,
 size_bytes integer NOT NULL,
 key_version varchar(80) NOT NULL,
 nonce varchar(32) NOT NULL,
 ciphertext text NOT NULL,
 mapping json NOT NULL,
 normalized_rows json NOT NULL,
 created_by varchar(80) NOT NULL,
 created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_customer_material_source_mapping UNIQUE(tenant_id, source_digest, mapping_digest)
);
CREATE INDEX IF NOT EXISTS ix_customer_material_tenant_created ON customer_materials(tenant_id, created_at);
