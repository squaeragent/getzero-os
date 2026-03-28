-- Session 39: Cache table for engine → website decoupling
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New Query)

CREATE TABLE IF NOT EXISTS engine_cache (
  key text PRIMARY KEY,
  data jsonb NOT NULL,
  updated_at timestamptz DEFAULT now()
);

-- Allow service_role full access (default), anon read-only for website
ALTER TABLE engine_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon can read cache"
  ON engine_cache FOR SELECT
  TO anon
  USING (true);

CREATE POLICY "service_role can upsert cache"
  ON engine_cache FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

-- Seed with empty rows so upserts work immediately
INSERT INTO engine_cache (key, data) VALUES
  ('heat', '{}'),
  ('regime', '{}'),
  ('approaching', '{}'),
  ('brief', '{}'),
  ('health', '{}'),
  ('sessions', '{}'),
  ('collective', '{}')
ON CONFLICT (key) DO NOTHING;
