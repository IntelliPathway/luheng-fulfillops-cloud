-- 履衡 AI FulfillOps v0.2.0 PostgreSQL baseline.
-- 生产部署应由迁移工具以事务方式执行；API 中 create_all 仅服务开发环境。

CREATE TABLE IF NOT EXISTS tenants (
  id varchar(40) PRIMARY KEY,
  name varchar(120) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asset_packages (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  package_id varchar(40) NOT NULL,
  title varchar(120) NOT NULL,
  policy_status varchar(24) NOT NULL DEFAULT 'published',
  policy_version integer NOT NULL DEFAULT 1,
  budget_limit_yuan double precision NOT NULL DEFAULT 30,
  UNIQUE (tenant_id, package_id)
);

CREATE TABLE IF NOT EXISTS cases (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  case_id varchar(40) NOT NULL,
  package_id varchar(40) NOT NULL,
  status varchar(40) NOT NULL,
  blocked boolean NOT NULL DEFAULT false,
  has_signed_plan boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, case_id)
);

CREATE TABLE IF NOT EXISTS service_configs (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  service_type varchar(24) NOT NULL,
  provider varchar(120) NOT NULL,
  settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  secret_ref varchar(255),
  credential_last4 varchar(8),
  version integer NOT NULL DEFAULT 1,
  saved boolean NOT NULL DEFAULT true,
  connected boolean NOT NULL DEFAULT false,
  latency_ms integer,
  connection_tested_at timestamp,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, service_type)
);

CREATE TABLE IF NOT EXISTS integration_states (
  tenant_id varchar(40) PRIMARY KEY REFERENCES tenants(id),
  enabled boolean NOT NULL DEFAULT false,
  last_self_test_id varchar(40),
  enabled_at timestamp,
  enabled_by varchar(80),
  enabled_service_versions jsonb,
  invalidated_reason text
);

CREATE TABLE IF NOT EXISTS connection_tests (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  service_type varchar(24) NOT NULL,
  config_version integer NOT NULL,
  status varchar(24) NOT NULL,
  latency_ms integer,
  detail text NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS self_test_reports (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  status varchar(24) NOT NULL,
  service_versions jsonb NOT NULL,
  items jsonb NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at timestamp NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  activity_id varchar(40) NOT NULL,
  name varchar(120) NOT NULL,
  package_id varchar(40) NOT NULL,
  goal varchar(80) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'running',
  mode varchar(24) NOT NULL,
  budget_yuan double precision NOT NULL,
  case_ids jsonb NOT NULL,
  policy_version integer NOT NULL,
  service_snapshot jsonb NOT NULL,
  preflight jsonb NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, activity_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  actor_id varchar(80) NOT NULL,
  action varchar(120) NOT NULL,
  resource_type varchar(80) NOT NULL,
  resource_id varchar(80) NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_cases_tenant_package ON cases (tenant_id, package_id);
CREATE INDEX IF NOT EXISTS ix_service_configs_tenant ON service_configs (tenant_id);
CREATE INDEX IF NOT EXISTS ix_connection_tests_tenant_created ON connection_tests (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_self_test_reports_tenant_created ON self_test_reports (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_activities_tenant_created ON activities (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_created ON audit_events (tenant_id, created_at DESC);
