-- resume_bullets.entry — which job or project a bullet came from.
--
-- extract_bullets() tags each bullet with its \section* ("Experience",
-- "Featured Projects") and deliberately skips the \jobentry/\projectentry
-- header lines between a section heading and its itemize block. So the corpus
-- knows a bullet is work experience but not WHOSE.
--
-- Measured 2026-09-29 on the live corpus: 35 bullets, 5,970 characters, and
-- zero occurrences of Clover, Kraken, Purrrfect or NaukriBaba. The achievement
-- sentences survived; every company and project name was discarded.
--
-- That matters for composition. The tailoring prompt receives retrieved
-- bullets as an evidence pool, and a bullet with no employer cannot be placed
-- under the right \jobentry — the model has to guess, or invent. Attribution
-- is what makes a corpus composable rather than a bag of sentences.
--
-- Nullable, because bullets indexed before this migration have no entry to
-- recover and re-indexing is what fills them in.

BEGIN;

ALTER TABLE resume_bullets
  ADD COLUMN IF NOT EXISTS entry TEXT;

COMMENT ON COLUMN resume_bullets.entry IS
  'The job or project this bullet belongs to, from the \jobentry/\projectentry '
  'header above it. NULL for rows indexed before 2026-09-30. Lets a retrieved '
  'bullet be placed under the right employer instead of guessed at.';

COMMIT;
