r"""A fabricated technology is cut out of the tailored résumé, not the tailoring.

THE REQUIREMENT, in the user's words: "I don't want fabricated shit but at the
same time I do want the tailored resumes". Since 98aca50 a fabrication that
survived the council and the quality retry sent the job to the corpus
fallback, which discards the whole tailoring for one token in a list. The
detector (`check_fabrication`) only ever flags a name from a fixed 17-token
blocklist, in the header subtitle and the Skills section, so what it flags is
almost always one entry in a comma list -- an entry that can simply be removed.

So now: strip exactly what was flagged, from exactly the regions it was
flagged in, with the detector's own boundary matcher; re-run the FULL gate set
(`_validation_errors` and the fabrication check, CLAUDE.md #14) on the result;
ship it tailored if it passes, record what was removed, and keep the corpus
fallback for everything that cannot be removed cleanly.

Every handler test asserts an OUTCOME (CLAUDE.md #13): the bytes written to
S3, `used_fallback`, `fabrications_stripped`, the verdict grade. The detector
and matcher are the real ones throughout; the only stripper double is in
`TestTheStrippedDocumentIsRechecked`, and it is proven sound before it is used.
"""
from __future__ import annotations

import logging
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402
from guardrails.output_guards import (  # noqa: E402
    _mentions,
    check_fabrication,
    strip_fabrications,
)
from shared.resume_verdict import from_step_results  # noqa: E402

from tests.unit.realistic_resume_body import body as realistic_body  # noqa: E402

_PREAMBLE = (
    "\\documentclass{article}\n"
    "\\newcommand{\\jobentry}[4]{#1 #2 #3 #4}\n"
    "\\newcommand{\\projectentry}[3]{#1 #2 #3}\n"
)
_BASE_TEX = (f"{_PREAMBLE}\\begin{{document}}\n"
             f"{realistic_body(summary='THE UNTAILORED CORPUS SUMMARY.')}\n"
             "\\end{document}\n")
_MARKERS = ["Utkarsh Singh", "254utkarsh@gmail.com"]

_CLEAN = realistic_body(summary="Payments engineer on the council.")
_PRIMARY = "Python, AWS, Docker"


def _with(primary: str, body: str = _CLEAN) -> str:
    """The council body with its `Primary:` skills line rewritten."""
    assert _PRIMARY in body
    return body.replace(_PRIMARY, primary)


def _db():
    def table(name):
        chain = MagicMock()
        for attr in ("select", "eq", "order", "limit", "update", "or_"):
            getattr(chain, attr).return_value = chain
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Rust and Kubernetes for payments."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [{"name": "Utkarsh Singh", "email": "254utkarsh@gmail.com"}],
            "jobs": [{"job_hash": "abc123"}],
        }.get(name, [])
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


def _run(council_body, retries=None):
    """Run the real handler. Every retry returns the council's body unchanged,
    so the quality retry cannot be what removes the fabrication."""
    retries = [council_body] if retries is None else retries
    responses = iter({"content": r, "provider": "p", "truncated": False} for r in retries)
    ai = MagicMock(side_effect=lambda *a, **k: next(
        responses, {"content": "", "provider": "p", "truncated": False}))
    with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
         patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
         patch.object(tailor_resume, "ai_complete", ai), \
         patch.object(tailor_resume, "council_complete",
                      return_value={"content": council_body, "provider": "p",
                                    "model": "m", "critique_outcome": "adjudicated",
                                    "guard_report": {"passed": False,
                                                     "violations": [], "blocking": []}}):
        out = tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    written = mock_boto3.client.return_value.put_object.call_args.kwargs["Body"].decode()
    return written, out


def _shipped(body: str) -> str:
    """The exact bytes the handler writes for `body`: the corpus's own
    preamble (as the handler splits it off) spliced onto `body`."""
    preamble, _ = tailor_resume._split_tex(_BASE_TEX)
    return tailor_resume._assemble(preamble, body)


# ---------------------------------------------------------------------------
# The doubles: the fixture bodies are what the tests below say they are.
# ---------------------------------------------------------------------------

