"""Tests for LENGTH_GUIDANCE constant and its inclusion in the tailoring prompt."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tailorer import LENGTH_GUIDANCE


class TestLengthGuidanceConstant:
    def test_length_guidance_constant_exists(self):
        assert isinstance(LENGTH_GUIDANCE, str)
        assert len(LENGTH_GUIDANCE) > 50

    def test_length_guidance_has_word_target(self):
        assert "850-1000 words" in LENGTH_GUIDANCE

    def test_length_guidance_has_summary_budget(self):
        assert "Summary" in LENGTH_GUIDANCE
        assert "40-60" in LENGTH_GUIDANCE

    def test_length_guidance_has_skills_budget(self):
        assert "Skills" in LENGTH_GUIDANCE
        assert "50-80" in LENGTH_GUIDANCE

    def test_length_guidance_has_experience_budget(self):
        assert "Experience" in LENGTH_GUIDANCE
        assert "80-120" in LENGTH_GUIDANCE

    def test_length_guidance_has_project_budget(self):
        assert "Project" in LENGTH_GUIDANCE
        assert "60-90" in LENGTH_GUIDANCE

    def test_length_guidance_has_education_budget(self):
        assert "Education" in LENGTH_GUIDANCE
        assert "30-50" in LENGTH_GUIDANCE

    def test_length_guidance_has_certifications_budget(self):
        assert "Certifications" in LENGTH_GUIDANCE
        assert "20-30" in LENGTH_GUIDANCE


class TestLengthGuidanceInPrompt:
    """Verify that tailor_resume() includes LENGTH_GUIDANCE in the system prompt."""

    @patch("tailorer.extract_keywords", return_value=[])
    @patch("tailorer.log_quality")
    @patch("tailorer._sanitize_latex", side_effect=lambda x: x)
    def test_length_guidance_in_prompt(self, mock_sanitize, mock_log, mock_kw, tmp_path):
        from scrapers.base import Job
        from tailorer import tailor_resume

        job = Job(
            title="SRE",
            company="Acme",
            location="Dublin",
            apply_url="https://example.com",
            source="test",
            description="A job description for testing purposes.",
        )

        # Minimal LaTeX that passes validation
        base_tex = (
            r"\documentclass{article}"
            "\n\\newcommand{\\jobentry}[1]{#1}"
            "\n\\begin{document}"
            "\n\\section*{Experience}"
            "\n\\section*{Technical Skills}"
            "\n\\section*{Education}"
            "\n\\end{document}"
        )

        mock_ai = MagicMock()
        mock_ai.providers = []
        mock_ai.complete_with_info.return_value = {
            "response": base_tex,
            "provider": "test",
            "model": "test-model",
        }

        tailor_resume(job, base_tex, mock_ai, tmp_path)

        # Grab the system prompt that was passed to the AI
        call_args = mock_ai.complete_with_info.call_args
        system_arg = call_args.kwargs.get("system") or call_args[1].get("system")
        assert "850-1000 words" in system_arg
        assert "SECTION WORD BUDGETS" in system_arg
        assert "40-60" in system_arg
