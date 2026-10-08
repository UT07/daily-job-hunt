"""A quality comparison between runs served by different models.

On 2026-10-07 the gate failed PR #199 with "tier_accuracy fell 10.0%
(85.0% -> 75.0%)". The PR's diff touches `GuardResult.to_dict`, a log line in
`quality_gate`, two additive return keys and a row write. It does not touch
scoring, which is what tier_accuracy measures. What HAD changed between the two
runs was who answered:

    baseline  families_served: ["gemini"]
    current   families_served: ["gemini", "groq"]

`pool_shrank` exists for exactly this class of error and could not see it: it
asks `set(was) - set(now)`, so it catches a family the run LOST and not one it
GAINED. The baseline's own `_families_served_caveat` states the principle in
full -- "the two would be measuring providers against each other, not the
change under review" -- and that is true in both directions, because
tier_accuracy measures agreement with labels an earlier mix produced.

Reported, deliberately NOT a verdict. Making any pool difference INCONCLUSIVE
is the easier change and the wrong one: free-tier pools vary run to run, so a
gate that abstains whenever they differ is a gate that rarely judges -- the
same lie as one that cannot fail, arrived at from the other side. These tests
pin both halves: the fact is surfaced, and the failure still fails.
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from check_eval_gate import evaluate_gate, pool_differs  # noqa: E402

BASE = {"tier_accuracy": 0.85, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
        "families_served": ["gemini"], "_frozen_at": "2026-09-28"}


class TestPoolDiffers:
    def test_a_gained_family_is_reported(self):
        """The case pool_shrank is structurally unable to see."""
        got = pool_differs({"families_served": ["gemini", "groq"]}, BASE)
        assert got and "gained ['groq']" in got

    def test_a_lost_family_is_reported_too(self):
        got = pool_differs({"families_served": ["groq"]}, BASE)
        assert got and "lost ['gemini']" in got and "gained ['groq']" in got

    def test_an_identical_set_is_not_a_difference(self):
        assert pool_differs({"families_served": ["gemini"]}, BASE) is None

    def test_order_is_not_a_difference(self):
        assert pool_differs({"families_served": ["groq", "gemini"]},
                            {"families_served": ["gemini", "groq"]}) is None

    def test_a_baseline_without_the_field_skips_rather_than_blocking(self):
        """Same contract as pool_shrank: a baseline frozen before the field
        existed must not fail every PR."""
        assert pool_differs({"families_served": ["gemini"]}, {}) is None
        assert pool_differs({}, BASE) is None


class TestTheGateMessage:
    def test_the_mix_difference_appears_in_the_failure(self):
        cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
               "families_served": ["gemini", "groq"]}
        ok, reasons = evaluate_gate(cur, BASE)
        blob = " ".join(reasons)
        assert ok is False
        assert "gained ['groq']" in blob, (
            "the reader cannot see that the two runs were served by different "
            "models without opening the JSON")

    def test_naming_the_mix_does_not_turn_the_failure_into_a_pass(self):
        """The whole point. A gate that excuses itself is worse than no gate,
        and this is the shape that would do it."""
        cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
               "families_served": ["gemini", "groq"]}
        ok, _ = evaluate_gate(cur, BASE)
        assert ok is False

    def test_a_matching_mix_adds_nothing_to_the_message(self):
        """No noise when the comparison IS comparable -- otherwise the note
        appears on every failure and stops meaning anything."""
        cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
               "families_served": ["gemini"]}
        _, reasons = evaluate_gate(cur, BASE)
        assert "gained" not in " ".join(reasons)

    def test_a_lost_family_still_reads_as_inconclusive(self):
        """pool_shrank keeps its stronger power: a family the run LOST means a
        quota was exhausted and the run could not be made, which is a different
        claim from a different mix."""
        cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
               "families_served": ["groq"]}
        ok, reasons = evaluate_gate(cur, BASE)
        assert ok is False
        assert "INCONCLUSIVE" in " ".join(reasons)
