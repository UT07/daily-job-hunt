"""The tailor path must ask for a budget big enough to hold a resume.

`tailor_resume.handler` sends the whole base resume body to the council and
ends the prompt with "Return ONLY the tailored body" — the answer is a
re-emission of a document, not a paragraph. It used to inherit the council's
hardcoded max_tokens=4096 anyway. When a model ran out mid-document the
returned fragment was indistinguishable from a finished answer, so the hard
gates further down rejected it for "missing sections" and silently swapped in
the untailored base resume (`used_fallback: True`) — the user gets their old
resume back and the log says the model dropped four sections.

The quality retry below the gates had the same shape and a sharper edge: it
accepts a retry when it carries FEWER warnings than the council's body, and a
body that stops halfway trivially contains fewer banned phrases than a
complete one. Nothing re-ran the structural gates on it, so a truncated retry
could overwrite a valid tailored resume.

See tests/unit/test_ai_truncation.py for the provider-layer half of this.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))

import ai_helper  # noqa: E402
import tailor_resume  # noqa: E402

# A base resume long enough that a real budget calculation exceeds 4096 —
# roughly the size of the ones in resumes/ (11.4k-12.8k characters).
_FILLER = "Delivered measurable outcomes on production systems. " * 240
BASE_TEX = (
    # The name/email live in the PREAMBLE, which handler() splices back on
    # after the council returns — `_check_header_present` runs against the
    # spliced document, so without them every case here would fall back to
    # the base resume before reaching the code under test.
    "\\documentclass{article}\n"
    "\\newcommand{\\header}{Test User \\\\ test@example.com}\n"
    "\\begin{document}\n"
    "\\section*{Summary}\nBase summary.\n"
    "\\section*{Technical Skills}\nPython, AWS, Docker\n"
    f"\\section*{{Experience}}\n\\textbf{{Acme}} — {_FILLER}\n"
    "\\section*{Featured Projects}\nBuilt things.\n"
    "\\section*{Education}\nBSc Computer Science.\n"
    "\\section*{Certifications}\nAWS Certified.\n"
    "\\end{document}\n"
)


def _table_mock(rows_by_table: dict) -> MagicMock:
    def table(name):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        result = MagicMock()
        result.data = rows_by_table.get(name, [])
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


def _db():
    return _table_mock({
        "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                      "description": "Run Kubernetes at scale."}],
        "user_resumes": [{"tex_content": BASE_TEX}],
        "users": [{"name": "Test User", "email": "test@example.com"}],
    })


def _run(council_return, ai_complete_return=None):
    """Drive handler() far enough to capture what it asked the council for."""
    with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
         patch.object(tailor_resume, "boto3", MagicMock()), \
         patch.object(tailor_resume, "ai_complete",
                      return_value=ai_complete_return or {"content": "", "provider": "p"}) as mock_ai, \
         patch.object(tailor_resume, "council_complete",
                      return_value=council_return) as mock_council:
        try:
            tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
        except Exception:
            pass  # S3/DB plumbing past the council call is not what these pin
    return mock_council, mock_ai


class TestCouncilBudget:
    def test_handler_sizes_max_tokens_from_the_base_resume_body(self):
        council, _ = _run({"content": "body", "provider": "p", "model": "m"})
        assert council.called, "council_complete was never reached"
        kwargs = council.call_args.kwargs
        assert kwargs["max_tokens"] == ai_helper.rewrite_budget(kwargs["base_body"])

    def test_the_budget_it_asks_for_exceeds_the_old_hardcoded_default(self):
        # The regression in one assertion: for a resume this size, 4096 is
        # not enough, and 4096 is exactly what this call used to get.
        council, _ = _run({"content": "body", "provider": "p", "model": "m"})
        assert council.call_args.kwargs["max_tokens"] > 4096

    def test_the_quality_retry_gets_the_same_budget_as_the_council_call(self):
        # The retry asks for the whole body again. Leaving it on
        # ai_complete's 4096 default would truncate it for exactly the
        # reason the council call no longer is.
        council, mock_ai = _run(
            # A body that passes the structural gates but trips a banned
            # phrase, so the quality retry actually fires.
            {"content": BASE_TEX.split("\\begin{document}")[1]
                .replace("Base summary.", "A robust summary.")
                .replace("\\end{document}", ""),
             "provider": "p", "model": "m"},
            ai_complete_return={"content": "retry body", "provider": "p", "truncated": False},
        )
        assert mock_ai.called, "the quality retry never fired"
        assert mock_ai.call_args.kwargs["max_tokens"] == council.call_args.kwargs["max_tokens"]


class TestTruncatedRetryIsRejected:
    """`len(retry_quality) < len(quality_warnings)` is a style comparison.
    A retry that got its lower count by stopping early must never reach it.

    Each case here is arranged so the incomplete retry WINS that comparison:
    the council's body carries two banned phrases, the retry carries none,
    and the retry keeps the base resume's one `\\textbf` so the preservation
    check stays quiet too. 0 < 2 — so before this fix the Summary-only
    fragment replaced a structurally valid tailored resume, and the hard
    gates never looked at it because they had already run on the council's
    body further up.
    """

    @staticmethod
    def _council_body_with_banned_phrases():
        body = BASE_TEX.split("\\begin{document}")[1].replace("\\end{document}", "")
        return body.replace("Base summary.", "A robust summary with seamless delivery.")

    def _retry(self, retry_dict):
        with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
             patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
             patch.object(tailor_resume, "ai_complete", return_value=retry_dict), \
             patch.object(tailor_resume, "council_complete",
                          return_value={"content": self._council_body_with_banned_phrases(),
                                        "provider": "p", "model": "m"}):
            try:
                tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
            except Exception:
                pass
            put = mock_boto3.client.return_value.put_object
            return put.call_args.kwargs["Body"].decode() if put.call_args else ""

    # Keeps the base resume's single \textbf, so check_textbf_preservation
    # reports nothing and the fragment's warning count really is zero.
    _FRAGMENT = "\\section*{Summary}\n\\textbf{Acme} shipped things."

    def test_a_retry_the_provider_cut_off_is_discarded(self):
        written = self._retry({"content": self._FRAGMENT, "provider": "p", "truncated": True})
        assert "Certifications" in written, "the council's complete body was replaced"
        assert "shipped things." not in written

    def test_a_retry_missing_required_sections_is_discarded(self):
        # Not flagged as truncated — a model can also just answer short.
        # Structure is checked on its own merits either way.
        written = self._retry({"content": self._FRAGMENT, "provider": "p", "truncated": False})
        assert "Certifications" in written, "the council's complete body was replaced"
        assert "shipped things." not in written

    def test_a_complete_retry_that_fixes_the_warnings_is_still_accepted(self):
        # The guard above must not break the feature it protects: a retry
        # that is complete AND cleaner still wins.
        clean = self._council_body_with_banned_phrases().replace(
            "A robust summary with seamless delivery.", "A rebuilt summary.")
        written = self._retry({"content": clean, "provider": "p", "truncated": False})
        assert "A rebuilt summary." in written


def test_handler_logs_when_the_council_winner_was_cut_off(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        _run({"content": "\\section*{Summary}\nCut off here",
              "provider": "p", "model": "m", "truncated": True})
    # Without this the fallback log reads as "the model ignored the
    # instructions" when the model never got to finish.
    assert "cut off at max_tokens" in caplog.text
