CREATE TABLE IF NOT EXISTS loan_profiles (
 id varchar(40) PRIMARY KEY, tenant_id varchar(80) NOT NULL, case_id varchar(40) NOT NULL,
 product varchar(80) NOT NULL, due_date date NOT NULL, amount_cents integer NOT NULL,
 contact_reference varchar(160) NOT NULL, source_reference varchar(160) NOT NULL,
 snapshot_at timestamp NOT NULL, ledger_baseline integer NOT NULL,
 version integer NOT NULL DEFAULT 1, created_by varchar(80) NOT NULL,
 UNIQUE(tenant_id, case_id), FOREIGN KEY(tenant_id, case_id) REFERENCES cases(tenant_id, case_id)
);
CREATE INDEX IF NOT EXISTS ix_loan_profiles_tenant_id ON loan_profiles(tenant_id);
CREATE TABLE IF NOT EXISTS loan_sessions (
 id varchar(40) PRIMARY KEY, tenant_id varchar(80) NOT NULL, case_id varchar(40) NOT NULL,
 request_key varchar(80) NOT NULL, mode varchar(24) NOT NULL DEFAULT 'sandbox',
 state varchar(24) NOT NULL DEFAULT 'identity_pending', profile_version integer NOT NULL,
 version integer NOT NULL DEFAULT 1, authorized_by varchar(80) NOT NULL,
 created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, promise json NOT NULL DEFAULT '{}',
 UNIQUE(tenant_id, request_key), UNIQUE(tenant_id, id),
 FOREIGN KEY(tenant_id, case_id) REFERENCES cases(tenant_id, case_id)
);
CREATE INDEX IF NOT EXISTS ix_loan_sessions_tenant_id ON loan_sessions(tenant_id);
CREATE TABLE IF NOT EXISTS loan_events (
 id varchar(40) PRIMARY KEY, tenant_id varchar(80) NOT NULL, session_id varchar(40) NOT NULL,
 event_key varchar(80) NOT NULL, payload_digest varchar(64) NOT NULL,
 intent varchar(40) NOT NULL, result json NOT NULL, created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(tenant_id, session_id, event_key),
 FOREIGN KEY(tenant_id, session_id) REFERENCES loan_sessions(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_loan_events_tenant_id ON loan_events(tenant_id);
CREATE INDEX IF NOT EXISTS ix_loan_events_session_id ON loan_events(session_id);
