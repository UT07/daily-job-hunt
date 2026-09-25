"""Evidence-pool injection: ground tailoring in the candidate's own resume bullets.

BULLET_RAG defaults off. When on, bullets retrieved from the candidate's own
resume (retrieval.bullets.retrieve_evidence) are appended to the tailoring
prompt as a bounded evidence pool the model must draw claims from instead of
inventing facts -- a hallucination-mitigation control whose effect Task 18
measures via the existing _check_fabrication guard.

Retrieval is an enhancement, never a dependency: if the vector store is
unreachable, `safe_evidence_block` must swallow the failure and return ""
so tailoring proceeds exactly as it did before this feature existed.
"""
from unittest.mock import MagicMock, patch

from lambdas.pipeline import tailor_resume


def test_evidence_block_lists_every_bullet():
    block = tailor_resume.build_evidence_block([
        {"section": "Experience", "text": "Built a Python service."},
        {"section": "Projects", "text": "Shipped a LaTeX pipeline."},
    ])
    assert "Built a Python service." in block
    assert "Shipped a LaTeX pipeline." in block


def test_evidence_block_states_the_no_invention_rule():
    # The whole point of the pool is to bound the model to real facts.
    block = tailor_resume.build_evidence_block([{"section": "X", "text": "y"}])
    assert "do not invent" in block.lower()


def test_evidence_block_is_empty_string_when_no_bullets():
    # No pool must mean no prompt change at all, not an empty header.
    assert tailor_resume.build_evidence_block([]) == ""


def test_safe_evidence_block_is_off_by_default(monkeypatch):
    """BULLET_RAG unset must never even attempt retrieval.

    Asserts retrieve_evidence is not called at all when the flag is off (the
    default) -- not just that the return value happens to be "".
    """
    monkeypatch.delenv("BULLET_RAG", raising=False)
    with patch.object(tailor_resume, "retrieve_evidence") as mock_retrieve:
        assert tailor_resume.safe_evidence_block("u1", "jd") == ""
    mock_retrieve.assert_not_called()


def test_retrieval_failure_does_not_break_tailoring(monkeypatch):
    """Pin the try/except's whole reason for existing.

    The brief's version of this test left BULLET_RAG unset, which would make
    it pass vacuously: safe_evidence_block returns "" from the flag-off
    short-circuit before ever reaching the try/except, so a RuntimeError
    patched onto retrieve_evidence would never actually be exercised.
    BULLET_RAG=on here so the failure path is genuinely covered.
    """
    monkeypatch.setenv("BULLET_RAG", "on")
    with patch.object(tailor_resume, "retrieve_evidence", side_effect=RuntimeError("down")):
        assert tailor_resume.safe_evidence_block("u1", "jd") == ""


def test_safe_evidence_block_returns_block_when_flag_on_and_retrieval_succeeds(monkeypatch):
    monkeypatch.setenv("BULLET_RAG", "on")
    with patch.object(
        tailor_resume,
        "retrieve_evidence",
        return_value=[{"section": "Experience", "text": "Built a Python service."}],
    ) as mock_retrieve:
        block = tailor_resume.safe_evidence_block("u1", "jd text", k=8)
    assert "Built a Python service." in block
    mock_retrieve.assert_called_once_with("u1", "jd text", k=8)


def _db_with(job_rows, resume_rows=None, user_rows=None):
    """Minimal Supabase stub: db.table(...).select(...)....execute().

    Mirrors tests/unit/test_tailor_resume_raises.py's _db_with, extended
    with a `users` table branch for _derive_header_markers.
    """
    def table(name):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        result = MagicMock()
        if name == "jobs_raw":
            result.data = job_rows
        elif name == "user_resumes":
            result.data = resume_rows if resume_rows is not None else []
        elif name == "users":
            result.data = user_rows if user_rows is not None else []
        else:
            result.data = []
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


BASE_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section*{Summary}\nBase summary.\n"
    "\\end{document}\n"
)


def test_handler_appends_evidence_block_to_the_council_prompt(monkeypatch):
    """The block must actually reach the model, not just exist as dead code.

    Wires a minimal handler() invocation with BULLET_RAG=on and asserts the
    retrieved bullet's own text is present in the exact `prompt` string
    handed to council_complete -- the string Task 18 will measure for a
    fabrication-rate delta. If this assertion ever passes without the
    retrieved text in the prompt, the wiring is a silent no-op.
    """
    monkeypatch.setenv("BULLET_RAG", "on")
    db = _db_with(
        job_rows=[{"job_hash": "abc123", "title": "SRE", "company": "Acme", "description": "d"}],
        resume_rows=[{"tex_content": BASE_TEX}],
        user_rows=[{"name": "Test User", "first_name": "Test", "last_name": "User",
                    "email": "test@example.com"}],
    )
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()), \
         patch.object(
             tailor_resume, "retrieve_evidence",
             return_value=[{"section": "Experience", "text": "Cut p95 latency 40 percent."}],
         ), \
         patch.object(tailor_resume, "council_complete") as mock_council:
        mock_council.return_value = {"content": "irrelevant for this test"}
        try:
            tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
        except Exception:
            pass  # only the prompt handed to council_complete matters here

    assert mock_council.called, "council_complete was never reached"
    prompt_seen = mock_council.call_args.kwargs["prompt"]
    assert "Cut p95 latency 40 percent." in prompt_seen


def test_handler_prompt_unchanged_when_flag_off(monkeypatch):
    """Symmetric check: with BULLET_RAG off, the prompt carries no evidence
    pool at all, confirming the feature is inert until explicitly enabled.
    """
    monkeypatch.delenv("BULLET_RAG", raising=False)
    db = _db_with(
        job_rows=[{"job_hash": "abc123", "title": "SRE", "company": "Acme", "description": "d"}],
        resume_rows=[{"tex_content": BASE_TEX}],
        user_rows=[{"name": "Test User", "first_name": "Test", "last_name": "User",
                    "email": "test@example.com"}],
    )
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()), \
         patch.object(
             tailor_resume, "retrieve_evidence",
             return_value=[{"section": "Experience", "text": "Cut p95 latency 40 percent."}],
         ) as mock_retrieve, \
         patch.object(tailor_resume, "council_complete") as mock_council:
        mock_council.return_value = {"content": "irrelevant for this test"}
        try:
            tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
        except Exception:
            pass

    mock_retrieve.assert_not_called()
    assert mock_council.called, "council_complete was never reached"
    prompt_seen = mock_council.call_args.kwargs["prompt"]
    assert "EVIDENCE POOL" not in prompt_seen
    assert "Cut p95 latency 40 percent." not in prompt_seen
