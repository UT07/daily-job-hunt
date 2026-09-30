r"""Composition rules must be COUNTED, not asked for.

`tailor_resume.py` used to carry four lines of one person's composition rules
as string literals in the system prompt:

    :250  "Projects: EXACTLY 3 PROJECTS. No more, no less."
    :251  'ALWAYS KEEP BOTH "Purrrfect Keys" AND "NaukriBaba"'
    :268  "The resume MUST be exactly TWO PAGES."
    :269  "Page 1: ... Clover IT Services (7 bullets), and Seattle Kraken (3 bullets)."

Nothing enforced any of them. `_validate_macro_arities` checks that each
`\jobentry` call has four arguments; nothing counted how many `\jobentry`
calls the model emitted. A model that returned five projects got five
projects shipped, and the only signal was a three-page PDF.

These tests pin both halves of the fix:

  * the rules come from the user's own `users.composition_policy`, so a
    second user does not inherit the first user's project list; and
  * the generated document is counted against them, repaired once with the
    real counts if it breaks them, and FLAGGED rather than silently accepted
    if the repair does not land.
"""
from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402

from tests.unit.realistic_resume_body import SKILLS_FLAT  # noqa: E402

USER_NAME = "Ada Lovelace"
USER_EMAIL = "ada@example.com"

# Neutral filler: carries the body over the handler's 500-word floor without
# tripping the banned-phrase guard, so the only reason `ai_complete` ever gets
# called in these tests is the composition repair.
_SENTENCE = "Reduced queue latency and shipped weekly releases for the payments team. "
FILLER = _SENTENCE * 60  # 720 words

PREAMBLE = (
    "\\documentclass{article}\n"
    "\\newcommand{\\jobentry}[4]{#1 #2 #3 #4}\n"
    "\\newcommand{\\projectentry}[3]{#1 #2 #3}\n"
    "\\newcommand{\\projectentryurl}[5]{#1 #2 #3 #4 #5}\n"
)


def body(projects: int = 3, jobs: int = 2, filler: str = FILLER) -> str:
    """A structurally valid resume body with an exact number of entries."""
    jobs_tex = "\n".join(
        f"\\jobentry{{Company{i}}}{{Dublin}}{{2022 -- 2024}}{{Engineer}}\n"
        f"\\begin{{itemize}}\\item Built pipeline {i}. {filler}\\end{{itemize}}"
        for i in range(jobs)
    )
    projects_tex = "\n".join(
        f"\\projectentry{{Project{i}}}{{2025}}{{Python}}\n"
        f"\\begin{{itemize}}\\item Shipped project {i}.\\end{{itemize}}"
        for i in range(projects)
    )
    return (
        "\\begin{center}\n"
        f"{{\\Large \\textbf{{{USER_NAME}}}}}\\\\\n"
        f"Dublin, Ireland \\textbar\\ {USER_EMAIL}\n"
        "\\end{center}\n"
        "\\section*{Summary}\n"
        "\\textbf{3+ years} building payment systems.\n"
        "\\section*{Technical Skills}\n"
        # Was "Python, AWS, PostgreSQL, Docker". The handler now also counts
        # identity anchors (guardrails.check_near_empty, floor 40) before
        # accepting a generated body, for the same reason FILLER above exists:
        # a fixture has to clear the content gates or the test measures the
        # fixture. Four technologies is not a Technical Skills section --
        # the real corpus row's carries 126 anchors.
        f"{SKILLS_FLAT}\n"
        "\\section*{Experience}\n"
        f"{jobs_tex}\n"
        "\\section*{Featured Projects}\n"
        f"{projects_tex}\n"
        "\\section*{Education}\n"
        "MSc Cloud Computing\n"
        "\\section*{Certifications}\n"
        "AWS Solutions Architect\n"
    )


