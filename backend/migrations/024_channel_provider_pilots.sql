CREATE TABLE IF NOT EXISTS channel_provider_pilots (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    channel varchar(16) NOT NULL,
    provider varchar(80) NOT NULL,
    endpoint_origin varchar(255) NOT NULL,
    credential_reference varchar(255) NOT NULL,
    callback_reference varchar(255) NOT NULL,
    mode varchar(16) NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'configured',
    version integer NOT NULL DEFAULT 1,
    evidence_digest varchar(64),
    configured_by varchar(80) NOT NULL,
    tested_by varchar(80),
    approved_by varchar(80),
    configured_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tested_at timestamp,
    approved_at timestamp,
    CONSTRAINT uq_channel_provider_pilots_channel UNIQUE (tenant_id, channel),
    CONSTRAINT ck_channel_provider_pilots_channel CHECK (channel IN ('phone','sms','email')),
    CONSTRAINT ck_channel_provider_pilots_mode CHECK (mode IN ('sandbox','live')),
    CONSTRAINT ck_channel_provider_pilots_status CHECK (status IN ('configured','tested','enabled'))
);
CREATE INDEX IF NOT EXISTS ix_channel_provider_pilots_tenant ON channel_provider_pilots (tenant_id, channel);
