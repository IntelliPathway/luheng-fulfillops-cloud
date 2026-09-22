CREATE TABLE IF NOT EXISTS tenant_plans (
    tenant_id varchar(40) PRIMARY KEY REFERENCES tenants(id),
    plan_code varchar(24) NOT NULL DEFAULT 'team',
    status varchar(24) NOT NULL DEFAULT 'active',
    seat_limit integer NOT NULL,
    monthly_run_limit integer NOT NULL,
    monthly_budget_cents integer NOT NULL,
    features json NOT NULL,
    version integer NOT NULL DEFAULT 1,
    updated_by varchar(80) NOT NULL,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_tenant_plans_code CHECK (plan_code IN ('pilot','team','enterprise')),
    CONSTRAINT ck_tenant_plans_status CHECK (status IN ('active','suspended')),
    CONSTRAINT ck_tenant_plans_limits CHECK (seat_limit > 0 AND monthly_run_limit > 0 AND monthly_budget_cents > 0)
);
