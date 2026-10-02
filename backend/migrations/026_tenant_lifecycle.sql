CREATE TABLE IF NOT EXISTS tenant_lifecycles (
    tenant_id varchar(40) PRIMARY KEY REFERENCES tenants(id),
    stage varchar(24) NOT NULL DEFAULT 'trial',
    region varchar(32) NOT NULL DEFAULT 'ap-southeast-1',
    data_retention_days integer NOT NULL DEFAULT 365,
    trial_ends_at timestamp,
    contract_reference varchar(160),
    customer_success_owner varchar(120),
    version integer NOT NULL DEFAULT 1,
    updated_by varchar(80) NOT NULL,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_tenant_lifecycles_stage CHECK (stage IN ('trial','active','grace','suspended','closed')),
    CONSTRAINT ck_tenant_lifecycles_retention CHECK (data_retention_days BETWEEN 30 AND 3650)
);

CREATE TABLE IF NOT EXISTS tenant_lifecycle_proposals (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    current_stage varchar(24) NOT NULL,
    target_stage varchar(24) NOT NULL,
    expected_lifecycle_version integer NOT NULL,
    region varchar(32) NOT NULL,
    data_retention_days integer NOT NULL,
    trial_ends_at timestamp,
    contract_reference varchar(160),
    customer_success_owner varchar(120),
    proposal_reason text NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'pending_review',
    version integer NOT NULL DEFAULT 1,
    proposed_by varchar(80) NOT NULL,
    reviewed_by varchar(80),
    review_note text,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at timestamp,
    CONSTRAINT ck_tenant_lifecycle_proposals_target CHECK (target_stage IN ('trial','active','grace','suspended','closed')),
    CONSTRAINT ck_tenant_lifecycle_proposals_status CHECK (status IN ('pending_review','approved','rejected'))
);
CREATE INDEX IF NOT EXISTS ix_tenant_lifecycle_proposals_status ON tenant_lifecycle_proposals (tenant_id, status, created_at);
