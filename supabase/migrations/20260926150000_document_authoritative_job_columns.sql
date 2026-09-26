-- Documents which `jobs` columns are authoritative vs legacy.
--
-- The table has accumulated multiple generations of near-duplicate score
-- and artifact columns, with nothing marking which one is current. That
-- ambiguity produced four independent wrong assertions about production
-- state in one session ("scoring is 12x too slow", "1,137 jobs unscored",
-- "all 64 live jobs unscored", "artifact generation is broken") plus an
-- identical mistake by an unrelated health audit — every one caused by
-- querying a plausible-sounding column that no longer carries the live
-- signal. See docs/ROADMAP.md, "Which columns are authoritative", for the
-- full table and tests/unit/test_authoritative_columns.py for the pin.
--
-- COMMENT ON COLUMN only — no schema change, no drops, no backfills.
--
-- Populated counts verified directly against the live database (1,251
-- `jobs` rows) and the "what writes / reads it" claims verified against
-- the code, both on 2026-09-26. Where a count disagreed with an earlier
-- draft of this investigation, the live query won.
--
-- COMMENT ON COLUMN has no IF EXISTS / IF NOT EXISTS form, but this file
-- is idempotent by construction rather than by guard clause: re-running it
-- sets the exact same comment text again, which is a no-op.

BEGIN;

COMMENT ON COLUMN public.jobs.match_score IS
  'AUTHORITATIVE -- the score. Populated on all 1,251 rows. Written by '
  'lambdas/pipeline/score_batch.py (handler, the live Step Functions '
  'scoring path) and by app.py''s on-demand tailoring flow '
  '(_update_job_artifacts). Read by the dashboard (ScoreBadge in '
  'JobTable/Dashboard/JobWorkspace) and by app.py for sorting and '
  'filtering (e.g. the re-tailor queue). Use this, non-null and >0, to '
  'tell whether a job has been scored, and as the score value itself.';

COMMENT ON COLUMN public.jobs.resume_s3_url IS
  'AUTHORITATIVE -- the resume artifact link. Populated on 921/1,251 '
  'rows. Written by lambdas/pipeline/save_job.py (live pipeline, from a '
  'freshly compiled PDF) and by app.py''s _update_job_artifacts '
  '(on-demand tailoring and section-rebuild flows). app.py''s '
  '_refresh_s3_urls regenerates the presigned URL from the stored S3 key '
  'on read so it never shows as expired. Read directly by the dashboard '
  '(JobTable, ResumeEditor, JobWorkspace) as the resume download/preview '
  'link, and gates whether the job-detail Resume tab has content to show.';

COMMENT ON COLUMN public.jobs.cover_letter_s3_url IS
  'AUTHORITATIVE -- the cover letter artifact link. Populated on '
  '658/1,251 rows. Same write and refresh path as resume_s3_url ('
  'save_job.py for the live pipeline, app.py''s _update_job_artifacts for '
  'on-demand generation). Read directly by the dashboard (JobTable, '
  'JobWorkspace) as the cover letter download/preview link, and gates '
  'the job-detail Cover Letter tab.';

COMMENT ON COLUMN public.jobs.score_status IS
  'LEGACY -- do not use. Populated on all 1,251 rows but meaningless as '
  'a signal: 1,140 sit at the column DEFAULT (''pending'') regardless of '
  'whether the job is actually scored, because the live scoring path '
  '(score_batch.py handler) never writes this column. Only the one-off '
  'scripts/rescore_batch.py, scripts/rescore_sample.py and '
  'scripts/score_pending_backlog.py ever set it to ''scored'' (111 '
  'rows). Use match_score (non-null and >0) instead to tell whether a '
  'job is scored.';

COMMENT ON COLUMN public.jobs.score_version IS
  'LEGACY -- do not use. Populated on all 1,251 rows at the column '
  'DEFAULT (1); bumped to 2 only by the same one-off '
  'scripts/rescore_batch.py and scripts/rescore_sample.py, as their own '
  'idempotency marker for "have I rescored this row yet". Not read '
  'anywhere in app.py or web/src. No replacement needed -- nothing in '
  'the live app has a score-version concept.';

COMMENT ON COLUMN public.jobs.final_score IS
  'LEGACY / PARTIAL -- do not use as the score. Populated on only '
  '129/1,251 rows, written by compute_tailored_scores() in '
  'lambdas/pipeline/score_batch.py, a base-vs-tailored delta helper that '
  'runs outside the main scoring handler; where present it duplicates '
  'the tailored resume''s match_score rather than adding new '
  'information. The main handler() never sets it, which is why it is '
  'null for the ~90% of rows scored through the ordinary batch path -- '
  'null here is NOT evidence a job is unscored. web/src/pages/'
  'JobWorkspace.jsx shows it as a supplementary "Final Score" callout '
  'when non-null, but match_score remains the score the rest of the app '
  'reads and sorts on.';

COMMENT ON COLUMN public.jobs.scored_at IS
  'LEGACY -- do not use. Populated on only 111/1,251 rows, written only '
  'by the same one-off rescore/backfill scripts as score_status '
  '(scripts/rescore_batch.py, scripts/rescore_sample.py, '
  'scripts/score_pending_backlog.py). The live scoring handler never '
  'sets it, so a null value does not mean a job is unscored. Use '
  'first_seen / last_seen for recency and match_score for scored-ness.';

COMMENT ON COLUMN public.jobs.tailored_pdf_path IS
  'LEGACY -- do not use. A local filesystem path, populated on only '
  '39/1,251 rows by main.py, the legacy local-run orchestrator (CLAUDE.md: '
  '"retained for local dry-runs only"). Never written by the live Step '
  'Functions pipeline or by app.py''s on-demand flow, and not read '
  'anywhere in web/src. Use resume_s3_url, the link the live pipeline '
  'and dashboard actually use.';

COMMENT ON COLUMN public.jobs.cover_letter_pdf_path IS
  'LEGACY -- do not use. Same pattern as tailored_pdf_path: a local '
  'filesystem path written only by the legacy main.py orchestrator '
  '(35/1,251 rows), never by the live pipeline or app.py, never read in '
  'web/src. Use cover_letter_s3_url instead.';

COMMENT ON COLUMN public.jobs.resume_doc_url IS
  'LEGACY -- do not use. A Google Docs link from the pre-LaTeX design '
  'that was tried and reverted (CLAUDE.md: "LaTeX over Google Docs"). '
  'Populated on only 12/1,251 older rows. Read in exactly one place '
  '(web/src/components/JobTable.jsx) as a fallback display link when '
  'resume_s3_url is not a valid URL; not written by any current code '
  'path. Use resume_s3_url as the primary artifact link.';

COMMIT;
