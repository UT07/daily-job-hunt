r"""Two defects found in SHIPPED resumes on 2026-09-30, and what stops them.

Both were found by measuring 740 real tailored artifacts out of
s3://utkarsh-job-hunt, and neither is about honesty -- a document can be
entirely truthful and still be one of these.

  PROMPT ECHO      2 of 740 (0.27%). `a0527faaf531` and `773dd7cd6729` carry the
                   model's whole chain of thought between \begin{document} and
                   \end{document}: "1. Header: We must change the \normalsize
                   title line", "Reminder: your output MUST contain all six
                   section headers verbatim", six section headers with
                   "... tailored ..." typed under each. A resume containing the
                   word "jd-relevant" was sent to an employer.

  NEAR-EMPTY       1 of 740 (0.14%). `b45671b7ec5c`'s entire document body is
                   the word "and". `is_latex_document` passes it, because
                   \documentclass and \begin{document} come from the preamble
                   the splice supplies. `sections_have_content` passes documents
                   like it because it needs only ONE of five sections to be
                   non-empty -- CLAUDE.md rule 2 names that function.

Neither is catchable structurally. `773dd7cd6729` has all six required
\section* headers, balanced braces, 1728 words and 181 identity anchors: every
check in the repository before this one passes it.

CLAUDE.md rule 13 is why these tests are shaped the way they are. `check_
fabrication` detected correctly and changed nothing for weeks because its
violations carried severity "warn"; a test asserting a detector returns a
violation passes identically whether the violation stops anything. So every
test below that matters asserts a ROUTE -- quality_gate returning "repair", or
the bytes handler() puts to S3 -- not a label. Proven by mutation; the results
are in the PR body.

The fixtures reproduce the SHAPE of the two real artifacts rather than
embedding them: the real ones carry the user's phone number and email address
and this repository is public (see .gitignore's note on output/).
"""
from __future__ import annotations

import pathlib
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402
from agents import nodes  # noqa: E402
from guardrails import output_guards as og  # noqa: E402
from guardrails.policy import POLICIES  # noqa: E402

from tests.unit.realistic_resume_body import (  # noqa: E402
    SKILLS_FLAT,
    assert_realistic,
    body as realistic_body,
)

# --- fixtures reproducing the two real artifacts ---------------------------

# The shape of a0527faaf531: numbered planning prose where the document should
# be. Abridged; the real one runs to 16,757 characters of this.
ECHO_PLANNING = (
    r"\begin{center}{\Large \textbf{A Candidate}}\end{center}"
    "We are given a base resume body and a job description for an IT Support "
    "role.\n\n"
    "1. Header: We must change the \\normalsize title line to emphasize tech "
    "relevant to the JD.\n"
    "3. Technical Skills: Reorder CATEGORIES to put the most relevant first. "
    "We can reorder items within a category to front-load JD-relevant "
    "technologies.\n"
    "8. Keyword injection: Use JD vocabulary in existing bullets.\n"
    "Alternatively, we can note that the observability stack was applied to "
    "Linux services, but the rule says: do not fabricate.\n"
    r"\section*{Featured Projects}"
)

# The shape of 773dd7cd6729: the six real section headers with the model's
# placeholder typed under each, and an argument with itself around them.
ECHO_SKELETON = (
    r"\begin{center}{\Large \textbf{A Candidate}}\end{center}"
    'The instruction says "Return ONLY the tailored body content." It also '
    'says "Reminder: your output MUST contain all six section headers '
    'verbatim". Safer to include the header block as given.\n'
    "Thus we need to output:\n"
    r"\section*{Summary}" "\n... tailored ...\n"
    r"\section*{Technical Skills}" "\n... tailored ...\n"
    r"\section*{Experience}" "\n... tailored ...\n"
    r"\section*{Featured Projects}" "\n... tailored ...\n"
    r"\section*{Education}" "\n... tailored ...\n"
    r"\section*{Certifications}" "\n... tailored ...\n"
    "We must preserve all \\textbf{} formatting from the base resume.\n"
)

