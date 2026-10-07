-- One stored answer to "is this resume any good".
--
-- Four checks already ran on every generated resume and none of them were
-- aggregated anywhere: page count (shared.page_check), composition rules
-- (shared.composition_policy), ATS extraction (shared.ats_extract_check) and
-- writing quality (tailor_resume._quality_warnings). The last of those was the
-- worst case -- computed, logged at WARNING, used to choose between two
-- attempts, then dropped before the return -- so no column, dashboard or alarm
-- could see it at all.
--
-- jsonb rather than a grade column plus four boolean columns, for two reasons:
--   * the reasons travel with the grade. A row that says only 'fail' sends the
--     reader back to the logs, and the logs are what this replaces.
--   * `measured` is stored per check, so "we checked and it is clean" and "we
--     never checked" stay distinguishable in the data. Collapsing them is how
--     a no-op run reports success (CLAUDE.md #2), and a boolean column cannot
--     hold three states without a nullable that reads as a mistake.
--
-- Shape, written by lambdas/pipeline/save_job.py via shared/resume_verdict.py:
--   {
--     "grade": "pass" | "warn" | "fail" | "unmeasured",
--     "reasons": ["pages: 4 pages, expected 2", ...],
--     "checks": {
--       "pages":       {"measured": true, "blocking": true,  "violations": []},
--       "composition": {"measured": true, "blocking": true,  "violations": []},
--       "ats":         {"measured": true, "blocking": true,  "violations": []},
--       "writing":     {"measured": true, "blocking": false, "violations": []}
--     }
--   }
--
-- NULL means no verdict has been recorded for this row yet, which is a third
-- thing again from 'unmeasured' -- that one is a verdict, reached by a compile
-- that ran and could measure nothing. save_job writes the column only when a
-- compile actually ran, so a cover-letter-only pass cannot overwrite a real
-- verdict with a worse one.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_verdict jsonb;

COMMENT ON COLUMN jobs.resume_verdict IS
  'Aggregate quality verdict for the generated resume: grade (pass/warn/fail/unmeasured), reasons, and per-check measured/blocking/violations. Written by save_job from shared/resume_verdict.py. NULL = never recorded; "unmeasured" = a compile ran and a check did not.';

-- Partial, because the question asked of this column is almost always "which
-- resumes are not good" -- and on a healthy corpus that is the small minority.
-- Indexing every row to find the few would be most of the table.
CREATE INDEX IF NOT EXISTS idx_jobs_resume_verdict_grade
  ON jobs ((resume_verdict->>'grade'))
  WHERE resume_verdict->>'grade' IN ('fail', 'unmeasured');
