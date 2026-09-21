ALTER TABLE contact_attempts ADD COLUMN IF NOT EXISTS retry_of_id varchar(40);
ALTER TABLE contact_attempts ADD COLUMN IF NOT EXISTS cancelled_by varchar(80);
ALTER TABLE contact_attempts ADD COLUMN IF NOT EXISTS cancelled_at timestamp;
ALTER TABLE contact_attempts ADD COLUMN IF NOT EXISTS cancel_reason text;
CREATE INDEX IF NOT EXISTS ix_contact_attempts_retry_of_id ON contact_attempts(retry_of_id);
