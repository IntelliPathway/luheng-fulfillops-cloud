CREATE TABLE IF NOT EXISTS harness_plugins (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    plugin_key varchar(80) NOT NULL,
    display_name varchar(120) NOT NULL,
    protocol varchar(32) NOT NULL,
    entrypoint_reference varchar(255) NOT NULL,
    capabilities json NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'registered',
    version integer NOT NULL DEFAULT 1,
    manifest_digest varchar(64) NOT NULL,
    verification_digest varchar(64),
    registered_by varchar(80) NOT NULL,
    verified_by varchar(80),
    approved_by varchar(80),
    registered_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at timestamp,
    approved_at timestamp,
    CONSTRAINT uq_harness_plugins_key UNIQUE (tenant_id, plugin_key),
    CONSTRAINT ck_harness_plugins_protocol CHECK (protocol IN ('native','python-sdk','json-rpc','contract-adapter')),
    CONSTRAINT ck_harness_plugins_status CHECK (status IN ('registered','verified','enabled','disabled'))
);
CREATE INDEX IF NOT EXISTS ix_harness_plugins_tenant_status ON harness_plugins (tenant_id, status);
