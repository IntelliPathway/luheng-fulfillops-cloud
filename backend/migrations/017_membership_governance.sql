CREATE TABLE IF NOT EXISTS membership_proposals (
  id varchar(40) PRIMARY KEY,
  tenant_id varchar(40) NOT NULL REFERENCES tenants(id),
  target_user_id varchar(80) NOT NULL REFERENCES users(id),
  requested_role varchar(24) NOT NULL,
  requested_status varchar(24) NOT NULL,
  expected_role varchar(24),
  expected_status varchar(24),
  status varchar(24) NOT NULL DEFAULT 'pending_review',
  version integer NOT NULL DEFAULT 1,
  proposed_by varchar(80) NOT NULL,
  proposal_reason text NOT NULL,
  reviewed_by varchar(80),
  review_note text,
  created_at timestamp NOT NULL,
  reviewed_at timestamp,
  CONSTRAINT ck_membership_proposals_status CHECK (status IN ('pending_review','approved','rejected')),
  CONSTRAINT ck_membership_proposals_role CHECK (requested_role IN ('viewer','operator','admin')),
  CONSTRAINT ck_membership_proposals_member_status CHECK (requested_status IN ('active','inactive'))
);
CREATE INDEX IF NOT EXISTS ix_membership_proposals_tenant_status ON membership_proposals(tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_membership_proposals_target_user ON membership_proposals(target_user_id);