# The shape of b45671b7ec5c: the whole body.
NEAR_EMPTY_BODY = "and"

# All six required headers, nothing under any of them. This is the document
# that isolates near_empty as a cause: every structural check passes it.
EMPTY_SKELETON = (
    r"\section*{Summary}"
    r"\section*{Technical Skills}"
    r"\section*{Experience}"
    r"\section*{Featured Projects}"
    r"\section*{Education}"
    r"\section*{Certifications}"
)

CLEAN_BODY = realistic_body()


def _blocking(result) -> set[str]:
    return {v.rule for v in result.violations if v.severity == "block"}


# ===========================================================================
# 1. The instruments: do they see the two real defects, and nothing else?
# ===========================================================================


class TestPromptEchoDetection:
    def test_it_sees_the_planning_prose_artifact(self):
        found = og.check_prompt_echo(ECHO_PLANNING)
        assert found, "a0527faaf531's shape must be detected"
        rules = {f.split(":")[1].strip().split(" ")[0] for f in found}
        # Several independent families, so no single marker is load-bearing.
        assert len(rules) >= 3, rules

    def test_it_sees_the_placeholder_skeleton_artifact(self):
        found = og.check_prompt_echo(ECHO_SKELETON)
        assert found, "773dd7cd6729's shape must be detected"
        assert any("placeholder" in f for f in found), (
            "'... tailored ...' left where content belongs is the most "
            "recognisable half of this artifact"
        )

    def test_it_is_silent_on_a_real_resume_body(self):
        assert og.check_prompt_echo(CLEAN_BODY) == []

    @pytest.mark.parametrize("tex_file", ["resumes/fullstack.tex", "resumes/sre_devops.tex"])
    def test_it_is_silent_on_the_repository_own_resumes(self, tex_file):
        """A second real population, not the one the markers were tuned on.

        `resumes/fullstack.tex` is the base resume the eval harness tailors,
        and it caught a genuine false positive: the line

            % "section header on page 1, content on page 2" awkward split

        is an author's note in a LaTeX comment. Two tests in
        test_ai_truncation.py failed on it before check_prompt_echo learned to
        strip comments, one of them the end-to-end eval-harness case -- which
        is the same code path the CI AI Eval Gate runs.
        """
        tex = (REPO / tex_file).read_text()
        body = tex.split(r"\begin{document}", 1)[1].rsplit(r"\end{document}", 1)[0]
        assert og.check_prompt_echo(body) == []

    def test_a_comment_is_not_content(self):
        assert og.check_prompt_echo("% we must reorder the base resume here") == []
        assert og.check_prompt_echo("we must reorder the base resume here") != []

    def test_the_violation_names_the_offending_text(self):
        """Repair has to be informed to be worth two rounds (rule 4)."""
        found = og.check_prompt_echo("Reorder to front-load JD-relevant terms.")
        assert found and "JD-relevant" in found[0]


class TestNearEmptyDetection:
    def test_it_sees_the_one_word_body(self):
        assert og.check_near_empty(NEAR_EMPTY_BODY)

    def test_it_sees_a_headers_only_skeleton(self):
        assert og.check_near_empty(EMPTY_SKELETON)

    def test_it_is_silent_on_a_real_resume_body(self):
        assert og.check_near_empty(CLEAN_BODY) == []

    def test_the_violation_names_the_count_and_the_floor(self):
        detail = og.check_near_empty(NEAR_EMPTY_BODY)[0]
        assert str(og.NEAR_EMPTY_ANCHOR_FLOOR) in detail
        assert "anchors" in detail

    def test_the_preamble_alone_does_not_lift_a_blank_body_over_the_floor(self):
        """Measured: the shared preamble contributes 9 anchors, floor is 40.

        b45671b7ec5c scores 0 on its body and 9 on the whole spliced document,
        so the check holds whichever of the two a caller passes. If a future
        preamble grows past the floor this fails, which is the point -- the
        alternative is a check that silently starts passing blank documents.
        """
        preamble = (REPO / "resumes/fullstack.tex").read_text().split(
            r"\begin{document}")[0]
        assert og.check_near_empty(preamble + NEAR_EMPTY_BODY)