def body_missing_a_section(projects: int = 3) -> str:
    """Structurally complete except for Certifications — trips a hard gate.

    Long enough and \\textbf-preserving, so the fallback it causes is a clean
    hard-gate fallback and not a word-count or quality-retry detour.
    """
    return body(projects=projects).replace(
        "\\section*{Certifications}\nAWS Solutions Architect\n", "",
    )


BASE_TEX = f"{PREAMBLE}\\begin{{document}}\n{body(projects=5)}\n\\end{{document}}\n"


class FakeDB:
    """Supabase stub that records the columns each table was selected with."""

    def __init__(self, user_row=None, users_select_raises=False):
        self.user_row = user_row
        self.users_select_raises = users_select_raises
        self.selects: list[tuple[str, str]] = []
        self.updates: list[dict] = []

    def table(self, name):
        outer = self
        chain = MagicMock()

        def select(columns="*"):
            outer.selects.append((name, columns))
            if name == "users" and outer.users_select_raises and "composition_policy" in columns:
                raise RuntimeError(
                    'column users.composition_policy does not exist'
                )
            return chain

        def execute():
            result = MagicMock()
            if name == "jobs_raw":
                result.data = [{
                    "job_hash": "abc123", "title": "Backend Engineer",
                    "company": "Acme", "description": "Python, AWS, PostgreSQL",
                }]
            elif name == "user_resumes":
                result.data = [{"tex_content": BASE_TEX, "created_at": "2026-09-29"}]
            elif name == "users":
                result.data = [outer.user_row] if outer.user_row else []
            else:
                result.data = []
            return result

        def update(payload):
            outer.updates.append(payload)
            return chain

        chain.select.side_effect = select
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.update.side_effect = update
        chain.execute.side_effect = execute
        return chain

    def users_columns(self) -> str:
        return next(cols for tbl, cols in self.selects if tbl == "users")


def user_row(policy=None):
    return {"name": USER_NAME, "first_name": "Ada", "last_name": "Lovelace",
            "email": USER_EMAIL, "composition_policy": policy}


EVENT = {"job_hash": "abc123", "user_id": "u-1"}


def run_handler(*, council_body, policy=None, repair=None, db=None):
    """Drive handler() with a mocked council and (optional) repair response.

    Returns (result, council_mock, repair_mock, s3_mock, db).
    """
    db = db if db is not None else FakeDB(user_row(policy))
    council = MagicMock(return_value={
        "content": council_body, "provider": "groq", "model": "test",
    })
    repair_mock = MagicMock(return_value=repair if repair is not None else {"content": ""})
    boto3_mock = MagicMock()
    s3 = boto3_mock.client.return_value
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", boto3_mock), \
         patch.object(tailor_resume, "council_complete", council), \
         patch.object(tailor_resume, "ai_complete", repair_mock):
        result = tailor_resume.handler(EVENT, None)
    return result, council, repair_mock, s3, db


def written_tex(s3) -> str:
    return s3.put_object.call_args.kwargs["Body"].decode("utf-8")


def system_prompt_of(council) -> str:
    return council.call_args.kwargs["system"]


# ---------------------------------------------------------------------------
# The prompt no longer carries one person's composition rules
# ---------------------------------------------------------------------------

class TestPromptIsNoLongerSingleTenant:
    """The four literals named in the brief, gone from the system prompt.

    Scope note: the REQUIRED HEADER BLOCK still quotes one person's name,
    phone and email. That is a worked example of the output FORMAT rather than
    a composition rule, and making it per-user needs contact details plumbed
    into the prompt — a separate change, asserted as a known gap by
    test_known_remaining_single_tenancy_is_the_header_block below.
    """

    def test_exactly_three_projects_literal_is_gone(self):
        assert "EXACTLY 3 PROJECTS" not in tailor_resume._SYSTEM_PROMPT

    def test_pinned_project_names_are_gone_from_the_rules(self):
        assert "ALWAYS KEEP BOTH" not in tailor_resume._SYSTEM_PROMPT
        assert "Genomic Benchmarking" not in tailor_resume._SYSTEM_PROMPT
        assert "WhatsTheCraic" not in tailor_resume._SYSTEM_PROMPT

    def test_two_pages_literal_is_gone(self):
        assert "exactly TWO PAGES" not in tailor_resume._SYSTEM_PROMPT

    def test_per_employer_bullet_counts_are_gone(self):
        assert "Clover to 6" not in tailor_resume._SYSTEM_PROMPT
        assert "Keep Clover at 7" not in tailor_resume._SYSTEM_PROMPT
        assert "Seattle Kraken (3 bullets)" not in tailor_resume._SYSTEM_PROMPT

    def test_prompt_defers_to_the_composition_rules_block(self):
        assert "COMPOSITION RULES" in tailor_resume._SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# The policy is the user's, read from the user's row
