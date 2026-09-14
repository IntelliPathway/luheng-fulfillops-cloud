-- 履衡 AI FulfillOps v0.3.1: provider-neutral runtime checkpoints.
CREATE TABLE IF NOT EXISTS agent_runtime_checkpoints (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  session_id varchar(40) NOT NULL REFERENCES agent_sessions(id),
  provider_session_id varchar(120) NOT NULL,
  event_cursor integer NOT NULL DEFAULT 0,
  turn_count integer NOT NULL DEFAULT 0,
  last_run_id varchar(40),
  runtime_metadata json NOT NULL,
  updated_at timestamp NOT NULL,
  UNIQUE (tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS ix_agent_runtime_checkpoints_tenant_session
  ON agent_runtime_checkpoints (tenant_id, session_id);
CREATE INDEX IF NOT EXISTS ix_agent_runtime_checkpoints_provider_session
  ON agent_runtime_checkpoints (provider_session_id);