class TestTheFixturesAreSound:
    def test_the_shipped_helper_is_what_the_handler_writes(self):
        """Otherwise "byte-identical" below compares against the wrong bytes."""
        written, out = _run(_CLEAN, [])
        assert written == _shipped(_CLEAN)
        assert out["used_fallback"] is False

    def test_the_clean_body_clears_every_gate(self):
        assert tailor_resume._validation_errors(_shipped(_CLEAN), _CLEAN, _MARKERS) == []
        assert check_fabrication(_BASE_TEX, _CLEAN) == []

    @pytest.mark.parametrize("token", ["Rust", "Java", "Scala", "Swift"])
    def test_each_token_is_unsupported_by_the_corpus(self, token):
        assert not _mentions(token, _BASE_TEX), f"{token} is in the corpus; not a fabrication"

    def test_the_corpus_does_carry_the_lookalikes(self):
        """The lookalike tests are only meaningful if the near-miss is really
        there next to the fabrication."""
        assert "TypeScript/JavaScript" in _CLEAN

    def test_go_is_not_on_the_blocklist(self):
        """Why there is no "Go next to Google/MongoDB" case: the detector never
        flags Go, so nothing would ever be stripped (and #16 forbids adding it
        here). Scala/Scalable and Swift/SwiftUI exercise the same boundary."""
        assert check_fabrication(_BASE_TEX, _with("Go, Google Cloud, MongoDB")) == []


# ---------------------------------------------------------------------------
# The stripper on its own: precise, and conservative.
# ---------------------------------------------------------------------------

def _strip(primary: str):
    return strip_fabrications(_BASE_TEX, _with(primary))


class TestSeparators:
    @pytest.mark.parametrize("fabricated, expected", [
        ("Rust, Python, AWS, Docker", "Python, AWS, Docker"),           # first
        ("Python, Rust, AWS, Docker", "Python, AWS, Docker"),           # middle
        ("Python, AWS, Docker, Rust", "Python, AWS, Docker"),           # last
        ("Python, AWS, Docker, and Rust", "Python, AWS, Docker"),       # oxford and
        ("Python, AWS and Rust, Docker", "Python, AWS, Docker"),         # and binds tighter
        ("Python / Rust / AWS, Docker", "Python / AWS, Docker"),        # slash
        ("Python | Rust | AWS | Docker", "Python | AWS | Docker"),       # pipe
        (r"Python \& Rust, AWS, Docker", "Python, AWS, Docker"),        # \& binds tighter
        ("Python (Rust, Flask), AWS, Docker", "Python (Flask), AWS, Docker"),
        ("Python (Flask, Rust), AWS, Docker", "Python (Flask), AWS, Docker"),
        ("Python (Rust), AWS, Docker", "Python, AWS, Docker"),          # no empty ()
        ("Python, Rust/TypeScript, AWS, Docker", "Python, TypeScript, AWS, Docker"),
        ("Python, TypeScript/Rust, AWS, Docker", "Python, TypeScript, AWS, Docker"),
        ("Python, AWS, Docker, Rust.", "Python, AWS, Docker."),
    ])
    def test_the_token_and_exactly_one_separator_go(self, fabricated, expected):
        result = _strip(fabricated)
        assert result.tex == _with(expected), result.reason
        assert result.removed == ("Rust",)

    def test_two_fabrications_on_one_line(self):
        result = _strip("Kotlin, Python, AWS, Rust, Docker")
        assert result.tex == _with("Python, AWS, Docker")
        assert sorted(result.removed) == ["Kotlin", "Rust"]

    def test_the_surface_form_is_recorded_as_written(self):
        assert _strip("Python, AWS, Docker, Vue.js").removed == ("Vue.js",)


class TestTheEntryCarriesItsOwnQualifier:
    """The commonest unstrippable shapes in 740 real résumés, before these
    were handled: a qualifier that is part of the same fabricated entry."""

    @pytest.mark.parametrize("fabricated, expected, removed", [
        ("Python, Rust (learning), AWS, Docker", "Python, AWS, Docker", ("Rust",)),
        ("Python, AWS, Docker, Angular 12+", "Python, AWS, Docker", ("Angular",)),
        ("Python, Angular.js, AWS, Docker", "Python, AWS, Docker", ("Angular",)),
        ("Python, Ruby on Rails, AWS, Docker", "Python, AWS, Docker", ("Rails", "Ruby")),
        ("Python, Ruby (Rails), AWS, Docker", "Python, AWS, Docker", ("Rails", "Ruby")),
    ])
    def test_the_whole_entry_goes(self, fabricated, expected, removed):
        result = _strip(fabricated)
        assert result.tex == _with(expected), result.reason
        assert result.removed == removed

    def test_on_only_joins_two_flagged_claims(self):
        """"Rust on AWS" is a sentence, not a compound: AWS is the candidate's."""
        assert _strip("Python, Rust on AWS, Docker").tex is None

    def test_a_nested_or_marked_up_qualifier_is_not_swallowed(self):
        assert _strip(r"Python, AWS, Docker, Rust (\textbf{core})").tex is None