# ---------------------------------------------------------------------------

class TestPolicyComesFromTheUserRow:
    def test_users_select_asks_for_composition_policy(self):
        _, _, _, _, db = run_handler(council_body=body(projects=3))
        assert "composition_policy" in db.users_columns()

    def test_null_policy_renders_the_defaults(self):
        _, council, _, _, _ = run_handler(council_body=body(projects=3), policy=None)
        prompt = system_prompt_of(council)
        assert "AT MOST 3 projects" in prompt
        assert "AT MOST 4 experience entries" in prompt

    def test_stored_policy_reaches_the_prompt(self):
        _, council, _, _, _ = run_handler(
            council_body=body(projects=1), policy={"max_projects": 1, "pages": 1},
        )
        prompt = system_prompt_of(council)
        assert "AT MOST 1 projects" in prompt
        assert "exactly 1 pages" in prompt

    def test_prompt_carries_no_other_users_project_names(self):
        _, council, _, _, _ = run_handler(council_body=body(projects=3))
        prompt = system_prompt_of(council)
        for name in ("Purrrfect Keys", "Seattle Kraken", "WhatsTheCraic"):
            assert name not in prompt.split("COMPOSITION RULES")[-1]

    def test_missing_profile_row_still_uses_defaults(self):
        db = FakeDB(user_row=None)
        _, council, _, _, _ = run_handler(council_body=body(projects=3), db=db)
        assert "AT MOST 3 projects" in system_prompt_of(council)

    def test_unmigrated_database_degrades_instead_of_failing_every_tailor(self, caplog):
        """Selecting a column PostgREST does not know about fails the whole
        request, and this select gates every tailor. Deploying the code before
        the migration must degrade the feature to its defaults, not take
        tailoring down for every job."""
        db = FakeDB(user_row(), users_select_raises=True)
        with caplog.at_level(logging.WARNING):
            result, council, _, _, _ = run_handler(council_body=body(projects=3), db=db)
        assert result["tex_s3_key"]
        assert "AT MOST 3 projects" in system_prompt_of(council)
        assert "20260929200000_users_composition_policy" in caplog.text

    def test_unmigrated_database_still_derives_header_markers(self):
        """The retry must keep the other columns, or every tailor falls back
        to the base resume for a missing header."""
        db = FakeDB(user_row(), users_select_raises=True)
        result, _, _, _, _ = run_handler(council_body=body(projects=3), db=db)
        assert result["used_fallback"] is False


# ---------------------------------------------------------------------------
# Enforcement: the output is counted
# ---------------------------------------------------------------------------

