"""Tests for guardrails/output_guards.py (Task 21).

The individual `check_*` functions are a verbatim move from tailor_resume.py
(see tests/unit/test_tailor_resume_header_markers.py and
tests/unit/test_tailor_resume_raises.py for the pre-existing coverage that
now exercises them indirectly via the re-exported `_check_*` names). These
tests cover the functions directly from their new home, plus the new
`check_output` aggregator that did not exist before this task.
"""
from guardrails import output_guards as og


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
    tex = r"\section*{Experience} \section*{Skills} \section*{Education} \section*{Projects} \section*{Certifications}"
    result = og.check_output(tex, "tailor")
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
    tex = (
        r"\section*{Experience} \section*{Skills} \section*{Education} "
        r"\section*{Projects} \section*{Certifications} A highly motivated engineer."
    )
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
    assert any(v.rule == "fabrication" and v.severity == "warn" for v in result.violations)


def test_check_output_fabrication_disabled_for_score_task():
    tex = r"\section*{Technical Skills} Python, Java \section*{Experience}"
    result = og.check_output(tex, "score", base_skills_text="Python, AWS")
    assert not any(v.rule == "fabrication" for v in result.violations)


def test_check_output_textbf_preservation_runs_whenever_base_body_given():
    tex = (
        r"\section*{Experience} \section*{Skills} \section*{Education} "
        r"\section*{Projects} \section*{Certifications} \textbf{A}"
    )
    base = r"\textbf{A} \textbf{B} \textbf{C} \textbf{D}"
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
