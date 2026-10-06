CREATE TABLE IF NOT EXISTS material_associations (
 id varchar(40) PRIMARY KEY,
 tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
 material_id varchar(40) NOT NULL REFERENCES customer_materials(id),
 case_id varchar(40) NOT NULL,
 evidence_digest varchar(64) NOT NULL,
 status varchar(24) NOT NULL DEFAULT 'pending_review',
 version integer NOT NULL DEFAULT 1,
 proposed_by varchar(80) NOT NULL,
 reviewed_by varchar(80),
 decision_reference varchar(160),
 created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
 reviewed_at timestamp,
 CONSTRAINT uq_material_association UNIQUE(tenant_id, material_id, case_id),
 CONSTRAINT ck_material_association_status CHECK(status IN ('pending_review','approved','rejected'))
);