# ===========================================================================
# 2. Calibration. CLAUDE.md rule 16: measured before shipping, not after.
# ===========================================================================


class TestThresholdsStayWhereTheyWereMeasured:
    """Pins both directions of over-correction with the measured numbers.

    Over 740 real tailored resumes (exclude_untailored=True, so the 222 stored
    copies of the April base resume are out), body anchor counts run
    min 0, p1 133, p5 186, median 236, max 289 -- and the ONLY member below
    102 is b45671b7ec5c, the known defect. So the floor has to sit below 102
    (or it flags real resumes) and above 9 (or the spliced preamble alone
    clears it).
    """

    LOWEST_REAL_DOCUMENT = 102
    PREAMBLE_ONLY_ANCHORS = 9

    def test_floor_is_below_the_thinnest_real_resume(self):
        assert og.NEAR_EMPTY_ANCHOR_FLOOR < self.LOWEST_REAL_DOCUMENT, (
            "raising the floor above 102 flags real tailored resumes; measured "
            "2026-09-30 over 740 artifacts"
        )

    def test_floor_is_above_a_preamble_with_no_body(self):
        assert og.NEAR_EMPTY_ANCHOR_FLOOR > self.PREAMBLE_ONLY_ANCHORS

    def test_the_realistic_fixture_has_margin_over_the_floor(self):
        """Guards the instrument, not the code.

        PR #167 (open, fix/anchor-extraction-recall-bias) changes
        extract_anchors. If it lands and moves this fixture's count toward the
        floor, this fails and names the cause -- rather than every
        near_empty test quietly losing its margin.
        """
        found = assert_realistic()
        assert found > og.NEAR_EMPTY_ANCHOR_FLOOR * 2, (
            f"realistic body yields {found} anchors, floor is "
            f"{og.NEAR_EMPTY_ANCHOR_FLOOR}; margin has collapsed"
        )

    # Markers that were MEASURED and rejected. Each fires on real resumes at
    # the rate in the comment, and each appears in the leaked text -- so a
    # detector calibrated on the two known-bad documents would have chosen
    # them. This is the over-correction guard for prompt echo: adding any of
    # these back fails here.
    REJECTED_MARKERS = [
        ("we must ensure the header renders", "ensure"),        # 56/738  7.59%
        ("Header: Platform Engineer", "header"),                # 129/738 17.48%
        ("Optimised for keyword density", "keyword"),           # 1/738   0.14%
        ("A safer rollout strategy", "safer"),                  # 1/738   0.14%
    ]

    @pytest.mark.parametrize("text,word", REJECTED_MARKERS)
    def test_bare_words_measured_as_false_positives_are_not_markers(self, text, word):
        # The phrase around the word is resume-shaped, so if this fires it is
        # the bare word doing it.
        clean = re.sub(r"\bwe must\b", "the team will", text, flags=re.I)
        assert og.check_prompt_echo(clean) == [], (
            f"{word!r} was measured as a false positive on real resumes and "
            f"must not be a marker on its own"
        )

    @pytest.mark.parametrize("connective", [
        "However, throughput doubled.",
        "Thus latency fell by 40%.",
        "Similarly, error rates dropped.",
        "Alternatively, the job ran nightly.",
    ])
    def test_generic_discourse_connectives_are_not_markers(self, connective):
        """Measured at 0/738 and still refused, deliberately.

        These were in the incident report's word list. A marker's safety has
        to come from what it MEANS -- self-reference to the generation task --
        not only from one user's 738 documents. A summary that says "however"
        is not a leaked prompt.
        """
        assert og.check_prompt_echo(connective) == []


# ===========================================================================
# 3. Control flow through check_output. Rule 13: severity IS the behaviour.
# ===========================================================================