class TestEnforcementByCounting:
    def test_compliant_output_is_never_retried(self):
        result, _, repair, _, _ = run_handler(council_body=body(projects=3))
        assert repair.call_count == 0
        assert result["composition_violations"] == []

    def test_under_the_cap_is_not_a_violation(self):
        result, _, repair, _, _ = run_handler(council_body=body(projects=1))
        assert repair.call_count == 0
        assert result["composition_violations"] == []

    def test_over_the_cap_triggers_exactly_one_repair(self):
        _, _, repair, _, _ = run_handler(
            council_body=body(projects=5),
            repair={"content": body(projects=3)},
        )
        assert repair.call_count == 1

    def test_repair_prompt_names_the_actual_counts(self):
        """"You emitted 5 projects, the limit is 3" is actionable. Repeating
        the original rule at a model that already ignored it is not."""
        _, _, repair, _, _ = run_handler(
            council_body=body(projects=5),
            repair={"content": body(projects=3)},
        )
        prompt = repair.call_args.args[0]
        assert "5 projects" in prompt
        assert "limit is 3" in prompt

    def test_repaired_body_is_the_one_that_ships(self):
        result, _, _, s3, _ = run_handler(
            council_body=body(projects=5),
            repair={"content": body(projects=2)},
        )
        from shared.composition_policy import count_entries
        assert count_entries(written_tex(s3))["projects"] == 2
        assert result["composition_violations"] == []

    def test_stored_policy_drives_enforcement_not_just_the_prompt(self):
        """A cap the user lowered must be counted at the lowered value."""
        result, _, repair, s3, _ = run_handler(
            council_body=body(projects=3),
            policy={"max_projects": 2},
            repair={"content": body(projects=2)},
        )
        assert repair.call_count == 1
        assert "limit is 2" in repair.call_args.args[0]
        assert result["composition_violations"] == []

    def test_experience_overflow_is_counted_too(self):
        result, _, repair, _, _ = run_handler(
            council_body=body(projects=3, jobs=5),
            repair={"content": body(projects=3, jobs=4)},
        )
        assert repair.call_count == 1
        assert "5 experience entries" in repair.call_args.args[0]
        assert result["composition_violations"] == []


# ---------------------------------------------------------------------------
# A repair that does not land is reported, never silently accepted
# ---------------------------------------------------------------------------

