"""Tests for guardrails/output_guards.py (Task 21).

The individual `check_*` functions are a verbatim move from tailor_resume.py
(see tests/unit/test_tailor_resume_header_markers.py and
tests/unit/test_tailor_resume_raises.py for the pre-existing coverage that
now exercises them indirectly via the re-exported `_check_*` names). These
tests cover the functions directly from their new home, plus the new
`check_output` aggregator that did not exist before this task.
"""
from guardrails import output_guards as og
from tests.unit.realistic_resume_body import body as realistic_body


# ---------------------------------------------------------------------------
# Individual checks, exercised directly from their new home.
# ---------------------------------------------------------------------------


def test_check_brace_balance_true_for_balanced():
    assert og.check_brace_balance(r"\section{A} \textbf{b}") is True


def test_check_brace_balance_false_for_unbalanced():
    assert og.check_brace_balance(r"\section{A") is False


def test_check_required_sections_flags_missing():
    tex = r"\section*{Summary} \section*{Skills}"
    assert "experience" in og.check_required_sections(tex)
    assert "skills" not in og.check_required_sections(tex)


def test_check_header_present_empty_markers_is_noop():
    assert og.check_header_present("anything", []) == []


def test_check_header_present_flags_missing_marker():
    assert og.check_header_present("no name here", ["Alice"]) == ["Alice"]


def test_check_banned_phrases_flags_known_filler():
    result = og.check_banned_phrases("A highly motivated engineer.")
    assert any("highly motivated" in r for r in result)


def test_check_banned_phrases_clean_text_returns_empty():
    assert og.check_banned_phrases("Reduced latency by 40%.") == []


def test_check_textbf_preservation_flags_stripped_bold():
    base = r"\textbf{A} \textbf{B} \textbf{C} \textbf{D}"
    tailored = r"\textbf{A}"
    result = og.check_textbf_preservation(base, tailored)
    assert result and "textbf_stripped" in result[0]


def test_check_textbf_preservation_passes_when_base_has_none():
    assert og.check_textbf_preservation("no bold here", "still none") == []


def test_check_fabrication_flags_skill_not_in_base():
    base_skills = "Python, AWS, Docker"
    tailored = r"\section*{Technical Skills} Python, Java \section*{Experience}"
    result = og.check_fabrication(base_skills, tailored)
    assert any("Java" in r for r in result)


# The header subtitle: 19 of 27 violations across 140 real production resumes
# live here, and the Skills-section regex cannot reach it. Verbatim shape from
# job_hash 00a6c061bddb, a shipped resume for an SRE/data-platform role.
_REAL_HEADER = (
    r"\begin{center}" "\n"
    r"{\Large \textbf{Utkarsh Singh}}\\[0.04em]" "\n"
    r"{\normalsize Software Engineer (SRE, Data Platform, Mobile, Rust/TypeScript, AWS K8s)}\\[0.08em]" "\n"
    r"Dublin, Ireland \textbar\ 254utkarsh@gmail.com\\[0.08em]" "\n"
    r"\end{center}"
)


def test_fabrication_is_caught_in_the_header_subtitle():
    """The case the deployed guard could not see.

    Measured: the old Skills-only regex flagged 7 of 140 real resumes; adding
    this region takes it to 23 of 140, and 19 of the 27 violations are here.
    """
    tex = _REAL_HEADER + r" \section*{Technical Skills} Python, AWS \section*{Experience}"
    result = og.check_fabrication("Python, AWS, Docker", tex)
    assert any("Rust" in r for r in result), result


def test_the_violation_says_which_region_the_claim_is_in():
    """A repair round told "in the header subtitle" can fix the right line;
    "somewhere in the document" invites a full rewrite."""
    tex = _REAL_HEADER + r" \section*{Technical Skills} Python \section*{Experience}"
    (violation,) = og.check_fabrication("Python", tex)
    assert "header subtitle" in violation, violation


