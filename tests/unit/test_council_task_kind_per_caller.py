"""Regression: each production council caller passes its own task kind.

`agents/nodes.py` resolves its guardrail policy from `state.get("task",
"default")`, but until this task nothing upstream ever set `task` --
every production call through the LangGraph council path silently got the
"default" policy (injection_detection + pii_scrub + banned_phrases only).
The two guards that exist specifically for resume tailoring -- fabrication
and latex_structure -- were unreachable through the graph path as a result,
even though `guardrails/policy.py` has carried a "tailor" entry enabling
both all along.

`council_complete()` now accepts an optional `task` kwarg (default
"default", so any caller that doesn't pass it keeps prior behaviour). These
tests pin the three production callers to the specific value each one must
pass, so a future refactor of any of them cannot silently regress back to
the "default" fallback without a test failing here:

  - tailor_resume.py          -> "tailor"
  - generate_cover_letter.py  -> "cover_letter"
  - post_score.py             -> "score" (it only ever scores a tailored
    resume against a JD and returns JSON; "score" turns off banned_phrases,
    which "default" would leave on against JSON output for no benefit)

Also pins tailor_resume.py's guard-context wiring: base_skills/base_body ARE
forwarded (so guard_output_node's fabrication and \\textbf-preservation
checks run against this candidate's real base resume, not an empty
baseline), but header_markers is deliberately withheld -- see the comment
at that call site for why forwarding it would arm a check that can never
pass (the graph only ever sees the pre-splice, header-less tailored body).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))

import generate_cover_letter  # noqa: E402
import post_score  # noqa: E402
import tailor_resume  # noqa: E402

BASE_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section*{Summary}\nBase summary.\n"
    "\\section*{Technical Skills}\nPython, AWS, Docker\n"
    "\\section*{Experience}\nDid things at Acme.\n"
    "\\section*{Featured Projects}\nBuilt things.\n"
    "\\section*{Education}\nBSc Computer Science.\n"
    "\\section*{Certifications}\nAWS Certified.\n"
    "\\end{document}\n"
)


def _table_mock(rows_by_table: dict) -> MagicMock:
    """Minimal Supabase stub: db.table(name).select()....execute().data."""
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


def test_tailor_resume_passes_task_tailor():
    db = _table_mock({
        "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                      "description": "Run Kubernetes at scale."}],
        "user_resumes": [{"tex_content": BASE_TEX}],
        "users": [{"name": "Test User", "email": "test@example.com"}],
    })
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()), \
         patch.object(tailor_resume, "council_complete") as mock_council:
        mock_council.return_value = {"content": "irrelevant for this test", "provider": "p", "model": "m"}
        try:
            tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
        except Exception:
            pass  # only the kwargs handed to council_complete matter here

    assert mock_council.called, "council_complete was never reached"
    assert mock_council.call_args.kwargs["task"] == "tailor"


def test_tailor_resume_forwards_base_skills_and_base_body_but_not_header_markers():
    """base_skills/base_body come from THIS candidate's real base resume, so
    guard_output_node's fabrication/textbf checks have a real baseline
    instead of silently passing everything against an empty one.
    header_markers is deliberately absent -- see tailor_resume.py's call
    site comment: the graph only ever evaluates the pre-splice body, which
    structurally never contains the header markers (they live only in the
    preamble, spliced in after council_complete returns), so forwarding it
    would arm a check that can never pass rather than skip meaninglessly.
    """
    db = _table_mock({
        "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                      "description": "Run Kubernetes at scale."}],
        "user_resumes": [{"tex_content": BASE_TEX}],
        "users": [{"name": "Test User", "email": "test@example.com"}],
    })
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()), \
         patch.object(tailor_resume, "council_complete") as mock_council:
        mock_council.return_value = {"content": "irrelevant for this test", "provider": "p", "model": "m"}
        try:
            tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
        except Exception:
            pass

    assert mock_council.called, "council_complete was never reached"
    kwargs = mock_council.call_args.kwargs
    assert "Python, AWS, Docker" in kwargs["base_skills"]
    assert "Did things at Acme." in kwargs["base_body"]
    assert kwargs.get("header_markers") is None


def test_generate_cover_letter_passes_task_cover_letter():
    db = _table_mock({
        "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                      "description": "Run Kubernetes at scale."}],
        "user_resumes": [{"tex_content": BASE_TEX}],
    })
    with patch.object(generate_cover_letter, "get_supabase", return_value=db), \
         patch.object(generate_cover_letter, "boto3", MagicMock()), \
         patch.object(generate_cover_letter, "council_complete") as mock_council:
        mock_council.return_value = {"content": "Paragraph one. Paragraph two.", "provider": "p", "model": "m"}
        try:
            generate_cover_letter.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
        except Exception:
            pass  # validation/retry/S3 plumbing beyond this isn't the point of this test

    assert mock_council.called, "council_complete was never reached"
    # A short stub body almost certainly fails _validate_cover_letter and
    # triggers retries -- assert every attempt carried the right task, not
    # just the first, so a "only the initial call is tagged" regression
    # would also be caught.
    assert all(call.kwargs.get("task") == "cover_letter" for call in mock_council.call_args_list)


def test_post_score_passes_task_score():
    """post_score.py only scores a tailored resume against a JD and returns
    JSON -- confirmed by reading handler() (see module docstring), not
    guessed from the filename. "score" turns off banned_phrases (which
    "default" leaves on) -- irrelevant, possibly noisy, against JSON output.
    """
    db = _table_mock({
        "jobs": [{"resume_s3_key": "resumes/u-1/abc123.pdf"}],
        "jobs_raw": [{"description": "Run Kubernetes at scale.", "title": "SRE", "company": "Acme"}],
    })
    s3 = MagicMock()
    body = MagicMock()
    body.read.return_value = BASE_TEX.encode("utf-8")
    s3.get_object.return_value = {"Body": body}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = s3

    with patch.object(post_score, "get_supabase", return_value=db), \
         patch.object(post_score, "boto3", boto3_mock), \
         patch.object(post_score, "council_complete") as mock_council:
        mock_council.return_value = {"content": "not valid json", "provider": "p", "model": "m"}
        result = post_score.handler({"job_hash": "abc123", "user_id": "u-1"}, None)

    assert mock_council.called, "council_complete was never reached"
    assert mock_council.call_args.kwargs["task"] == "score"
    # Sanity: the stub content is intentionally unparseable, so the handler
    # should report a clean parse_error, not have crashed some other way.
    assert result == {"job_hash": "abc123", "user_id": "u-1", "scored": False, "reason": "parse_error"}
