CREATE TABLE IF NOT EXISTS voice_combinations (
 id varchar(40) PRIMARY KEY,
 tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
 name varchar(80) NOT NULL,
 selection json NOT NULL,
 version integer NOT NULL DEFAULT 1,
 connection json NOT NULL DEFAULT '{}',
 enabled_report_id varchar(40), enabled_by varchar(80), active_report_id varchar(40),
 updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_voice_combinations_tenant_id ON voice_combinations(tenant_id);
CREATE TABLE IF NOT EXISTS voice_benchmarks (
 id varchar(40) PRIMARY KEY,
 tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
 combination_id varchar(40) NOT NULL,
 config_version integer NOT NULL,
 comparison_id varchar(40) NOT NULL,
 status varchar(24) NOT NULL DEFAULT 'running',
 result json NOT NULL DEFAULT '{}',
 created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 expires_at timestamp NOT NULL,
 FOREIGN KEY (tenant_id, combination_id) REFERENCES voice_combinations(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_voice_benchmarks_tenant_id ON voice_benchmarks(tenant_id);
CREATE INDEX IF NOT EXISTS ix_voice_benchmarks_comparison_id ON voice_benchmarks(comparison_id);