def test_a_skill_the_base_resume_has_is_not_flagged_in_the_header():
    """The header legitimately summarises real skills -- most of what is on that
    line is supported, which is why the region is safe to add."""
    tex = _REAL_HEADER + r" \section*{Technical Skills} Python \section*{Experience}"
    assert og.check_fabrication("Python, Rust, TypeScript", tex) == []


def test_the_same_skill_in_both_regions_is_reported_once():
    """Deduplicated, or a two-line repair prompt arrives for one problem."""
    tex = (
        _REAL_HEADER
        + r" \section*{Technical Skills} Python, Rust \section*{Experience}"
    )
    result = og.check_fabrication("Python", tex)
    assert len([r for r in result if "Rust" in r]) == 1, result


def test_a_document_with_no_header_still_has_its_skills_checked():
    """Cover letters and fragments have no header; the Skills path must not
    become dependent on finding one."""
    tex = r"\section*{Technical Skills} Python, Kotlin \section*{Experience}"
    assert any("Kotlin" in r for r in og.check_fabrication("Python", tex))


def test_a_longer_word_does_not_exonerate_a_claim():
    """"javascript" must not whitelist "java".

    Substring containment did exactly that, and it disabled the most-fabricated
    token in the blocklist: 482 of 707 real outputs list Java as a bare comma
    item and none could be flagged, because the corpus Skills section reads
    "TypeScript/JavaScript (React, ...)".
    """
    tex = r"\section*{Technical Skills} Java, SQL \section*{Experience}"
    result = og.check_fabrication("TypeScript/JavaScript (React)", tex)
    assert any("Java" in r for r in result), result


def test_a_claim_the_candidate_really_has_is_not_flagged():
    """The other half, which only works because the baseline is every row.

    Word boundaries alone are not an improvement: against the single row
    production tailors from they flag 97 of 140 real resumes instead of 23,
    because the 2026-09-28 row dropped Java and the 2026-04-05 row has it. The
    caller passes BaseResume.all_tex so a skill the candidate has ever listed
    counts as supported.
    """
    tex = r"\section*{Technical Skills} Java, SQL \section*{Experience}"
    union_of_all_rows = "TypeScript/JavaScript (React), Java, Python"
    assert og.check_fabrication(union_of_all_rows, tex) == []


def test_punctuated_names_still_match_their_own_boundary():
    """A dot and a space are inside these tokens, so \b would misbehave."""
    for name in ("vue.js", "spring boot"):
        supported = r"\section*{Technical Skills} Python \section*{X}"
        claimed = rf"\section*{{Technical Skills}} Python, {name} \section*{{X}}"
        assert og.check_fabrication(f"Python, {name}, SQL", claimed) == [], name
        assert og.check_fabrication("Python, SQL", claimed), name
        assert og.check_fabrication(f"Python, {name}", supported) == []


def test_check_fabrication_scope_is_skills_section_only():
    """Pins the documented limitation: a fabricated claim OUTSIDE the Skills
    section is invisible to this guard, even for the exact same blocklisted
    word, because the regex never looks past the Skills section boundary."""
    base_skills = "Python, AWS"
    tailored = (
        r"\section*{Technical Skills} Python, AWS \section*{Experience}"
        r" Built a Java service. \section*{Education}"
    )
    assert og.check_fabrication(base_skills, tailored) == []


# ---------------------------------------------------------------------------
# check_output: new aggregator, policy-gated.
# ---------------------------------------------------------------------------


def test_check_output_passes_on_clean_tailor_output():
    result = og.check_output(_COMPLETE_RESUME, "tailor")
    assert result.passed is True


def test_check_output_blocks_on_brace_imbalance_for_tailor_task():
    result = og.check_output(r"\section{Unbalanced", "tailor")
    assert result.passed is False
    assert any(v.rule == "brace_balance" for v in result.violations)


def test_check_output_blocks_on_missing_required_section():
    result = og.check_output(r"\section*{Summary}", "tailor")
    assert result.passed is False
    assert any(v.rule == "required_sections" for v in result.violations)


