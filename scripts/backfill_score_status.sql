-- Repair the two columns that answered confidently and wrongly.
--
-- `score_status` sat at its database default of 'pending' on 1290 of 1401 rows
-- that ALL had a match_score, and `scored_at` was populated on 111 of 1401.
-- Nothing ever wrote either: they are absent from score_batch's job_record.
-- tests/unit/test_mcp_server.py already called them "dead columns nothing
-- reliably writes".
--
-- On 2026-10-07 they misled an audit of this very table into reporting that
-- 92% of jobs had never been scored, that 1078 had expired unscored, and that
-- the daily pipeline was silently doing nothing. None of it was true. A column
-- that is read and never written is worse than an absent one, because an
-- absent column raises an error and a stale one answers.
--
-- score_batch now writes both on insert (this repo, same change). This fixes
-- the rows that predate it.
--
-- SAFETY: scoped to rows that demonstrably HAVE a score. A row with a NULL
-- match_score is genuinely unscored and must keep saying so — backfilling it
-- would recreate the same lie in the other direction.

-- What it will touch, before it touches it.
SELECT
    count(*)                                            AS rows_with_a_score,
    count(*) FILTER (WHERE score_status IS DISTINCT FROM 'scored') AS status_to_fix,
    count(*) FILTER (WHERE scored_at IS NULL)           AS scored_at_to_fix
FROM jobs
WHERE match_score IS NOT NULL;

UPDATE jobs
SET score_status = 'scored',
    -- first_seen, not now(): the row was scored when it was created, and
    -- stamping today would claim 1401 jobs were scored this afternoon —
    -- replacing a silent wrong answer with a loud one.
    scored_at = COALESCE(scored_at, first_seen)
WHERE match_score IS NOT NULL
  AND (score_status IS DISTINCT FROM 'scored' OR scored_at IS NULL);

-- Expect: pending_with_a_score = 0, and unscored = only genuinely unscored rows.
SELECT
    count(*) FILTER (WHERE match_score IS NOT NULL AND score_status <> 'scored') AS pending_with_a_score,
    count(*) FILTER (WHERE match_score IS NOT NULL AND scored_at IS NULL)        AS scored_without_a_timestamp,
    count(*) FILTER (WHERE match_score IS NULL)                                  AS genuinely_unscored,
    count(*)                                                                     AS total
FROM jobs;
