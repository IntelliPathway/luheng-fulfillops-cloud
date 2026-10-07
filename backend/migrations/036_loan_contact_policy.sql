CREATE TABLE IF NOT EXISTS loan_contact_policies (
 tenant_id varchar(40) PRIMARY KEY REFERENCES tenants(id), timezone varchar(40) NOT NULL DEFAULT 'Asia/Shanghai',
 window_start_minute integer NOT NULL, window_end_minute integer NOT NULL,
 daily_session_limit integer NOT NULL CHECK (daily_session_limit BETWEEN 1 AND 3),
 snapshot_max_hours integer NOT NULL CHECK (snapshot_max_hours BETWEEN 1 AND 24),
 promise_max_days integer NOT NULL CHECK (promise_max_days BETWEEN 1 AND 30),
 authorization_minutes integer NOT NULL CHECK (authorization_minutes BETWEEN 1 AND 30),
 paused boolean NOT NULL DEFAULT true, authority_reference varchar(160) NOT NULL,
 valid_until timestamp NOT NULL, version integer NOT NULL DEFAULT 1,
 updated_by varchar(80) NOT NULL, updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK (window_start_minute >= 0 AND window_start_minute < window_end_minute AND window_end_minute <= 1440)
);
ALTER TABLE loan_sessions ADD COLUMN IF NOT EXISTS policy_version integer;
ALTER TABLE loan_sessions ADD COLUMN IF NOT EXISTS policy_snapshot json NOT NULL DEFAULT '{}';
