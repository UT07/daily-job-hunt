-- Add enabled_sources jsonb to user_search_configs to back the
-- Settings → "Save Sources" toggle UI. Frontend has been sending
-- this field in PUT /api/search-config since the toggle UI shipped,
-- but the backend rejected it with 400 ("No valid fields") because
-- enabled_sources wasn't in app.py:_FIELD_MAP and the column didn't
-- exist. This migration unblocks the save.
--
-- Default value: every active source enabled. Matches the implicit
-- pre-migration behaviour where the pipeline scrapes all sources.
-- Pipeline filtering by enabled_sources is a separate follow-up
-- (each scrape Lambda needs to read user config + early-return if
-- disabled) — this migration only unblocks persistence.

ALTER TABLE user_search_configs
  ADD COLUMN IF NOT EXISTS enabled_sources jsonb
  DEFAULT '["linkedin","indeed","greenhouse","ashby","irish_portals","yc","hn_hiring"]'::jsonb
  NOT NULL;
