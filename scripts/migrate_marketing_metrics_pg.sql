-- Маркетинговые метрики: marketing_signal_events + messages.mention_user_ids (PostgreSQL).
--
-- Идемпотентно через Python (нужен DATABASE_URL на Postgres + sqlalchemy, psycopg2-binary):
--   cd /path/to/telegram-political-monitor-bot && .venv/bin/python scripts/apply_marketing_metrics_migration.py
--
-- Ниже — тот же SQL для ручного psql.

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS mention_user_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS marketing_signal_events (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  occurred_at TIMESTAMP NOT NULL,
  sentiment VARCHAR(16) NOT NULL DEFAULT 'neutral',
  is_political BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_mse_chat_time
  ON marketing_signal_events (chat_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_mse_user_chat_time
  ON marketing_signal_events (user_id, chat_id, occurred_at);
