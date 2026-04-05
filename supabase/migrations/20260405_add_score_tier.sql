-- Phase 2.10: Score-Based Job Tiering
-- Adds score_tier column to jobs, backfills from match_score, and adds a filtering index.
--
-- Tiers (default thresholds from unified grand plan 2026-04-03):
--   S: 90-100  (Must Apply)
--   A: 80-89   (Strong Match)
--   B: 70-79   (Worth Trying)
--   C: 60-69   (Long Shot)
--   D: <60     (Skip)

ALTER TABLE "public"."jobs"
    ADD COLUMN IF NOT EXISTS "score_tier" TEXT
    CHECK ("score_tier" IS NULL OR "score_tier" IN ('S', 'A', 'B', 'C', 'D'));

-- Backfill tier from current match_score for all existing jobs
UPDATE "public"."jobs"
SET "score_tier" = CASE
    WHEN "match_score" >= 90 THEN 'S'
    WHEN "match_score" >= 80 THEN 'A'
    WHEN "match_score" >= 70 THEN 'B'
    WHEN "match_score" >= 60 THEN 'C'
    ELSE 'D'
END
WHERE "match_score" IS NOT NULL;

-- Index for dashboard tier filtering (user_id + tier + score desc)
CREATE INDEX IF NOT EXISTS "idx_jobs_user_tier_score"
    ON "public"."jobs" ("user_id", "score_tier", "match_score" DESC);
