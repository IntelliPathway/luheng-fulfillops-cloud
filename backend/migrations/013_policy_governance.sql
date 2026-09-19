CREATE TABLE IF NOT EXISTS policy_proposals (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  package_id varchar(40) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'pending_review',
  version integer NOT NULL DEFAULT 1,
  expected_policy_version integer NOT NULL,
  proposed_policy json NOT NULL,
  evaluation json NOT NULL,
  evidence_digest varchar(64) NOT NULL,
  proposed_by varchar(80) NOT NULL,
  proposal_reason text NOT NULL,
  reviewed_by varchar(80),
  review_note text,
  created_at timestamp NOT NULL,
  reviewed_at timestamp
);

CREATE INDEX IF NOT EXISTS ix_policy_proposals_tenant_package
  ON policy_proposals (tenant_id, package_id, created_at);
CREATE INDEX IF NOT EXISTS ix_policy_proposals_tenant_status
  ON policy_proposals (tenant_id, status, created_at);
