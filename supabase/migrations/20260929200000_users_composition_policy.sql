-- users.composition_policy — the rules for composing a resume FROM the corpus.
--
-- The master a user uploads is a corpus: everything about them, three pages or
-- ten. A resume is composed from it per job, and the rules governing that
-- composition are the user's, not the code's. Today they are string literals
-- in lambdas/pipeline/tailor_resume.py:
--
--   :250  "Projects: EXACTLY 3 PROJECTS. No more, no less."
--   :251  'ALWAYS KEEP BOTH "Purrrfect Keys" AND "NaukriBaba"'
--   :268  "The resume MUST be exactly TWO PAGES."
--   :269  "Page 1: ... Clover IT Services (7 bullets), and Seattle Kraken (3 bullets)."
--
-- Two problems with that. It is single-tenant by construction — those are one
-- person's project names compiled into the prompt. And nothing enforces any of
-- it: _validate_macro_arities checks that each \jobentry call has four
-- arguments, and nothing anywhere counts how many \jobentry calls exist. Every
-- one of those lines is a request, not a constraint.
--
-- Shape:
--
--   {
--     "max_experience_entries": 2,
--     "max_projects": 3,
--     "pages": 2,
--     "bullets_per_entry": {"min": 3, "max": 7},
--     "prefer": ["UTA IT Support over Seattle Kraken"],
--     "rename": [{"from": "IT Support", "to": "Web Developer",
--                 "when": "full-stack software engineering"}]
--   }
--
-- The countable keys are enforced by counting the generated document. prefer
-- and rename can only ever be prompt text — there is nothing to count — but
-- they are still the user's durable settings rather than a one-off request,
-- which is the distinction that matters.
--
-- NULL means "use the defaults in shared/composition_policy.py", so this
-- migration changes no behaviour on its own.

BEGIN;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS composition_policy JSONB;

COMMENT ON COLUMN users.composition_policy IS
  'Rules for composing a tailored resume from the corpus: entry caps, page '
  'count, and preference/rename instructions. NULL uses the defaults in '
  'shared/composition_policy.py. Countable keys are enforced by counting the '
  'generated LaTeX, not by asking the model nicely.';

COMMIT;
