"""`jobs.score_tier` must not contradict `jobs.match_score`.

The tier is denormalised next to the score and nothing enforces agreement. The
dashboard's tier filter queries the STORED column (app.py's
`.in_("score_tier", tiers)`), so a stale label means filtering by S returns jobs
that are not S — user-visible "tier pollution".

Measured in production 2026-09-30, after the geo/work-auth backfill had run: 52
of 1,313 scored rows disagreed with their own score. 23 of them stored "S"
against a score in the A band; 15 stored "A" against a B score; 7 stored "B"
against a C score. Five drifted the other way.

The current pipeline cannot produce this — score_batch.py writes
`score_tier: score_to_tier(match_score)` from the POST-cap score, so new rows are
consistent by construction. Those 52 were historical, from before the cap existed
or from partial updates, and `backfill_geo_score_cap.py` could not reach them
because it only rewrites rows whose score the cap changes.

Two guards, because the one-off repair is not a guard:
  * these tests, over the repair script's decision logic
  * `score-tier-matches-score` in scripts/smoke_prod.py, which fails the deploy
    if drift ever returns
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _backfill():
    return _load("bf_tier", "scripts/backfill_score_tier_consistency.py")


# --- the repair script's decision logic -------------------------------------


def test_a_row_whose_tier_matches_its_score_is_left_alone():
    bf = _backfill()
    rows = [
        {"job_id": "1", "match_score": 95, "score_tier": "S"},
        {"job_id": "2", "match_score": 85, "score_tier": "A"},
        {"job_id": "3", "match_score": 75, "score_tier": "B"},
        {"job_id": "4", "match_score": 65, "score_tier": "C"},
        {"job_id": "5", "match_score": 55, "score_tier": "D"},
    ]
    assert bf.find_inconsistent(rows) == []


def test_the_real_production_shapes_are_all_caught():
    """The four largest correction classes measured in production."""
    bf = _backfill()
    rows = [
        {"job_id": "s-not-s", "match_score": 89, "score_tier": "S"},   # 23 of these
        {"job_id": "a-not-a", "match_score": 75, "score_tier": "A"},   # 15
        {"job_id": "b-not-b", "match_score": 68, "score_tier": "B"},   # 7
        {"job_id": "understated", "match_score": 75, "score_tier": "C"},  # 3, the other way
    ]
    found = bf.find_inconsistent(rows)
    assert {r["job_id"] for r in found} == {"s-not-s", "a-not-a", "b-not-b", "understated"}
    assert {r["job_id"]: r["_expected_tier"] for r in found} == {
        "s-not-s": "A", "a-not-a": "B", "b-not-b": "C", "understated": "B",
    }


def test_a_null_tier_with_a_score_is_a_correction_not_a_skip():
    """One production row held score 53 and no tier at all."""
    bf = _backfill()
    (found,) = bf.find_inconsistent([{"job_id": "x", "match_score": 53, "score_tier": None}])
    assert found["_expected_tier"] == "D"


def test_a_row_with_no_score_is_skipped_not_assigned_a_tier():
    """Absent data is not evidence of a low tier.

    Writing "D" here would invent a label the pipeline never derived, and the
    dashboard would then filter on it.
    """
    bf = _backfill()
    assert bf.find_inconsistent([{"job_id": "x", "match_score": None, "score_tier": None}]) == []
    assert bf.find_inconsistent([{"job_id": "y", "match_score": None, "score_tier": "A"}]) == []


def test_the_repair_is_idempotent_by_construction():
    """It derives the tier from a value it never writes, so a second pass is a no-op."""
    bf = _backfill()
    rows = [{"job_id": "1", "match_score": 89, "score_tier": "S"}]
    first = bf.find_inconsistent(rows)
    repaired = [{**rows[0], "score_tier": first[0]["_expected_tier"]}]
    assert bf.find_inconsistent(repaired) == []


# --- the recurrence guard ---------------------------------------------------


def test_the_smoke_check_is_registered():
    """A check that exists and is not in CHECKS never runs.

    Same dead-flag class as the `fairness_cap` policy key removed from
    guardrails/policy.py: it looks like a guard and guards nothing.
    """
    src = (ROOT / "scripts" / "smoke_prod.py").read_text()
    assert "def score_tier_matches_score()" in src, "the check is gone"
    checks_block = src.split("CHECKS = [", 1)[1].split("]", 1)[0]
    assert "score_tier_matches_score" in checks_block, (
        "score_tier_matches_score is defined but not in CHECKS, so smoke_prod "
        "never runs it and the deploy gate cannot see tier drift"
    )


def test_the_smoke_check_refuses_to_pass_vacuously():
    """Zero scored rows must fail, not pass. CLAUDE.md rule 2."""
    src = (ROOT / "scripts" / "smoke_prod.py").read_text()
    body = src.split("def score_tier_matches_score()", 1)[1].split("\nCHECKS", 1)[0]
    assert "would pass vacuously" in body or "assert rows" in body, (
        "the check must assert it found rows; an empty table would otherwise "
        "report success"
    )


# --- the three-copy hazard --------------------------------------------------


def test_every_tier_implementation_in_the_repo_agrees():
    """Three copies of these boundaries exist. They must not diverge.

    score_batch.score_to_tier (the pipeline's, and the authority), app._score_tier
    (the API's) and backfill_geo_score_cap._score_to_tier. They agree today; this
    pins it, because the whole defect above is a label disagreeing with the number
    it describes, and a fourth disagreement would be the same bug one level up.
    The repair script deliberately imports the pipeline's rather than adding a
    fourth.
    """
    from score_batch import score_to_tier as pipeline_tier

    import app as appmod
    geo = _load("bf_geo", "scripts/backfill_geo_score_cap.py")

    impls = {
        "score_batch.score_to_tier": pipeline_tier,
        "app._score_tier": appmod._score_tier,
        "backfill_geo_score_cap._score_to_tier": geo._score_to_tier,
    }
    disagreements = []
    for score in (0, 10, 55, 59, 59.9, 60, 69, 69.9, 70, 79, 79.9, 80, 89, 89.9, 90, 95, 100):
        values = {name: fn(score) for name, fn in impls.items()}
        if len(set(values.values())) > 1:
            disagreements.append((score, values))
    assert not disagreements, f"tier implementations disagree: {disagreements}"


def test_the_repair_script_uses_the_pipeline_implementation():
    """Not a fourth copy — the column is written by the pipeline, so its
    definition is the one that decides what 'correct' means."""
    src = (ROOT / "scripts" / "backfill_score_tier_consistency.py").read_text()
    assert "from score_batch import score_to_tier" in src
    assert "def score_to_tier" not in src, "restates the boundaries instead of importing them"