class TestLookalikesAreNotTouched:
    def test_java_next_to_javascript(self):
        fabricated = _with("Java, Python, AWS, Docker")
        result = strip_fabrications(_BASE_TEX, fabricated)
        assert result.tex == _CLEAN
        assert result.removed == ("Java",)
        assert "TypeScript/JavaScript" in result.tex

    def test_javascript_alone_is_not_a_fabrication_and_is_not_stripped(self):
        assert check_fabrication(_BASE_TEX, _with("JavaScript, Python, AWS, Docker")) == []
        result = strip_fabrications(_BASE_TEX, _with("JavaScript, Python, AWS, Docker"))
        assert result.removed == ()

    def test_scala_next_to_scalable(self):
        result = _strip("Scalable Systems, Scala, Python, AWS, Docker")
        assert result.tex == _with("Scalable Systems, Python, AWS, Docker")
        assert result.removed == ("Scala",)

    def test_swift_next_to_swiftui(self):
        result = _strip("SwiftUI, Python, Swift, AWS, Docker")
        assert result.tex == _with("SwiftUI, Python, AWS, Docker")


class TestSingleItemCategory:
    def test_a_category_holding_only_the_fabrication_is_dropped(self):
        body = _CLEAN.replace(
            r"\item \textbf{CI/CD:}",
            r"\item \textbf{Systems:} Rust" r"\item \textbf{CI/CD:}")
        result = strip_fabrications(_BASE_TEX, body)
        assert result.tex == _CLEAN, result.reason
        assert result.removed == ("Rust",)

    def test_the_only_item_of_a_list_is_not_dropped(self):
        """Dropping it would leave an empty itemize -- a LaTeX error. Fall back."""
        body = _CLEAN.replace(
            r"\section*{Experience}",
            r"\begin{itemize}\item \textbf{Systems:} Rust\end{itemize}\section*{Experience}")
        result = strip_fabrications(_BASE_TEX, body)
        assert result.tex is None


class TestWhatCannotBeStrippedFallsBack:
    @pytest.mark.parametrize("fabricated", [
        "Python, AWS, Docker; built payment services in Rust for low latency",
        "Python, AWS, Docker, Rust-based tooling",
        r"Python, AWS, Docker, \textbf{Rust}",
    ])
    def test_prose_or_decorated_tokens(self, fabricated):
        assert _strip(fabricated).tex is None

    def test_a_claim_also_made_outside_the_flagged_regions(self):
        """The Skills entry can be removed, but the same claim in a project's
        tech list is outside what the detector inspects and what this may
        edit. Stripping one copy would ship the other."""
        body = _with("Python, AWS, Docker, Rust").replace(
            "{React Native, Expo, Firebase}", "{React Native, Rust, Firebase}")
        assert _strip_outside_is_refused(body)


def _strip_outside_is_refused(body: str) -> bool:
    result = strip_fabrications(_BASE_TEX, body)
    return result.tex is None and "outside" in result.reason


class TestHeaderSubtitle:
    _H = r"{\normalsize Software Engineer (SRE, %s, AWS)}\\"

    def _body(self, inner):
        return (r"\begin{center}" + self._H % inner + r"\end{center}") + _CLEAN

    def test_slash_pair_in_the_header(self):
        result = strip_fabrications(_BASE_TEX, self._body("Rust/TypeScript"))
        assert result.tex == self._body("TypeScript")

    def test_a_header_only_fabrication(self):
        body = (r"\begin{center}{\normalsize Software Engineer (Rust)}\\\end{center}"
                + _CLEAN)
        result = strip_fabrications(_BASE_TEX, body)
        assert result.tex == (r"\begin{center}{\normalsize Software Engineer}\\\end{center}"
                              + _CLEAN)


# ---------------------------------------------------------------------------
# The handler: what ships.
# ---------------------------------------------------------------------------