class TestCheckOutputBlocksRatherThanRecords:
    def test_prompt_echo_is_the_only_thing_blocking_an_otherwise_clean_document(self):
        """Isolates the cause, which a minimal fixture cannot.

        The first draft of the fabrication equivalent of this test passed
        under BOTH severities, because its fixture was already missing four
        required sections. So this one is built from a document where
        everything else is clean and only the echo is wrong: all six headers,
        balanced braces, production-like anchor density.
        """
        tex = CLEAN_BODY + "\nWe must reorder the base resume bullets.\n"
        result = og.check_output(tex, "tailor")
        assert _blocking(result) == {"prompt_echo"}
        assert result.passed is False

    def test_near_empty_is_the_only_thing_blocking_a_structurally_valid_skeleton(self):
        result = og.check_output(EMPTY_SKELETON, "tailor")
        assert og.check_brace_balance(EMPTY_SKELETON) is True
        assert og.check_required_sections(EMPTY_SKELETON) == [], (
            "the whole point of this fixture is that structure is clean"
        )
        assert _blocking(result) == {"near_empty"}
        assert result.passed is False

    def test_both_violations_carry_block_not_warn(self):
        for tex in (ECHO_PLANNING, ECHO_SKELETON):
            result = og.check_output(tex, "tailor")
            echo = [v for v in result.violations if v.rule == "prompt_echo"]
            assert echo and all(v.severity == "block" for v in echo)
        result = og.check_output(EMPTY_SKELETON, "tailor")
        empty = [v for v in result.violations if v.rule == "near_empty"]
        assert empty and all(v.severity == "block" for v in empty)

    def test_a_clean_realistic_body_still_passes(self):
        """The cost control. Neither check may fire on good output."""
        result = og.check_output(CLEAN_BODY, "tailor")
        assert result.passed is True
        assert result.violations == []


# ===========================================================================
# 4. The policy flags are real flags, not decoration.
# ===========================================================================


class TestPolicyFlagsAreConsumed:
    """guardrails/policy.py's docstring: a key nothing reads is worse than no
    key. It documents two prior instances (`fairness_cap`, `pii_scrub`). These
    tests are the general form of that rule rather than two more special cases.
    """

    def test_every_policy_carries_both_new_keys(self):
        for task, policy in POLICIES.items():
            assert "prompt_echo" in policy, task
            assert "near_empty" in policy, task

    def test_every_policy_key_is_read_somewhere_in_the_guardrails_package(self):
        pkg = REPO / "lambdas" / "pipeline" / "guardrails"
        source = "\n".join(p.read_text() for p in pkg.glob("*.py"))
        keys = {k for policy in POLICIES.values() for k in policy}
        unread = [k for k in sorted(keys)
                  if f'policy.get("{k}")' not in source
                  and f'policy["{k}"]' not in source
                  and f'["{k}"]' not in source]
        assert unread == [], (
            f"policy keys nothing in guardrails/ reads: {unread}. A flag that "
            "looks like a reviewable one-line toggle and changes nothing is "
            "the defect this test exists to prevent."
        )

    def test_turning_prompt_echo_off_removes_the_violation(self):
        tex = CLEAN_BODY + "\nWe must reorder the base resume bullets.\n"
        assert _blocking(og.check_output(tex, "tailor")) == {"prompt_echo"}
        with patch.dict(POLICIES["tailor"], {"prompt_echo": False}):
            assert og.check_output(tex, "tailor").passed is True

    def test_turning_near_empty_off_removes_the_violation(self):
        assert _blocking(og.check_output(EMPTY_SKELETON, "tailor")) == {"near_empty"}
        with patch.dict(POLICIES["tailor"], {"near_empty": False}):
            assert og.check_output(EMPTY_SKELETON, "tailor").passed is True

    def test_neither_check_runs_for_the_score_task(self):
        # score output is a JSON score object, not a document.
        assert og.check_output(ECHO_PLANNING, "score").passed is True
        assert og.check_output(NEAR_EMPTY_BODY, "score").passed is True

    def test_near_empty_is_off_for_cover_letters(self):
        """A cover letter is ~250 words and carries roughly 25 anchors.

        At the resume floor of 40 this would fire on every letter ever
        written. Off until it is measured against the real cover-letter
        population -- CLAUDE.md rule 7.
        """
        letter = ("Dear Hiring Manager, I am applying for the Platform "
                  "Engineer role at Acme. At Clover I ran Kubernetes on AWS "
                  "and cut MTTR by 35%. Regards, A Candidate.")
        assert og.check_near_empty(letter), "the letter really is under the floor"
        assert og.check_output(letter, "cover_letter").passed is True


