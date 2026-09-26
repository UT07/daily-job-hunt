"""Golden-set shape and stratification tests (Task 23).

See evals/golden/manifest.json for how the 25 fixtures were built and why
the tier spread is 6/6/6/6/1 (S/A/B/C/D) rather than a flat 5-per-tier: the
production `jobs` table only ever inserts a row when match_score >= 60, so
D-tier (score < 60) is structurally almost absent from live data.
"""
from evals import load_golden

VALID_TIERS = {"S", "A", "B", "C", "D"}


def test_golden_set_has_twenty_five_fixtures():
    assert len(load_golden()) == 25


def test_every_fixture_has_the_required_shape():
    for case in load_golden():
        assert {"id", "task", "description", "expected"} <= set(case)
        assert case["task"] in {"score", "tailor"}


def test_scoring_fixtures_declare_an_expected_tier():
    for case in load_golden():
        if case["task"] == "score":
            assert case["expected"]["tier"] in VALID_TIERS


def test_tailoring_fixtures_declare_required_keywords():
    for case in load_golden():
        if case["task"] == "tailor":
            assert case["expected"]["must_contain"]


def test_fixture_ids_are_unique():
    ids = [c["id"] for c in load_golden()]
    assert len(ids) == len(set(ids))


def test_tiers_are_spread_not_all_one_bucket():
    # A golden set that is all S-tier cannot detect a scoring regression.
    tiers = {c["expected"]["tier"] for c in load_golden() if c["task"] == "score"}
    assert len(tiers) >= 3


def test_tailoring_fixtures_declare_genuinely_present_keywords():
    """A must_contain keyword the JD never mentions makes the fixture
    untestable (Task 23's own warning) — assert build time actually
    honoured that, not just that the list is non-empty."""
    for case in load_golden():
        if case["task"] == "tailor":
            desc_lower = case["description"].lower()
            for keyword in case["expected"]["must_contain"]:
                assert keyword.lower() in desc_lower, (
                    f"{case['id']}: must_contain keyword {keyword!r} does not "
                    f"appear in its own JD"
                )
