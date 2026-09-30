"""The user's resume-composition rules, and the counting that enforces them.

The master a user uploads is a corpus — everything about them, three pages or
ten. A resume is composed FROM it per job, and the rules governing that
composition (how many experience entries, how many projects, how many pages)
are the user's settings.

Until now they were string literals in the tailoring prompt:

    tailor_resume.py:250  "Projects: EXACTLY 3 PROJECTS. No more, no less."
    tailor_resume.py:251  'ALWAYS KEEP BOTH "Purrrfect Keys" AND "NaukriBaba"'
    tailor_resume.py:268  "The resume MUST be exactly TWO PAGES."
    tailor_resume.py:269  "Page 1: ... Clover IT Services (7 bullets) ..."

Two defects. Those are one person's project names compiled into the prompt, so
the system is single-tenant by construction. And nothing enforced any of it:
`_validate_macro_arities` checks that each `\\jobentry` call has four
arguments, and nothing anywhere counted how many `\\jobentry` calls existed.
Every one of those lines was a request.

These tests pin the countable half: what the rules are, and that the output is
measured against them rather than asked nicely.
"""
from __future__ import annotations

import pathlib

import pytest

from shared.composition_policy import (
    DEFAULTS,
    check_output,
    count_entries,
    describe_violations,
    render_for_prompt,
    resolve,
)

REPO = pathlib.Path(__file__).resolve().parents[2]

# One person's names. None of them may appear in text rendered for the prompt
# — that is what made the old rules single-tenant.
HARDCODED_NAMES = [
    "Purrrfect Keys",
    "NaukriBaba",
    "Clover IT Services",
    "Seattle Kraken",
    "WhatsTheCraic",
    "Genomic Benchmarking",
    "UTWorld",
]


class TestDefaults:
    def test_max_projects_is_three(self):
        """Explicit user instruction, 2026-09-29: four projects is too many
        for a two-page document."""
        assert DEFAULTS["max_projects"] == 3

    def test_defaults_cover_every_documented_key(self):
        assert set(DEFAULTS) == {
            "max_experience_entries",
            "max_projects",
            "pages",
            "bullets_per_entry",
            "prefer",
            "rename",
        }

    def test_defaults_name_nobody(self):
        blob = repr(DEFAULTS)
        for name in HARDCODED_NAMES:
            assert name not in blob


class TestResolve:
    def test_none_yields_defaults(self):
        assert resolve(None) == DEFAULTS

    def test_non_dict_yields_defaults(self):
        # A malformed JSONB value must not take tailoring down.
        assert resolve("not a policy") == DEFAULTS
        assert resolve([1, 2, 3]) == DEFAULTS

    def test_partial_override_keeps_other_defaults(self):
        policy = resolve({"max_projects": 2})
        assert policy["max_projects"] == 2
        assert policy["max_experience_entries"] == DEFAULTS["max_experience_entries"]
        assert policy["pages"] == DEFAULTS["pages"]

    def test_bullets_per_entry_merges_rather_than_replaces(self):
        policy = resolve({"bullets_per_entry": {"max": 5}})
        assert policy["bullets_per_entry"] == {"min": 3, "max": 5}

    def test_unknown_keys_are_ignored(self):
        policy = resolve({"max_projects": 1, "favourite_colour": "blue"})
        assert "favourite_colour" not in policy
        assert policy["max_projects"] == 1

    def test_does_not_mutate_defaults(self):
        resolve({"max_projects": 99, "bullets_per_entry": {"min": 1}})
        assert DEFAULTS["max_projects"] == 3
        assert DEFAULTS["bullets_per_entry"] == {"min": 3, "max": 7}


class TestCountEntries:
    def test_real_base_resume(self):
        """The shipped corpus: two jobs, five projects."""
        tex = (REPO / "resumes" / "fullstack.tex").read_text()
        assert count_entries(tex) == {"experience": 2, "projects": 5}

    def test_macro_definition_is_not_a_call(self):
        r"""\newcommand{\jobentry}[4]{...} defines the macro; it is not an entry.

        Counting it would make every resume look like it had one extra job,
        because the preamble defines all three macros.
        """
        assert count_entries(r"\newcommand{\jobentry}[4]{#1 #2 #3 #4}") == {
            "experience": 0, "projects": 0,
        }

    def test_longer_macro_name_is_not_a_match(self):
        assert count_entries(r"\jobentryheader{x}")["experience"] == 0
        assert count_entries(r"\projectentrywhatever{x}")["projects"] == 0

    def test_both_project_macros_count(self):
        tex = (
            r"\projectentry{A}{2025}{Python}"
            "\n"
            r"\projectentryurl{B}{2025}{https://x}{x}{Python}"
        )
        assert count_entries(tex)["projects"] == 2

    def test_empty_and_none_are_zero(self):
        assert count_entries("") == {"experience": 0, "projects": 0}
        assert count_entries(None) == {"experience": 0, "projects": 0}


