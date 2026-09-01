"""Keep the scoring prompt inside Groq's free-tier tokens-per-minute budget.

Measured on 2026-09-01 against the production Groq key:

    x-ratelimit-limit-tokens: 8000

Groq bills `prompt_tokens + max_tokens` against that per-minute budget, so a
generous max_tokens burns quota even when the model doesn't use it. The old
call sent the resume as raw LaTeX with the default max_tokens=4096:

    prompt_tokens 3792 + max_tokens 4096 = 7888   -> right on the 8000 limit,
                                                     413 "Request too large"

Two measured facts drive the numbers pinned here:

* max_tokens=1024 truncates. finish_reason='length', invalid JSON — this was
  the "[score_batch] JSON parse error" in the logs. 1536 completed with
  finish_reason='stop' and 1206 completion tokens, 702 of them reasoning.
* tex_to_plaintext() takes the resume from 12,500 to 9,705 chars (22% off)
  with no loss of scoring signal — LaTeX markup carries none.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

import score_batch  # noqa: E402


LATEX_RESUME = (
    "\\documentclass{article}\n\\usepackage{geometry}\n"
    "\\newcommand{\\role}[3]{#1 #2 #3}\n"
    "\\begin{document}\n"
    "\\section*{Technical Skills}\n\\textbf{Python}, \\textbf{AWS}, Kubernetes\n"
    "\\section*{Experience}\n\\role{SRE}{Acme}{2022-2026} Cut deploy time 40\\%.\n"
    "\\end{document}\n"
)


def _capture_prompt(job, resume, **kw):
    """Run score_single_job with the AI stubbed; return the prompt it built."""
    seen = {}

    def fake_ai(prompt, system="", **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {"content": '{"match_score": 80, "ats_score": 80, '
                           '"hiring_manager_score": 80, "tech_recruiter_score": 80}',
                "provider": "groq", "model": "openai/gpt-oss-120b"}

    with patch.object(score_batch, "ai_complete_cached", side_effect=fake_ai):
        score_batch.score_single_job(job, resume, **kw)
    return seen


BASE_JOB = {"title": "SRE", "company": "Acme", "location": "Dublin", "remote": "hybrid",
            "description": "We need Python and AWS."}


def test_resume_latex_is_stripped_before_prompting():
    """The model must never see \\documentclass, \\usepackage or custom macros.

    LaTeX markup is pure token overhead for a scoring task.
    """
    seen = _capture_prompt(BASE_JOB, LATEX_RESUME)
    prompt = seen["prompt"]
    for marker in ("\\documentclass", "\\usepackage", "\\newcommand", "\\begin{document}"):
        assert marker not in prompt, f"raw LaTeX {marker!r} leaked into the scoring prompt"
    # the actual content must survive
    assert "Python" in prompt and "Kubernetes" in prompt


def test_long_description_is_capped():
    """Descriptions have a long boilerplate tail; the cap keeps prompts bounded."""
    job = dict(BASE_JOB, description="x" * 20000)
    seen = _capture_prompt(job, LATEX_RESUME)
    assert len(seen["prompt"]) < 20000, (
        "description was not capped — a 19k-char JD blows the TPM budget on its own"
    )


def test_short_description_is_not_truncated():
    job = dict(BASE_JOB, description="Python and Terraform required.")
    seen = _capture_prompt(job, LATEX_RESUME)
    assert "Python and Terraform required." in seen["prompt"]


def test_description_cap_is_sane():
    assert 2000 <= score_batch.MAX_DESCRIPTION_CHARS <= 8000, (
        f"MAX_DESCRIPTION_CHARS={score_batch.MAX_DESCRIPTION_CHARS} is outside the "
        "range that keeps prompts under the 8000 TPM budget while preserving signal"
    )


def test_scoring_max_tokens_avoids_truncation_and_fits_budget():
    """Measured floor and ceiling.

    Below 1536 the response truncates mid-JSON and the whole call is wasted.
    Above ~2500 the request trips the 8000 TPM limit at a ~3800-token prompt.
    """
    assert score_batch.SCORE_MAX_TOKENS >= 1536, (
        f"SCORE_MAX_TOKENS={score_batch.SCORE_MAX_TOKENS} truncates the scoring JSON "
        "(measured: 1024 -> finish_reason='length', invalid JSON)"
    )
    assert score_batch.SCORE_MAX_TOKENS <= 2560, (
        f"SCORE_MAX_TOKENS={score_batch.SCORE_MAX_TOKENS} plus a ~3800-token prompt "
        "exceeds Groq's 8000 tokens-per-minute free-tier limit"
    )


def test_scoring_passes_explicit_max_tokens():
    """It must not inherit ai_complete_cached's 4096 default."""
    seen = _capture_prompt(BASE_JOB, LATEX_RESUME)
    assert seen["kwargs"].get("max_tokens") == score_batch.SCORE_MAX_TOKENS, (
        "score_single_job did not pass an explicit max_tokens; it falls back to the "
        "4096 default, which trips the TPM limit"
    )
