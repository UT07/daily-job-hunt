"""The tailor handler must RAISE on failure, never return an error dict.

Background (2026-08-31 audit). The 08:00 daily run died with:

    An error occurred while executing the state 'CompileResume'.
    The JSONPath '$.tailor_result.tex_s3_key' could not be found in the input:
      { "tailor_result": { "error": "Council: all generators failed" } }

`tailor_resume.handler` caught its own failure and *returned* `{"error": ...}`.
To Step Functions a returned dict is a SUCCESSFUL invocation, so the
`Catch` on the TailorResume state never fired. The failure only surfaced one
state later, as an unhandled JSONPath error in CompileResume — which is fatal
to the entire execution rather than to the single job.

That defeats the whole point of the per-job Map: one job whose council is
exhausted takes down the day's run for every other job.

The state machine already routes `Catch -> SaveJobAfterError`, so raising is
what the design expects. These tests pin that contract.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402


def _db_with(job_rows, resume_rows=None):
    """Minimal Supabase stub: db.table(...).select(...)....execute()."""
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
        else:
            result.data = []
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


EVENT = {"job_hash": "abc123", "user_id": "u-1"}

BASE_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section*{Summary}\nBase summary.\n"
    "\\end{document}\n"
)


def test_missing_job_raises_not_returns():
    with patch.object(tailor_resume, "get_supabase", return_value=_db_with([])), \
         patch.object(tailor_resume, "boto3", MagicMock()):
        with pytest.raises(Exception) as exc:
            tailor_resume.handler(EVENT, None)
    assert "abc123" in str(exc.value)


def test_missing_resume_raises_not_returns():
    db = _db_with([{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                    "description": "d"}], resume_rows=[])
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()):
        with pytest.raises(Exception) as exc:
            tailor_resume.handler(EVENT, None)
    assert "resume" in str(exc.value).lower()


def test_council_failure_raises_not_returns():
    """The exact shape that killed the 2026-08-31 run."""
    db = _db_with(
        [{"job_hash": "abc123", "title": "SRE", "company": "Acme", "description": "d"}],
        resume_rows=[{"tex_content": BASE_TEX}],
    )
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()), \
         patch.object(tailor_resume, "council_complete",
                      side_effect=RuntimeError("Council: all generators failed")):
        with pytest.raises(Exception) as exc:
            tailor_resume.handler(EVENT, None)
    assert "generators failed" in str(exc.value)


def test_handler_never_returns_a_dict_carrying_only_an_error():
    """Belt and braces: no code path may return {'error': ...} without tex_s3_key.

    Step Functions reads `$.tailor_result.tex_s3_key`. Any successful return
    that lacks it is a landmine for the next state.
    """
    import inspect
    src = inspect.getsource(tailor_resume.handler)
    offenders = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith("return {") and '"error"' in line and "tex_s3_key" not in line
    ]
    assert not offenders, (
        "handler still returns bare error dicts, which Step Functions treats as "
        f"success: {offenders}"
    )