class TestAStrippableFabricationShipsTailored:
    def test_rust_is_removed_and_the_tailoring_survives_byte_for_byte(self):
        written, out = _run(_with("Python, AWS, Docker, Rust"))
        assert written == _shipped(_CLEAN), "anything but the token changed"
        assert out["used_fallback"] is False
        assert out["fabrications_stripped"] == ["Rust"]
        assert out["shipped_from"] == "council"

    def test_java_next_to_javascript_ships_tailored(self):
        written, out = _run(_with("Java, Python, AWS, Docker"))
        assert written == _shipped(_CLEAN)
        assert "TypeScript/JavaScript" in written
        assert out["fabrications_stripped"] == ["Java"]
        assert out["used_fallback"] is False

    def test_the_summary_line_says_what_was_removed(self, caplog):
        with caplog.at_level(logging.INFO):
            _run(_with("Python, AWS, Docker, Rust"))
        summary = [r.getMessage() for r in caplog.records
                   if "tailor for abc123" in r.getMessage()]
        assert len(summary) == 1
        assert "fallback" not in summary[0]
        assert "stripped" in summary[0] and "Rust" in summary[0], summary[0]

    def test_the_verdict_does_not_read_as_a_clean_pass(self):
        """The document was edited after every check that judged it; the edit
        is a measured, advisory `writing` finding -> `warn`, never `pass`."""
        _, out = _run(_with("Python, AWS, Docker, Rust"))
        verdict = from_step_results(out, {"page_violations": [], "ats_violations": []})
        assert verdict.grade == "warn", verdict.to_row()
        assert any("Rust" in r and "stripped" in r for r in verdict.reasons)

    def test_the_edit_note_is_not_itself_a_blocking_finding(self):
        _, out = _run(_with("Python, AWS, Docker, Rust"))
        assert tailor_resume._blocking_findings(out["quality_warnings"]) == []

    def test_a_clean_run_records_nothing_stripped(self):
        _, out = _run(_CLEAN, [])
        assert out["fabrications_stripped"] == []
        assert out["used_fallback"] is False


class TestAnUnstrippableFabricationStillFallsBack:
    def test_prose_in_skills_ships_the_corpus(self):
        written, out = _run(_with(
            "Python, AWS, Docker; built payment services in Rust for low latency"))
        assert "Rust" not in written
        assert "THE UNTAILORED CORPUS SUMMARY" in written
        assert out["used_fallback"] is True
        assert out["fabrications_stripped"] == []
        assert out["quality_warnings"] is None

    def test_a_claim_outside_the_regions_ships_the_corpus(self):
        body = _with("Python, AWS, Docker, Rust").replace(
            "{React Native, Expo, Firebase}", "{React Native, Rust, Firebase}")
        written, out = _run(body)
        assert "Rust" not in written
        assert out["used_fallback"] is True


class TestTheStrippedDocumentIsRechecked:
    """Proven with a stripper double whose output breaks one gate each.
    Each double is checked against the real gates first (CLAUDE.md #6): it
    must fail exactly the gate under test, so the fallback below can only be
    the re-check's doing."""

    _FAB = _with("Python, AWS, Docker, Rust")

    def _with_stripper_returning(self, body):
        from guardrails.output_guards import StrippedFabrications
        fake = MagicMock(return_value=StrippedFabrications(body, ("Rust",), ""))
        with patch.object(tailor_resume, "_strip_fabrications", fake):
            written, out = _run(self._FAB)
        assert fake.called, "the stripper double was never consulted"
        return written, out

    def test_a_stripped_body_breaking_a_hard_gate_falls_back(self):
        broken = _CLEAN.replace(r"\section*{Education}", r"\section*{Education}{")
        assert tailor_resume._validation_errors(_shipped(broken), broken, _MARKERS) == [
            "brace imbalance"], "double: must fail the brace gate and only it"
        assert check_fabrication(_BASE_TEX, broken) == [], "double: no fabrication left"
        written, out = self._with_stripper_returning(broken)
        assert out["used_fallback"] is True
        assert "THE UNTAILORED CORPUS SUMMARY" in written
        assert out["fabrications_stripped"] == []

    def test_a_stripped_body_still_carrying_a_fabrication_falls_back(self):
        still = _with("Python, AWS, Docker, Kotlin")
        assert tailor_resume._validation_errors(_shipped(still), still, _MARKERS) == []
        assert check_fabrication(_BASE_TEX, still), "double: must still fabricate"
        written, out = self._with_stripper_returning(still)
        assert out["used_fallback"] is True
        assert "Kotlin" not in written