class TestUnrepairedViolations:
    """What ships when the AI repair does not produce a compliant document.

    Every assertion about the SHIPPED document was inverted on 2026-09-30, and
    the reason is worth stating rather than quietly editing. The old contract
    was "discard the bad repair, ship the council's over-cap body, and report
    the violation on the return value". That is exactly the shape of CLAUDE.md
    rule 13: the check fired every time, correctly, and the response was a
    field nobody acted on. Measured in production the same day, job
    0ab484ad7687 shipped 5 experience entries and 5 projects against caps of 3
    and 3, with "does not meet the composition rules" in CloudWatch.

    Trimming needs no model, so it now always happens (compose_from_corpus).
    The assertions about what the REPAIR did are unchanged -- a truncated,
    empty or section-missing repair is still discarded -- and they are now
    sharper: the shipped document must contain the council's entries trimmed to
    the cap, NOT the discarded repair's. body(projects=1) yields Project0
    alone, so asserting three projects INCLUDING Project2 distinguishes "the
    council's five were trimmed" from "the truncated repair was used after
    all", which the old count-only assertion could not.
    """

    def test_still_violating_repair_is_flagged(self, caplog):
        from shared.composition_policy import count_entries
        with caplog.at_level(logging.ERROR):
            result, _, repair, s3, _ = run_handler(
                council_body=body(projects=5),
                repair={"content": body(projects=4)},
            )
        assert repair.call_count == 1
        # Still logged at ERROR by _enforce_composition: the model was asked
        # and did not comply, which is worth an alert even though the document
        # is then repaired by arithmetic.
        assert "composition" in caplog.text.lower()
        assert count_entries(written_tex(s3))["projects"] == 3
        assert result["composition_violations"] == []

    def test_still_violating_repair_is_logged_at_error_level(self):
        with patch.object(tailor_resume.logger, "error") as err:
            run_handler(council_body=body(projects=5),
                        repair={"content": body(projects=4)})
        assert err.called

    def test_truncated_repair_is_discarded(self):
        """A repair that complied by being cut off is not a repair.

        The truncated repair held ONE project. Three shipping, Project2 among
        them, proves the council's five were trimmed rather than the stub used.
        """
        result, _, _, s3, _ = run_handler(
            council_body=body(projects=5),
            repair={"content": body(projects=1), "truncated": True},
        )
        from shared.composition_policy import count_entries
        written = written_tex(s3)
        assert count_entries(written)["projects"] == 3
        assert "Project2" in written, "the truncated one-project repair shipped"
        assert result["composition_violations"] == []

    def test_repair_missing_sections_is_discarded(self):
        result, _, _, s3, _ = run_handler(
            council_body=body(projects=5),
            repair={"content": "\\section*{Summary}\nToo short.\n"},
        )
        from shared.composition_policy import count_entries
        written = written_tex(s3)
        assert count_entries(written)["projects"] == 3
        # The discarded repair had no Certifications and no entries at all.
        assert "Project2" in written
        assert "\\section*{Certifications}" in written
        assert result["composition_violations"] == []

    def test_empty_repair_is_discarded(self):
        result, _, _, s3, _ = run_handler(
            council_body=body(projects=5), repair={"content": "   "},
        )
        from shared.composition_policy import count_entries
        written = written_tex(s3)
        assert count_entries(written)["projects"] == 3
        assert "Project2" in written
        assert result["composition_violations"] == []

    def test_repair_call_failure_does_not_raise(self):
        """The council body is still a resume. A failed repair degrades to
        trimming it deterministically; it does not fail the job."""
        from shared.composition_policy import count_entries
        db = FakeDB(user_row())
        council = MagicMock(return_value={"content": body(projects=5),
                                          "provider": "groq", "model": "t"})
        boto3_mock = MagicMock()
        with patch.object(tailor_resume, "get_supabase", return_value=db), \
             patch.object(tailor_resume, "boto3", boto3_mock), \
             patch.object(tailor_resume, "council_complete", council), \
             patch.object(tailor_resume, "ai_complete",
                          side_effect=RuntimeError("all providers exhausted")):
            result = tailor_resume.handler(EVENT, None)
        assert result["tex_s3_key"]
        assert result["composition_violations"] == []
        written = boto3_mock.client.return_value.put_object.call_args_list[0][1]["Body"]
        assert count_entries(written.decode())["projects"] == 3

    def test_flag_is_always_present_on_the_return_value(self):
        result, _, _, _, _ = run_handler(council_body=body(projects=3))
        assert "composition_violations" in result


# ---------------------------------------------------------------------------
# The flag describes the document that actually ships
# ---------------------------------------------------------------------------

class TestFlagDescribesTheShippedDocument:
    def test_base_resume_fallback_is_composed_down_to_the_policy(self):
        """Superseded on 2026-09-30, premise and all.

        This asserted that a fallback reports the corpus counts, reasoning that
        "a fallback ships the corpus, which exceeds the caps by definition --
        returning a clean bill of health for a document nobody composed would
        be a lie". The reasoning was sound and the conclusion was the bug: the
        fix is not to report the lie accurately, it is to compose the document.
        A corpus exceeds the caps by definition, and cutting it down to them
        needs no model -- the entries are already written and already ordered.

        So the clean bill of health is now earned rather than false, and
        `used_fallback` stays True because the council's body really was
        rejected. The two facts are separate: WHERE the content came from, and
        WHETHER what shipped obeys the user's rules.
        """
        from shared.composition_policy import count_entries
        result, _, _, s3, _ = run_handler(council_body=body_missing_a_section())
        assert result["used_fallback"] is True
        assert result["composition_violations"] == []
        assert count_entries(written_tex(s3))["projects"] == 3

    def test_the_fallback_is_trimmed_arithmetically_never_by_a_model(self):
        """No AI call on this path -- that part of the old contract holds.

        The original name was `test_fallback_is_not_repaired` and its point was
        that re-splicing a generated body over a deliberate fallback would undo
        the fallback. Still true, and still enforced: repair.call_count is 0.
        What changed is that the corpus is no longer written out verbatim. It is
        the same document minus the entries over the cap -- every surviving
        entry byte-identical to the base, because nothing generated it.
        """
        _, _, repair, s3, _ = run_handler(council_body=body_missing_a_section())
        assert repair.call_count == 0

        written = written_tex(s3)
        assert written != BASE_TEX, "the corpus shipped untrimmed"
        # Kept entries are the base's own text, not a regeneration.
        for kept in ("Project0", "Project1", "Project2", "Company0", "Company1"):
            assert kept in written
        for dropped in ("Project3", "Project4"):
            assert dropped not in written
        assert "\\section*{Certifications}" in written


