-- 履衡 AI FulfillOps v0.3.0: identity, durable jobs and Agent Gateway persistence.

CREATE TABLE IF NOT EXISTS users (
  id varchar(80) PRIMARY KEY,
  email varchar(255) NOT NULL UNIQUE,
  display_name varchar(120) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'active',
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  user_id varchar(80) NOT NULL REFERENCES users(id),
  role varchar(24) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'active',
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS async_jobs (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  kind varchar(40) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'queued',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  result jsonb,
  error text,
  attempt integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  idempotency_key varchar(120),
  created_by varchar(80) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at timestamp,
  completed_at timestamp,
  UNIQUE (tenant_id, kind, idempotency_key)
);

CREATE TABLE IF NOT EXISTS agent_sessions (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  created_by varchar(80) NOT NULL,
  scope_type varchar(24) NOT NULL DEFAULT 'global',
  scope_id varchar(80),
  runtime_provider varchar(120) NOT NULL,
  runtime_profile varchar(120) NOT NULL,
  title varchar(160) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_messages (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  session_id varchar(40) NOT NULL REFERENCES agent_sessions(id),
  role varchar(24) NOT NULL,
  content text NOT NULL,
  structured jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  session_id varchar(40) NOT NULL REFERENCES agent_sessions(id),
  job_id varchar(40) REFERENCES async_jobs(id),
  provider varchar(120) NOT NULL,
  profile varchar(120) NOT NULL,
  status varchar(24) NOT NULL,
  tool_trace jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  error text,
  started_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at timestamp
);

CREATE TABLE IF NOT EXISTS agent_proposals (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  session_id varchar(40) NOT NULL REFERENCES agent_sessions(id),
  message_id varchar(40) NOT NULL REFERENCES agent_messages(id),
  action_type varchar(80) NOT NULL,
  arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
  status varchar(24) NOT NULL DEFAULT 'pending',
  required_role varchar(24) NOT NULL DEFAULT 'operator',
  expires_at timestamp NOT NULL,
  decided_at timestamp,
  decided_by varchar(80)
);

CREATE TABLE IF NOT EXISTS business_metric_snapshots (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  metric_key varchar(80) NOT NULL,
  value double precision NOT NULL,
  unit varchar(24) NOT NULL DEFAULT 'yuan',
  source varchar(120) NOT NULL,
  as_of timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, metric_key)
);

CREATE INDEX IF NOT EXISTS ix_tenant_memberships_user ON tenant_memberships (user_id, tenant_id);
CREATE INDEX IF NOT EXISTS ix_async_jobs_tenant_status ON async_jobs (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_sessions_tenant_actor ON agent_sessions (tenant_id, created_by, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_messages_session_created ON agent_messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_agent_runs_session_created ON agent_runs (session_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_proposals_tenant_status ON agent_proposals (tenant_id, status, expires_at);
CREATE INDEX IF NOT EXISTS ix_business_metrics_tenant ON business_metric_snapshots (tenant_id, metric_key);
