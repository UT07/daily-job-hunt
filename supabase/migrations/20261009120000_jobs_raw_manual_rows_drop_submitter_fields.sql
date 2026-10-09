-- Blank the submitter-supplied apply_url and location on MANUAL jobs_raw rows.
--
-- jobs_raw is shared across users and keyed by
-- canonical_hash(company, title, description). apply_url and location are not
-- covered by that hash. Until 2026-10-09, Add Job (/api/pipeline/run-single)
-- and Save & Score (/api/score) wrote the submitter's own apply_url and
-- location onto the shared row, and score_batch copied them into the jobs row
-- of EVERY user who later scored the same JD: the first submitter chose the
-- Apply link (phishing) and the location that drives the geo / work-auth
-- score cap for everyone else. Flagged by a security review as cross-tenant
-- data tampering.
--
-- The code no longer writes these fields on manual rows and no longer READS
-- them from manual rows (shared/jobs_raw_trust.py), so it is safe whether or
-- not this has run. This removes the stale values at rest. A submitter's own
-- values live on their own `jobs` row and are not touched.
--
-- Scraped rows (source <> 'manual') are not touched: their values come from
-- the job board.
--
-- Idempotent: the IS NOT NULL guard means a re-run updates nothing.
--
-- NOT APPLIED by the commit that adds it.
update public.jobs_raw
   set apply_url = null,
       location = null
 where source = 'manual'
   and (apply_url is not null or location is not null);
