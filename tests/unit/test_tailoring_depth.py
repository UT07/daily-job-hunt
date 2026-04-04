"""Tests for tailorer.get_tailoring_depth and should_tailor."""

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tailorer import get_tailoring_depth, should_tailor


class TestGetTailoringDepth:
    def test_light_touch_high_score(self):
        depth, rounds = get_tailoring_depth(90)
        assert "LIGHT" in depth
        assert rounds == 1

    def test_light_touch_boundary(self):
        depth, rounds = get_tailoring_depth(85)
        assert "LIGHT" in depth
        assert rounds == 1

    def test_moderate_rewrite(self):
        depth, rounds = get_tailoring_depth(75)
        assert "MODERATE" in depth
        assert rounds == 2

    def test_moderate_boundary_low(self):
        depth, rounds = get_tailoring_depth(70)
        assert "MODERATE" in depth
        assert rounds == 2

    def test_heavy_rewrite(self):
        depth, rounds = get_tailoring_depth(50)
        assert "HEAVY" in depth
        assert rounds == 3

    def test_heavy_rewrite_zero(self):
        depth, rounds = get_tailoring_depth(0)
        assert "HEAVY" in depth
        assert rounds == 3

    def test_heavy_boundary(self):
        depth, rounds = get_tailoring_depth(69.9)
        assert "HEAVY" in depth
        assert rounds == 3

    def test_none_score_defaults_to_moderate(self):
        depth, rounds = get_tailoring_depth(None)
        assert rounds == 2
        assert depth == "moderate"

    def test_negative_score_defaults_to_moderate(self):
        depth, rounds = get_tailoring_depth(-5)
        assert rounds == 2
        assert depth == "moderate"


class TestShouldTailor:
    def test_skips_insufficient_data(self):
        assert should_tailor({"score_status": "insufficient_data"}) is False

    def test_skips_incomplete(self):
        assert should_tailor({"score_status": "incomplete"}) is False

    def test_allows_scored(self):
        assert should_tailor({"score_status": "scored"}) is True

    def test_allows_empty_dict(self):
        assert should_tailor({}) is True

    def test_allows_no_score_status(self):
        assert should_tailor({"title": "SRE", "company": "Acme"}) is True


class TestCriticRubricPrompt:
    def test_critic_rubric_prompt_exists(self):
        from tailorer import CRITIC_RUBRIC_PROMPT
        assert "keyword coverage" in CRITIC_RUBRIC_PROMPT.lower()
        assert "section completeness" in CRITIC_RUBRIC_PROMPT.lower()
        assert "fabrication" in CRITIC_RUBRIC_PROMPT.lower()

    def test_critic_rubric_expects_json(self):
        from tailorer import CRITIC_RUBRIC_PROMPT
        assert "winner" in CRITIC_RUBRIC_PROMPT
        assert "scores_a" in CRITIC_RUBRIC_PROMPT
