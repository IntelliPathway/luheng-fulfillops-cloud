-- Existing sessions intentionally remain NULL and fail closed on their next event.
ALTER TABLE loan_sessions ADD COLUMN IF NOT EXISTS authorization_expires_at timestamp;