# ---------------------------------------------------------------------------
# Deploy shape: the import must resolve where the code actually runs
# ---------------------------------------------------------------------------

def test_shared_import_resolves_in_the_zip_lambda_shape(tmp_path):
    r"""`shared/` reaches zip Lambdas at /opt/python, not via the repo root.

    pytest puts the repo root on sys.path, so `from shared.composition_policy
    import ...` resolves there whether or not it would resolve in production.
    A zip-based pipeline Lambda sees only its flattened CodeUri
    (lambdas/pipeline/ -> /var/task) plus the layer mount (/opt/python). This
    reproduces that path shape and imports the real module.
    """
    opt_python = tmp_path / "opt_python"
    opt_python.mkdir()
    (opt_python / "shared").symlink_to(REPO / "shared", target_is_directory=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(opt_python), str(REPO / "lambdas" / "pipeline")])
    env["AWS_DEFAULT_REGION"] = "eu-west-1"

    proc = subprocess.run(
        [sys.executable, "-P", "-c",
         "import tailor_resume; "
         "from shared.composition_policy import check_output, describe_violations, render_for_prompt; "
         "assert tailor_resume.render_for_prompt is render_for_prompt; "
         "print('ok')"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, (
        f"tailor_resume does not import in the zip-Lambda path shape:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "ok" in proc.stdout


def test_composition_policy_ships_on_both_deploy_paths():
    """`shared` is already covered by test_deploy_path_parity; this pins that
    the new module lives inside that package rather than at the repo root,
    where it would reach neither the layer nor the container image."""
    assert (REPO / "shared" / "composition_policy.py").is_file()
    assert not (REPO / "composition_policy.py").exists()


@pytest.mark.parametrize("literal", [
    "EXACTLY 3 PROJECTS",
    "ALWAYS KEEP BOTH",
    "exactly TWO PAGES",
    "Clover IT Services (7 bullets)",
    "Keep Clover at 7",
])
def test_hardcoded_composition_rules_are_gone_from_the_prompt(literal):
    """Scoped to the prompt text the model actually receives, not to the
    module source — a docstring that quotes the old rule to explain why it
    was removed is not a re-introduction of it."""
    assert literal not in tailor_resume._SYSTEM_PROMPT


def test_macro_examples_do_not_name_a_real_project():
    """The macro examples showed one candidate's real project names. In a
    multi-tenant prompt that is a fabrication vector, not just an aesthetic
    problem: a model short of material can copy the placeholder straight into
    someone else's resume."""
    for name in ("Purrrfect Keys", "WhatsTheCraic", "Clover IT Services"):
        assert name not in tailor_resume._SYSTEM_PROMPT


def test_known_remaining_single_tenancy_is_the_header_block():
    """Recorded, not fixed here.

    The REQUIRED HEADER BLOCK still quotes one person's name, phone, email and
    links. That is a worked example of the OUTPUT FORMAT rather than a
    composition rule, and replacing it needs the user's contact details
    plumbed into the prompt — a different change from this one. This test
    exists so the gap is a known, asserted fact rather than something a later
    reader assumes was already handled.
    """
    assert "Utkarsh Singh" in tailor_resume._SYSTEM_PROMPT, (
        "the header block was made per-user — good; delete this test and the "
        "scope note in TestPromptIsNoLongerSingleTenant"
    )
