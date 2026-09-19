ALTER TABLE case_financial_profiles ADD COLUMN IF NOT EXISTS principal_cents integer;
ALTER TABLE case_financial_profiles ADD COLUMN IF NOT EXISTS interest_cents integer;
ALTER TABLE case_financial_profiles ADD COLUMN IF NOT EXISTS fee_cents integer;
ALTER TABLE case_financial_profiles ADD COLUMN IF NOT EXISTS first_overdue_date date;
ALTER TABLE case_financial_profiles ADD COLUMN IF NOT EXISTS last_contact_at timestamp;
DO $$ BEGIN ALTER TABLE case_financial_profiles ADD CONSTRAINT ck_case_financial_profiles_principal CHECK (principal_cents IS NULL OR principal_cents >= 0); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE case_financial_profiles ADD CONSTRAINT ck_case_financial_profiles_interest CHECK (interest_cents IS NULL OR interest_cents >= 0); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE case_financial_profiles ADD CONSTRAINT ck_case_financial_profiles_fee CHECK (fee_cents IS NULL OR fee_cents >= 0); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
