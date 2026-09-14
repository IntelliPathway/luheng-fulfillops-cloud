-- 履衡 AI FulfillOps v0.8.0: governed model egress and live-provider replay evidence.

ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS model_config_version integer;
ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS model_name varchar(120);
ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS policy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS external_call_count integer NOT NULL DEFAULT 0;
ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS input_tokens integer NOT NULL DEFAULT 0;
ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS output_tokens integer NOT NULL DEFAULT 0;
ALTER TABLE model_replay_runs
  ADD COLUMN IF NOT EXISTS estimated_cost_usd double precision NOT NULL DEFAULT 0;
