CREATE TABLE IF NOT EXISTS loan_sip_dispatches (
 id varchar(40) PRIMARY KEY,
 tenant_id varchar(80) NOT NULL,
 session_id varchar(40) NOT NULL,
 session_version integer NOT NULL,
 instance_id varchar(32) NOT NULL,
 authorized_by varchar(80) NOT NULL,
 state varchar(24) NOT NULL DEFAULT 'prepared' CHECK (state IN ('prepared','dispatching','unknown','submitted','blocked','stop_requested')),
 created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 observation json NOT NULL DEFAULT '{}',
 UNIQUE (tenant_id, session_id),
 FOREIGN KEY (tenant_id, session_id) REFERENCES loan_sessions(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_loan_sip_dispatches_tenant_id ON loan_sip_dispatches(tenant_id);