# ===========================================================================
# 5. Control flow through the council graph: does anything CHANGE?
# ===========================================================================


class TestTheCouncilIsActuallyTold:
    """The seam fabrication was broken at, for these two checks.

    `quality_gate` reads `guard_report["passed"]`, which is
    `not any(severity == "block")`. At "warn" these return "finalize" and the
    document ships. These drive the REAL guard_output_node -> real
    check_output -> real quality_gate.
    """

    @staticmethod
    def _route(content):
        state = {"winner": {"content": content}, "task": "tailor",
                 "repair_attempts": 0}
        state.update(nodes.guard_output_node(state))
        return state, nodes.quality_gate(state)

    def test_prompt_echo_alone_routes_to_repair(self):
        """Echo is the ONLY thing wrong with this winner.

        The first version of this test used ECHO_PLANNING, whose leaked prose
        replaces the document -- it is missing four required sections, which
        block on their own. So it routed to "repair" under severity "warn" too,
        and proved nothing about prompt_echo. That is the exact trap commit
        55ebff2 recorded for the fabrication equivalent; mutation 1 (block ->
        warn) now fails here, and did not before.
        """
        state, route = self._route(CLEAN_BODY + "\nWe must reorder the base resume.\n")
        assert state["guard_report"]["passed"] is False
        assert route == "repair"

    def test_the_real_planning_prose_artifact_routes_to_repair(self):
        state, route = self._route(ECHO_PLANNING)
        assert state["guard_report"]["passed"] is False
        assert route == "repair"

    def test_the_placeholder_skeleton_routes_to_repair(self):
        _, route = self._route(ECHO_SKELETON)
        assert route == "repair"

    def test_near_empty_routes_to_repair(self):
        state, route = self._route(EMPTY_SKELETON)
        assert state["guard_report"]["passed"] is False
        assert route == "repair"

    def test_a_clean_body_still_finalizes_on_the_first_pass(self):
        state, route = self._route(CLEAN_BODY)
        assert state["guard_report"]["passed"] is True
        assert route == "finalize"

    def test_the_repair_prompt_names_what_to_remove(self):
        state, _ = self._route(CLEAN_BODY + "\nWe must reorder the base resume.\n")
        state["prompt"] = "tailor this resume"
        repaired = nodes.repair_node(state)
        assert "prompt_echo" in repaired["prompt"]
        assert "base resume" in repaired["prompt"], (
            "repair_node folds violation text verbatim into the retry; if the "
            "offending phrase is not in there the model is being told to fix "
            "something it cannot see"
        )

    def test_repair_is_bounded_so_neither_check_can_loop(self):
        state = {"winner": {"content": EMPTY_SKELETON}, "task": "tailor",
                 "repair_attempts": 2}
        state.update(nodes.guard_output_node(state))
        assert state["guard_report"]["passed"] is False
        # Which is exactly why check_output cannot be the only defence, and
        # why the hard gate below exists.
        assert nodes.quality_gate(state) == "finalize"


# ===========================================================================
# 6. The terminal guarantee: what actually reaches S3.
# ===========================================================================

_PREAMBLE = (
    "\\documentclass{article}\n"
    "\\newcommand{\\header}{Ada Lovelace \\\\ ada@example.com}\n"
    "\\newcommand{\\jobentry}[4]{#1 #2 #3 #4}\n"
    "\\newcommand{\\projectentry}[3]{#1 #2 #3}\n"
)