def test_check_output_skips_latex_structure_when_policy_disables_it():
    # "score" policy has latex_structure=False -- an unbalanced brace must
    # not even be inspected for a scoring call.
    result = og.check_output(r"\section{Unbalanced", "score")
    assert result.passed is True


def test_check_output_header_check_is_noop_without_markers():
    tex = r"\section*{Experience} \section*{Skills} \section*{Education} \section*{Projects} \section*{Certifications}"
    result = og.check_output(tex, "tailor")  # no header_markers kwarg
    assert not any(v.rule == "header_present" for v in result.violations)


def test_check_output_flags_missing_header_marker_as_block():
    tex = r"\section*{Experience} \section*{Skills} \section*{Education} \section*{Projects} \section*{Certifications}"
    result = og.check_output(tex, "tailor", header_markers=["Jane Doe"])
    assert result.passed is False
    assert any(v.rule == "header_present" and v.severity == "block" for v in result.violations)


def test_check_output_banned_phrase_is_warn_not_block():
    tex = _COMPLETE_RESUME + " A highly motivated engineer."
    result = og.check_output(tex, "tailor")
    assert result.passed is True  # warn severity never fails the result
    assert any(v.rule == "banned_phrase" and v.severity == "warn" for v in result.violations)


def test_check_output_fabrication_requires_base_skills_text():
    tex = r"\section*{Technical Skills} Python, Java \section*{Experience}"
    # No base_skills_text supplied -> fabrication check never runs, matching
    # tailor_resume.handler()'s own guard (`if base_skills_match: ...`).
    result = og.check_output(tex, "tailor")
    assert not any(v.rule == "fabrication" for v in result.violations)


def test_check_output_fabrication_runs_when_base_skills_text_given():
    tex = r"\section*{Technical Skills} Python, Java \section*{Experience}"
    result = og.check_output(tex, "tailor", base_skills_text="Python, AWS")
    # Severity is "block", not "warn". This assertion pinned "warn" until CI
    # run 36651253369 showed what that meant in practice: a resume claiming
    # Rust reported guards_passed=True and shipped. The test was green the
    # whole time -- it asserted the detector fired, which it did, and said
    # nothing about whether firing changed anything.
    assert any(v.rule == "fabrication" and v.severity == "block" for v in result.violations)


def test_fabrication_is_the_only_thing_blocking_a_well_formed_resume():
    """Isolates fabrication as the cause, which a minimal fixture cannot.

    The first draft of this test asserted `passed is False` against a
    two-section snippet. It passed with fabrication set to either severity,
    because a snippet that short is already missing four required sections
    and those block on their own -- green for a reason unrelated to what it
    claimed to test. Verified by mutation: restore "warn" and the assertion
    below fails, which the earlier one did not.
    """
    tex = _COMPLETE_RESUME.replace("Docker", "Docker, Rust")
    result = og.check_output(tex, "tailor", base_skills_text="Python, AWS, Docker")
    blocking = {v.rule for v in result.violations if v.severity == "block"}
    assert blocking == {"fabrication"}
    assert result.passed is False


# Was six one-line sections, ~250 characters, 3 identity anchors. `check_output`
# now reads document CONTENT as well as structure (check_near_empty), and no
# document the pipeline has ever produced is that thin -- 740 real tailored
# resumes carry 102-289 anchors. See tests/unit/realistic_resume_body.py for
# why the stub was replaced rather than the floor lowered. "Docker" is still in
# the Skills section, which the fabrication cases below substitute into.
_COMPLETE_RESUME = realistic_body()


