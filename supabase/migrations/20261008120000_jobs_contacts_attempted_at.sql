-- When scrape_contacts last searched for this job's contacts.
--
-- scrape_contacts selects S/A jobs with linkedin_contacts IS NULL, top 15 by
-- score, and only wrote when it found someone. A job with no findable
-- contacts stayed NULL, so every run re-selected the same 15, re-searched them
-- through the paid proxy, found nothing again, and never reached job 16.
--
-- It now stamps this column whenever a search actually ran (found or not)
-- and skips jobs stamped within its retry window (7 days). NULL means "never
-- searched", which is different from "searched, nobody found" -- the reason
-- this is a separate column rather than writing '[]' into linkedin_contacts.
--
-- NOT APPLIED by the commit that adds it. Until it is, scrape_contacts falls
-- back to the old select and returns error "migration_missing: ...".
alter table public.jobs
  add column if not exists contacts_attempted_at timestamptz;
