DO $$ BEGIN
  ALTER TABLE asset_packages ADD CONSTRAINT ck_asset_packages_policy_status CHECK (policy_status IN ('draft','published'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE asset_packages ADD CONSTRAINT ck_asset_packages_budget_positive CHECK (budget_limit_yuan > 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE asset_packages ADD CONSTRAINT ck_asset_packages_settlement_bps CHECK (min_settlement_bps BETWEEN 1000 AND 10000);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE asset_packages ADD CONSTRAINT ck_asset_packages_installments CHECK (max_installments BETWEEN 1 AND 60);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE asset_packages ADD CONSTRAINT ck_asset_packages_down_payment_bps CHECK (min_down_payment_bps BETWEEN 0 AND 10000);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE policy_proposals ADD CONSTRAINT ck_policy_proposals_status CHECK (status IN ('pending_review','approved','rejected'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_events ADD CONSTRAINT ck_telephony_event_type CHECK (event_type IN ('initiated','ringing','answered','completed','failed'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE telephony_events ADD CONSTRAINT ck_telephony_status CHECK (status IN ('initiated','ringing','answered','completed','failed'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE commission_rules ADD CONSTRAINT ck_commission_rules_rate_bps CHECK (rate_bps BETWEEN 0 AND 10000);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE case_financial_profiles ADD CONSTRAINT ck_case_financial_profiles_claim_positive CHECK (claim_balance_cents > 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE case_financial_profiles ADD CONSTRAINT ck_case_financial_profiles_mandate_dates CHECK (mandate_start <= mandate_end);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE cases ADD CONSTRAINT fk_cases_tenant_package FOREIGN KEY (tenant_id, package_id) REFERENCES asset_packages (tenant_id, package_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE activities ADD CONSTRAINT fk_activities_tenant_package FOREIGN KEY (tenant_id, package_id) REFERENCES asset_packages (tenant_id, package_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE commission_rules ADD CONSTRAINT fk_commission_rules_tenant_package FOREIGN KEY (tenant_id, package_id) REFERENCES asset_packages (tenant_id, package_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE case_financial_profiles ADD CONSTRAINT fk_case_profiles_tenant_case FOREIGN KEY (tenant_id, case_id) REFERENCES cases (tenant_id, case_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE policy_proposals ADD CONSTRAINT fk_policy_proposals_tenant_package FOREIGN KEY (tenant_id, package_id) REFERENCES asset_packages (tenant_id, package_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
