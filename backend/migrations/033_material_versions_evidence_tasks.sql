CREATE TABLE IF NOT EXISTS material_series (
 id VARCHAR(40) PRIMARY KEY, tenant_id VARCHAR(40) NOT NULL REFERENCES tenants(id),
 root_id VARCHAR(40) NOT NULL REFERENCES customer_materials(id), latest_id VARCHAR(40) NOT NULL REFERENCES customer_materials(id),
 version INTEGER NOT NULL, CONSTRAINT uq_material_series_root UNIQUE(tenant_id,root_id)
);
CREATE TABLE IF NOT EXISTS material_revisions (
 id VARCHAR(40) PRIMARY KEY, tenant_id VARCHAR(40) NOT NULL REFERENCES tenants(id),
 root_id VARCHAR(40) NOT NULL REFERENCES customer_materials(id), material_id VARCHAR(40) NOT NULL REFERENCES customer_materials(id),
 parent_id VARCHAR(40) NOT NULL REFERENCES customer_materials(id), version INTEGER NOT NULL,
 CONSTRAINT uq_material_revision_member UNIQUE(tenant_id,material_id), CONSTRAINT uq_material_revision_version UNIQUE(tenant_id,root_id,version)
);
CREATE TABLE IF NOT EXISTS evidence_tasks (
 id VARCHAR(40) PRIMARY KEY, tenant_id VARCHAR(40) NOT NULL REFERENCES tenants(id),case_id VARCHAR(40) NOT NULL,
 source_id VARCHAR(80) NOT NULL,evidence_digest VARCHAR(64) NOT NULL,title VARCHAR(200) NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'open',version INTEGER NOT NULL DEFAULT 1,
 created_by VARCHAR(80) NOT NULL,resolution_reference VARCHAR(160),created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_evidence_task_source UNIQUE(tenant_id,case_id,evidence_digest,source_id)
);
