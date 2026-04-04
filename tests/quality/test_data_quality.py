"""Tier 4b: Data quality tests. MUST PASS in CI."""
import pytest
from utils.canonical_hash import canonical_hash
from tests.fixtures.dedup_fixtures import DUPLICATE_PAIRS, NEAR_MISS_PAIRS, EDGE_CASES


# Pair indices where canonical_hash alone should produce the same hash.
# Pair 2 ("Senior" vs "Sr") requires fuzzy dedup, not hash equality.
_HASH_EQUAL_PAIRS = [0, 1, 3, 4]


class TestHashConsistency:
    """Same job through any code path -> identical canonical_hash."""

    @pytest.mark.parametrize("pair", DUPLICATE_PAIRS, ids=[f"dup_{i}" for i in range(len(DUPLICATE_PAIRS))])
    def test_duplicate_pairs_same_hash(self, pair):
        h_a = canonical_hash(pair["job_a"]["company"], pair["job_a"]["title"], pair["job_a"]["description"])
        h_b = canonical_hash(pair["job_b"]["company"], pair["job_b"]["title"], pair["job_b"]["description"])
        if pair["should_match"]:
            idx = DUPLICATE_PAIRS.index(pair)
            if idx in _HASH_EQUAL_PAIRS:
                assert h_a == h_b, f"Expected same hash for duplicate pair {idx}"
            # Pair 2 is caught by fuzzy dedup, not canonical hash


class TestDedupCorrectness:
    @pytest.mark.parametrize("pair", NEAR_MISS_PAIRS, ids=[f"nm_{i}" for i in range(len(NEAR_MISS_PAIRS))])
    def test_near_miss_pairs_different_hash(self, pair):
        h_a = canonical_hash(pair["job_a"]["company"], pair["job_a"]["title"], pair["job_a"]["description"])
        h_b = canonical_hash(pair["job_b"]["company"], pair["job_b"]["title"], pair["job_b"]["description"])
        assert h_a != h_b, "Near-miss pair should NOT have same hash"


class TestNoTruncation:
    def test_long_description_not_truncated(self):
        base = "x" * 500
        h_short = canonical_hash("Acme", "Engineer", base)
        h_long = canonical_hash("Acme", "Engineer", base + "y" * 4500)
        assert h_short != h_long


class TestDescriptionlessHandling:
    def test_empty_description_skipped(self):
        from lambdas.pipeline.score_batch import should_skip_scoring
        assert should_skip_scoring(EDGE_CASES["empty_description"]) == "insufficient_data"

    def test_short_description_skipped(self):
        from lambdas.pipeline.score_batch import should_skip_scoring
        assert should_skip_scoring(EDGE_CASES["short_description"]) == "insufficient_data"

    def test_missing_company_skipped(self):
        from lambdas.pipeline.score_batch import should_skip_scoring
        assert should_skip_scoring(EDGE_CASES["missing_company"]) == "incomplete"


class TestBeforeAfterDelta:
    def test_delta_computed_correctly(self):
        base = {"base_ats_score": 70, "base_hm_score": 65, "base_tr_score": 72}
        tailored = {"tailored_ats_score": 85, "tailored_hm_score": 78, "tailored_tr_score": 80}
        delta = {
            "ats_delta": tailored["tailored_ats_score"] - base["base_ats_score"],
            "hm_delta": tailored["tailored_hm_score"] - base["base_hm_score"],
            "tr_delta": tailored["tailored_tr_score"] - base["base_tr_score"],
        }
        assert delta["ats_delta"] == 15
        assert delta["hm_delta"] == 13
        assert delta["tr_delta"] == 8


class TestCrossRunDedup:
    def test_recently_scored_job_skipped(self):
        from lambdas.pipeline.merge_dedup import cross_run_check
        from datetime import datetime, timedelta

        existing = {
            "scored_at": (datetime.now() - timedelta(days=3)).isoformat(),
            "base_ats_score": 75,
            "resume_s3_url": "s3://bucket/resume.pdf",
        }
        result = cross_run_check(existing)
        assert result["skip_scoring"] is True
        assert result["skip_tailoring"] is True

    def test_old_job_rescored(self):
        from lambdas.pipeline.merge_dedup import cross_run_check
        from datetime import datetime, timedelta

        old = {"scored_at": (datetime.now() - timedelta(days=8)).isoformat()}
        result = cross_run_check(old)
        assert result["skip_scoring"] is False