# A base resume that clears every gate itself, so a fallback to it is
# distinguishable from a tailored body that happened to look like it.
_BASE_BODY = (
    r"\section*{Summary}" "\n\\textbf{3+ years} building platforms.\n"
    r"\section*{Technical Skills}" f"\n{SKILLS_FLAT}\n"
    r"\section*{Experience}" "\n"
    r"\jobentry{Clover IT Services}{Dublin}{2023 -- 2026}{Engineer}" "\n"
    "Shipped 8 microservices; 99.9% uptime; cut MTTR 35%. "
    # 90, not 60: at 60 this body is 497 words and handler()'s
    # `word_count < 500` branch swaps in the base resume WITHOUT recording a
    # validation error, so `used_fallback` came back False for a run that
    # fell back. That branch is a pre-existing defect (CLAUDE.md rule 2) and
    # out of scope here; the fixture is sized past it so these cases measure
    # the gates they are about.
    + "Delivered weekly releases for the payments team. " * 90 + "\n"
    r"\section*{Featured Projects}" "\n"
    r"\projectentry{Purrrfect Match}{2025}{React Native}" "\n"
    r"\section*{Education}" "\nMSc Cloud Computing, Arlington, 2022.\n"
    r"\section*{Certifications}" "\nAWS Solutions Architect, 2025.\n"
)
_BASE_TEX = f"{_PREAMBLE}\\begin{{document}}\n{_BASE_BODY}\n\\end{{document}}\n"


def _db():
    def table(name):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.update.return_value = chain
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Run Kubernetes at scale for a payments platform."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [{"name": "Ada Lovelace", "email": "ada@example.com"}],
        }.get(name, [])
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


def _ship(council_body):
    """Run handler() and return (bytes written to S3, its return value)."""
    with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
         patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
         patch.object(tailor_resume, "ai_complete",
                      return_value={"content": "", "provider": "p", "truncated": False}), \
         patch.object(tailor_resume, "council_complete",
                      return_value={"content": council_body, "provider": "p",
                                    "model": "m"}):
        out = tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    put = mock_boto3.client.return_value.put_object
    assert put.call_args, "nothing was written to S3"
    return put.call_args.kwargs["Body"].decode(), out


class TestNothingBadReachesS3:
    """`check_output` blocking buys two repair attempts; `quality_gate` then
    finalizes best-effort (see test_repair_is_bounded_so_neither_check_can_loop).
    A route that ends in the same place whether the repair worked or was
    abandoned is not a gate -- CLAUDE.md rule 2. This is the gate.
    """

    def test_the_council_body_ships_when_it_is_clean(self):
        """Over-correction guard: the gates must not eat good output."""
        written, out = _ship(_BASE_BODY.replace(
            "building platforms", "building payment platforms"))
        assert out["used_fallback"] is False
        assert "building payment platforms" in written

    def test_a_body_carrying_prompt_echo_never_reaches_s3(self):
        written, out = _ship(_BASE_BODY + "\nWe must reorder the base resume.\n")
        assert out["used_fallback"] is True, (
            "the handler reported success on a document it should have discarded"
        )
        assert "We must reorder the base resume" not in written
        assert written == _BASE_TEX

    def test_the_placeholder_skeleton_never_reaches_s3(self):
        written, out = _ship(ECHO_SKELETON)
        assert out["used_fallback"] is True
        assert "... tailored ..." not in written

    def test_a_near_empty_body_never_reaches_s3(self):
        written, out = _ship(EMPTY_SKELETON)
        assert out["used_fallback"] is True
        assert written == _BASE_TEX

    def test_the_one_word_body_never_reaches_s3(self):
        """b45671b7ec5c exactly: \\begin{document} and \\end{document}.

        The stored artifact is 1177 bytes of preamble around the word "and".
        `is_latex_document` returns True for it, which is how it survived
        every existing validity check.
        """
        from shared.resume_format import is_latex_document

        real_shape = f"{_PREAMBLE}\\begin{{document}}\nand\n\\end{{document}}"
        assert is_latex_document(real_shape) is True, (
            "the premise: the existing validity check passes this document"
        )
        written, out = _ship(NEAR_EMPTY_BODY)
        assert out["used_fallback"] is True
        assert written == _BASE_TEX

    def test_the_failure_is_named_in_the_log(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _ship(EMPTY_SKELETON)
        assert "near-empty output" in caplog.text
        assert "falling back to base resume" in caplog.text
