CREATE TABLE IF NOT EXISTS strategy_experiments (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    package_id varchar(40) NOT NULL,
    name varchar(120) NOT NULL,
    hypothesis text NOT NULL,
    control_policy_version integer NOT NULL,
    candidate_policy_version integer NOT NULL,
    allocation_bps integer NOT NULL,
    success_metric varchar(40) NOT NULL DEFAULT 'confirmed_recovery_rate',
    status varchar(24) NOT NULL DEFAULT 'draft',
    version integer NOT NULL DEFAULT 1,
    created_by varchar(80) NOT NULL,
    started_by varchar(80),
    stopped_by varchar(80),
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at timestamp,
    stopped_at timestamp,
    CONSTRAINT fk_strategy_experiment_package FOREIGN KEY (tenant_id, package_id) REFERENCES asset_packages(tenant_id, package_id),
    CONSTRAINT ck_strategy_experiments_status CHECK (status IN ('draft','running','stopped')),
    CONSTRAINT ck_strategy_experiments_allocation CHECK (allocation_bps BETWEEN 100 AND 5000)
);
CREATE INDEX IF NOT EXISTS ix_strategy_experiments_tenant_status ON strategy_experiments (tenant_id, status, created_at);