class TestCheckOutput:
    def test_real_base_resume_exceeds_the_default_project_cap(self):
        """The exact violation string a repair prompt has to carry."""
        tex = (REPO / "resumes" / "fullstack.tex").read_text()
        assert check_output(tex) == ["5 projects, limit is 3"]

    def test_compliant_document_has_no_violations(self):
        tex = r"\jobentry{A}{B}{C}{D}" + r"\projectentry{P}{2025}{Python}" * 3
        assert check_output(tex) == []

    def test_under_the_cap_is_not_a_violation(self):
        """Caps are limits. A candidate with one job cannot produce two, and
        failing them for it would break the rule for exactly the people it is
        not meant to constrain."""
        assert check_output(r"\projectentry{P}{2025}{Python}") == []

    def test_experience_overflow_is_reported(self):
        tex = r"\jobentry{A}{B}{C}{D}" * 5
        assert check_output(tex) == ["5 experience entries, limit is 4"]

    def test_both_kinds_reported_together(self):
        tex = r"\jobentry{A}{B}{C}{D}" * 5 + r"\projectentry{P}{2025}{Py}" * 4
        assert check_output(tex) == [
            "5 experience entries, limit is 4",
            "4 projects, limit is 3",
        ]

    def test_stored_policy_overrides_the_cap(self):
        tex = r"\projectentry{P}{2025}{Python}" * 3
        assert check_output(tex, {"max_projects": 2}) == ["3 projects, limit is 2"]
        assert check_output(tex, {"max_projects": 5}) == []

    def test_none_policy_uses_defaults(self):
        tex = r"\projectentry{P}{2025}{Python}" * 4
        assert check_output(tex, None) == ["4 projects, limit is 3"]

    def test_page_count_is_not_guessed_from_source(self):
        """Pages are countable only against a compiled PDF. A check that
        guesses from LaTeX is worse than one that abstains."""
        assert all("page" not in v for v in check_output("", {"pages": 1}))


class TestDescribeViolations:
    def test_names_the_actual_counts(self):
        text = describe_violations(["5 projects, limit is 3"])
        assert "5 projects" in text
        assert "limit is 3" in text

    def test_tells_the_model_to_delete_the_whole_entry(self):
        text = describe_violations(["4 projects, limit is 3"])
        assert "itemize" in text or "empty shell" in text

    def test_empty_for_no_violations(self):
        assert describe_violations([]) == ""


class TestRenderForPrompt:
    def test_names_nobody(self):
        """The smoking gun for the single-tenancy defect."""
        rendered = render_for_prompt(None)
        for name in HARDCODED_NAMES:
            assert name not in rendered, f"{name} is compiled into the prompt"

    def test_states_the_default_caps(self):
        rendered = render_for_prompt(None)
        assert "AT MOST 4 experience entries" in rendered
        assert "AT MOST 3 projects" in rendered
        assert "exactly 2 pages" in rendered

    def test_reflects_a_stored_policy(self):
        rendered = render_for_prompt({"max_projects": 4, "pages": 1})
        assert "AT MOST 4 projects" in rendered
        assert "exactly 1 pages" in rendered

    def test_two_users_get_two_different_prompts(self):
        """The property the old hardcoded prompt could not have."""
        a = render_for_prompt({"max_projects": 2})
        b = render_for_prompt({"max_projects": 5})
        assert a != b

    def test_preferences_appear_only_when_set(self):
        assert "PREFERENCES" not in render_for_prompt(None)
        rendered = render_for_prompt({"prefer": ["UTA IT Support over Seattle Kraken"]})
        assert "PREFERENCES" in rendered
        assert "UTA IT Support over Seattle Kraken" in rendered

    def test_renames_appear_only_when_set(self):
        assert "TITLE ADJUSTMENTS" not in render_for_prompt(None)
        rendered = render_for_prompt({
            "rename": [{"from": "IT Support", "to": "Web Developer",
                        "when": "full-stack software engineering"}]
        })
        assert "IT Support" in rendered
        assert "Web Developer" in rendered
        assert "full-stack software engineering" in rendered

    def test_rename_without_when_is_unconditional(self):
        rendered = render_for_prompt({"rename": [{"from": "A", "to": "B"}]})
        assert '"A"' in rendered and '"B"' in rendered
        assert "when the target role is" not in rendered

    def test_caps_are_stated_as_limits_not_targets(self):
        assert "limits, not targets" in render_for_prompt(None)


class TestMigration:
    """The column the policy is read from."""

    PATH = REPO / "supabase" / "migrations" / "20260929200000_users_composition_policy.sql"

    def test_migration_exists(self):
        assert self.PATH.is_file()

    def test_adds_the_column_idempotently(self):
        sql = self.PATH.read_text()
        assert "ADD COLUMN IF NOT EXISTS composition_policy JSONB" in sql

    def test_column_is_nullable_so_null_means_defaults(self):
        sql = self.PATH.read_text()
        assert "NOT NULL" not in sql.split("ADD COLUMN IF NOT EXISTS")[1].split(";")[0]


@pytest.mark.parametrize("stored", [None, {}, {"max_projects": 1}, {"pages": 3}])
def test_resolve_always_returns_every_key(stored):
    """Callers index the policy directly; a partial dict must never reach them."""
    assert set(resolve(stored)) == set(DEFAULTS)
