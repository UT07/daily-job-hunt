-- Rewrite the stored composition policy in its EXECUTABLE form.
--
-- Why this is needed. `prefer` rules used to be prose, which only the model
-- could act on. Prose is still supported and still reaches the prompt, but a
-- prose rule cannot be applied when the model's output is discarded -- and on
-- 2026-09-30 that was the common case: a hard gate (prompt echo, "Let's") sent
-- tailoring to the base-resume fallback, and the fallback shipped the corpus
-- verbatim: 5 experience entries and 5 projects against caps of 3 and 3.
--
-- shared/composition_policy.compose_from_corpus now trims deterministically,
-- but it can only honour a rule it can read. With prose rules it applies the
-- CAPS ONLY, and caps alone give the wrong answer here: the corpus orders
-- Seattle Kraken (3rd) ahead of the UT Arlington IT role (4th), so "keep the
-- first three" keeps the entry to drop and drops the entry to keep.
--
-- The two shapes it can execute:
--     {"exclude": "<entry name>",                "why": "..."}   never include
--     {"include": "<entry name>", "over": "...", "why": "..."}   prefer A to B
--     {"include": "<entry name>",                "why": "..."}   must be present
-- Names are matched as normalised substrings, so a partial name is enough and
-- LaTeX escapes (\& etc.) do not need reproducing. `why` is carried into the
-- prompt verbatim, so it is worth writing for the model as well as the reader.
--
-- Everything not named here keeps its default from shared/composition_policy
-- DEFAULTS -- `writing`, `bullets_per_entry`, `pages`. `resolve()` merges, so
-- this UPDATE does not silently drop them.
--
-- Scoped by email rather than a pasted UUID so it is re-runnable and reviewable.
-- Verify after running with the SELECT at the bottom.

UPDATE users
SET composition_policy = jsonb_build_object(
    'max_experience_entries', 3,
    'max_projects', 3,
    'prefer', jsonb_build_array(
        jsonb_build_object(
            'include', 'Yuno Energy',
            'why',     'the current role; a resume without it has no present employer'
        ),
        jsonb_build_object(
            'include', 'Office of IT, University of Texas at Arlington',
            'over',    'Seattle Kraken',
            'why',     'the UT Arlington IT role is the longer tenure'
        ),
        jsonb_build_object(
            'exclude', 'Dept. of Computer Science',
            'why',     'academic teaching assistant work, not industry experience'
        ),
        jsonb_build_object(
            'include', 'Purrrfect Keys',
            'over',    'Genomic Benchmarking',
            'why',     'a shipped product with real users beats an academic thesis'
        )
    ),
    'emphasise', jsonb_build_array(
        jsonb_build_object(
            'what', 'Foreground the DevOps and reliability work — pipelines, IaC, '
                    'Kubernetes, observability, incident response, uptime and cost — '
                    'ahead of application and front-end work, in both the summary and '
                    'the bullet ordering within each entry',
            'when', 'DevOps, SRE, platform, infrastructure, cloud or reliability'
        )
    )
)
WHERE email = '254utkarsh@gmail.com';

-- Expect: 3 / 3, FOUR prefer rules, all four with an executable shape.
SELECT
    composition_policy -> 'max_experience_entries'          AS max_experience,
    composition_policy -> 'max_projects'                    AS max_projects,
    jsonb_array_length(composition_policy -> 'prefer')      AS prefer_rules,
    (SELECT count(*)
       FROM jsonb_array_elements(composition_policy -> 'prefer') r
      WHERE r ? 'exclude' OR (r ? 'include' AND r ? 'over'))  AS executable_rules
FROM users
WHERE email = '254utkarsh@gmail.com';