def test_the_exact_case_that_shipped_in_ci():
    """Regression for eval case 12ed5b1de5e8 (CI run 36651253369).

    A tailor case served by gemini-3.5-flash-lite emitted a Skills section
    listing Rust against a base resume that has none. Reproduced with that
    shape rather than a minimal fixture so the test is recognisable as the
    incident: all six real section headers present, document well-formed,
    braces balanced -- every structural check passes. The only thing wrong
    with the document is that it is not true.
    """
    tex = _COMPLETE_RESUME.replace("Docker", "Docker, Rust")
    result = og.check_output(tex, "tailor", base_skills_text="Python, AWS, Docker")
    assert og.check_brace_balance(tex) is True
    assert not og.check_required_sections(tex), "structural checks must be clean"
    assert result.passed is False
    assert any("Rust" in v.detail for v in result.violations)


def test_cosmetic_violations_still_only_warn():
    """The distinction severity exists for, pinned from the other side.

    Promoting fabrication is not an argument for promoting everything:
    guardrails/types.py keeps severity so a stylistic nit cannot cost two
    repair rounds. A banned phrase and a dropped \textbf are cosmetic, and a
    document carrying only those must still finalize.
    """
    tex = _COMPLETE_RESUME + " a robust solution"
    result = og.check_output(tex, "tailor", base_body=r"\textbf{a} \textbf{b}")
    assert [v.rule for v in result.violations if v.severity == "block"] == []
    assert result.passed is True
    assert {v.rule for v in result.violations} <= {"banned_phrase", "textbf_preservation"}


def test_check_output_fabrication_disabled_for_score_task():
    tex = r"\section*{Technical Skills} Python, Java \section*{Experience}"
    result = og.check_output(tex, "score", base_skills_text="Python, AWS")
    assert not any(v.rule == "fabrication" for v in result.violations)


def test_check_output_textbf_preservation_runs_whenever_base_body_given():
    tex = _COMPLETE_RESUME
    base = r"\textbf{A} " * 200
    result = og.check_output(tex, "tailor", base_body=base)
    assert any(v.rule == "textbf_preservation" and v.severity == "warn" for v in result.violations)
    assert result.passed is True  # warn only


def test_check_output_default_task_unknown_falls_back_safely():
    # policy_for("nonexistent") falls back to "default", which has
    # latex_structure/fabrication off and banned_phrases on -- check_output
    # must not raise for an unrecognised task string.
    tex = r"\section*{Summary} A highly motivated engineer."
    result = og.check_output(tex, "some-unknown-task")
    assert any(v.rule == "banned_phrase" for v in result.violations)
    assert not any(v.rule in ("brace_balance", "required_sections") for v in result.violations)


# ---------------------------------------------------------------------------
# Yale's third term: the quantified result (check_unquantified_bullets)
# ---------------------------------------------------------------------------
# ACTION VERB + what you did and at what scale + QUANTIFIED RESULT. The last
# term is the one that gets dropped. Measured over 2,378 achievement bullets in
# 100 live résumés: 66.4% carry a number, 33.6% do not, and 0 of 100 résumés
# have every bullet quantified.
#
# So this REPORTS and never blocks, for two separate reasons. Blocking rejects
# every résumé ever generated (CLAUDE.md #16). And a hard counter is satisfied
# by inventing a figure — which the policy's own writing rules forbid, because
# an invented number is worse than an absent one.

