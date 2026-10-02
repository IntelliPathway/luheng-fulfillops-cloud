CREATE TABLE IF NOT EXISTS usage_events (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    meter varchar(40) NOT NULL,
    quantity integer NOT NULL,
    unit varchar(24) NOT NULL,
    unit_price_cents integer NOT NULL DEFAULT 0,
    amount_cents integer NOT NULL,
    source_type varchar(40) NOT NULL,
    source_id varchar(80) NOT NULL,
    idempotency_key varchar(160) NOT NULL,
    metadata_json json NOT NULL,
    occurred_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_usage_events_idempotency UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT ck_usage_events_quantity_positive CHECK (quantity > 0),
    CONSTRAINT ck_usage_events_amount_nonnegative CHECK (amount_cents >= 0)
);
CREATE INDEX IF NOT EXISTS ix_usage_events_tenant_occurred ON usage_events (tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_usage_events_source_id ON usage_events (source_id);
