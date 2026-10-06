CREATE TABLE IF NOT EXISTS pilot_evidence (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    gate_id varchar(24) NOT NULL,
    evidence_reference varchar(160) NOT NULL,
    evidence_digest varchar(64) NOT NULL,
    configuration_digest varchar(64) NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'pending_review',
    version integer NOT NULL DEFAULT 1,
    proposed_by varchar(80) NOT NULL,
    reviewed_by varchar(80),
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at timestamp,
    expires_at timestamp NOT NULL,
    CONSTRAINT ck_pilot_evidence_gate CHECK (gate_id IN ('identity','compliance','providers','recovery')),
    CONSTRAINT ck_pilot_evidence_status CHECK (status IN ('pending_review','approved','rejected'))
);
CREATE INDEX IF NOT EXISTS ix_pilot_evidence_tenant_gate ON pilot_evidence(tenant_id, gate_id, created_at);