class TestUnquantifiedBullets:
    @staticmethod
    def _f():
        from guardrails.output_guards import check_unquantified_bullets
        return check_unquantified_bullets

    def test_a_bullet_with_a_measured_result_is_not_flagged(self):
        tex = r"\begin{itemize}\item Cut p95 latency 42\% across 8 services." "\n" r"\end{itemize}"
        assert self._f()(tex) == []

    def test_a_bullet_with_no_number_is_named(self):
        tex = (r"\begin{itemize}" "\n"
               r"  \item Improved reliability of the deployment pipeline." "\n"
               r"\end{itemize}")
        out = self._f()(tex)
        assert len(out) == 1
        assert "1 of 1" in out[0]
        assert "Improved reliability" in out[0]

    def test_a_bare_year_is_not_a_result(self):
        """"Migrated the platform in 2024" states WHEN, not how much."""
        tex = r"\begin{itemize}" "\n" r"  \item Migrated the platform in 2024." "\n" r"\end{itemize}"
        assert self._f()(tex), "a year was accepted as a quantified result"

    def test_a_year_alongside_a_real_metric_still_passes(self):
        tex = (r"\begin{itemize}" "\n"
               r"  \item Migrated 12 services in 2024, cutting spend 30\%." "\n"
               r"\end{itemize}")
        assert self._f()(tex) == []

    def test_skills_entries_are_not_achievement_bullets(self):
        r"""The Technical Skills section lists technologies, uses \item, and
        must never carry numbers. Counting it put the unquantified rate at 34%
        over the wrong population — CLAUDE.md #7, and the reason the first
        measurement of this was thrown away."""
        tex = (r"\section*{Technical Skills}" "\n" r"\begin{itemize}" "\n"
               r"  \item \textbf{IaC:} Terraform, Ansible, CloudFormation" "\n"
               r"  \item \textbf{Observability:} Prometheus, Grafana, PagerDuty" "\n"
               r"\end{itemize}")
        assert self._f()(tex) == []

    def test_the_count_covers_achievements_only_when_both_are_present(self):
        tex = (r"\section*{Technical Skills}" "\n" r"\begin{itemize}" "\n"
               r"  \item \textbf{IaC:} Terraform" "\n" r"\end{itemize}" "\n"
               r"\section*{Experience}" "\n" r"\begin{itemize}" "\n"
               r"  \item Improved the thing." "\n"
               r"  \item Cut cost 30\%." "\n" r"\end{itemize}")
        out = self._f()(tex)
        assert "1 of 2" in out[0], f"skills leaked into the denominator: {out}"

    def test_the_message_forbids_inventing_a_number(self):
        """The finding drives a repair prompt. Asking for a number without
        saying where it may come from is how fabrication gets requested."""
        tex = r"\begin{itemize}" "\n" r"  \item Improved reliability." "\n" r"\end{itemize}"
        msg = self._f()(tex)[0]
        assert "never invent" in msg.lower()
        assert "base résumé supports" in msg or "base resume supports" in msg

    def test_an_empty_document_is_not_a_finding(self):
        assert self._f()("") == []


# -------------------------------------------------------------------------
# Severity must survive serialisation (GuardResult.to_dict)
# ---------------------------------------------------------------------------
# `to_dict` flattened every violation to one "rule: detail" string, so past
# that boundary nothing could tell a block from a warn. `passed` is computed
# before the flattening and survived; the REASONS did not -- and the reasons
# are what a repair prompt, a log line and a stored verdict all need.
# CLAUDE.md #13 at a serialisation boundary.

def test_to_dict_separates_blocking_violations_from_warnings():
    from guardrails.types import GuardResult, Violation
    r = GuardResult(violations=[
        Violation(rule="fabrication", detail="'Kotlin' not in base", severity="block"),
        Violation(rule="banned_phrase", detail="'robust'", severity="warn"),
    ])
    d = r.to_dict()
    assert d["passed"] is False
    assert d["blocking"] == ["fabrication: 'Kotlin' not in base"]
    assert len(d["violations"]) == 2, "`violations` must keep its previous contents"


def test_a_clean_result_reports_an_empty_blocking_list_not_a_missing_key():
    """`[]` is the claim that it ran and found nothing blocking. A missing key
    is indistinguishable from an older report that never recorded severity,
    and callers would have to guess which."""
    from guardrails.types import GuardResult
    assert GuardResult.ok().to_dict() == {"passed": True, "violations": [], "blocking": []}


def test_warnings_alone_pass_and_are_not_listed_as_blocking():
    from guardrails.types import GuardResult, Violation
    d = GuardResult(violations=[
        Violation(rule="banned_phrase", detail="'leveraging'", severity="warn")]).to_dict()
    assert d["passed"] is True
    assert d["blocking"] == []
    assert d["violations"] == ["banned_phrase: 'leveraging'"]
