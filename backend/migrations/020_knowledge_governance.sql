CREATE TABLE IF NOT EXISTS knowledge_documents (
    id varchar(40) PRIMARY KEY,
    tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
    document_key varchar(80) NOT NULL,
    title varchar(160) NOT NULL,
    category varchar(40) NOT NULL,
    source_reference varchar(255) NOT NULL,
    content_digest varchar(64) NOT NULL,
    summary text NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'pending_review',
    version integer NOT NULL,
    proposed_by varchar(80) NOT NULL,
    reviewed_by varchar(80),
    review_note text,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at timestamp,
    CONSTRAINT uq_knowledge_documents_version UNIQUE (tenant_id, document_key, version),
    CONSTRAINT ck_knowledge_documents_status CHECK (status IN ('pending_review','published','rejected','retired'))
);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tenant_status ON knowledge_documents (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_document_key ON knowledge_documents (document_key);
