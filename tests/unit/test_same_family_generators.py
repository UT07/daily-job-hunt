"""One live family must still produce two candidates to compare.

Measured over the 212-résumé batch of 2026-10-07:

    single_candidate   415
    adjudicated        129

A 429 cools the whole ACCOUNT, not one model. OpenRouter dies on its daily
quota and Groq took 41 cooldowns x 90s, so on a fast batch the live pool
collapses to Gemini alone. Gemini has FIVE models in the pool and
`_model_family` folds them into one family — so a perfectly healthy Gemini
contributed exactly ONE generator, and one candidate means nothing to
adjudicate, no critic verdict, and a planning-laced body surviving to the hard
gates that then ship the corpus instead.

Two models from one family is weaker diversity than two families. It is much
stronger than no comparison at all, which is what the strict rule delivers
whenever the pool is degraded.

The critic slot deliberately does NOT get this: a critic drawn from the family
that generated is not an independent reviewer, and that is the whole point of
the slot.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lambdas/pipeline"))

import ai_helper  # noqa: E402

GEMINI = [{"name": f"gemini/g-{i}", "model": f"gemini-{i}-flash"} for i in range(5)]
GROQ = [{"name": "groq/gpt-oss-120b", "model": "openai/gpt-oss-120b"}]


@pytest.fixture(autouse=True)
def _no_cooldowns():
    ai_helper._reset_cooldowns()
    yield
    ai_helper._reset_cooldowns()


def _families(picked):
    return {ai_helper._model_family(p["model"]) for p in picked}


class TestTheStrictRuleIsUnchangedWhenThePoolIsHealthy:
    def test_two_families_available_gives_two_families(self):
        picked = ai_helper._select_diverse_providers(
            GEMINI + GROQ, n=2, fill_same_family=True)
        assert len(picked) == 2
        assert len(_families(picked)) == 2, (
            "a healthy pool must still prefer DISTINCT families; the fill is "
            "only for slots distinct families cannot cover")

    def test_the_default_is_still_strict(self):
        """Off unless asked for. `select_critics` relies on this."""
        picked = ai_helper._select_diverse_providers(GEMINI, n=2)
        assert len(picked) == 1, "the one-per-family rule was relaxed by default"


class TestOneFamilyStillProducesSomethingToCompare:
    def test_two_generators_come_back_from_a_single_family(self):
        picked = ai_helper._select_diverse_providers(GEMINI, n=2, fill_same_family=True)
        assert len(picked) == 2, (
            "one live family still yields one candidate — nothing to adjudicate, "
            "which is the 415 single_candidate this exists to fix")
        assert len({p["name"] for p in picked}) == 2, "the same provider twice"

    def test_it_never_returns_the_same_provider_twice(self):
        for _ in range(20):
            picked = ai_helper._select_diverse_providers(
                GEMINI, n=3, fill_same_family=True)
            names = [p["name"] for p in picked]
            assert len(names) == len(set(names)), f"duplicate provider: {names}"

    def test_it_cannot_exceed_the_pool(self):
        picked = ai_helper._select_diverse_providers(
            GEMINI[:2], n=5, fill_same_family=True)
        assert len(picked) == 2

    def test_excluded_families_stay_excluded(self):
        """The fill lifts the one-per-family rule, NOT the exclusion. A critic
        asking to avoid the generating family must never be handed it."""
        picked = ai_helper._select_diverse_providers(
            GEMINI, n=2, exclude_families={"gemini"}, fill_same_family=True)
        assert picked == [], (
            "the fill bypassed exclude_families — a critic from the family that "
            "generated is not an independent reviewer")


def test_only_the_generator_slot_asks_for_it():
    """Structural. `select_critics` passing this would quietly turn the critic
    into a same-family reviewer, which is the one thing the slot exists to
    prevent — and nothing downstream would look any different."""
    import ast

    tree = ast.parse((ROOT / "lambdas/pipeline/agents/providers.py").read_text())

    def _kwargs_of_calls_in(fn_name):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        return {kw.arg for c in ast.walk(fn) if isinstance(c, ast.Call)
                for kw in c.keywords if kw.arg}

    # Walked as an AST, not matched as text. The first version of this test
    # asserted `"fill_same_family=True" in <source slice>` — and the docstring
    # of select_generators contains that exact string, so it passed with the
    # keyword stripped from the call. Mutation testing caught it: "the
    # generator slot stops asking for it" SURVIVED. Same tautology as a test
    # that reads its expectations from the thing under test.
    assert "fill_same_family" in _kwargs_of_calls_in("select_generators"), (
        "select_generators no longer passes fill_same_family, so a degraded "
        "pool is back to one candidate and nothing to adjudicate")
    assert "fill_same_family" not in _kwargs_of_calls_in("select_critics"), (
        "select_critics passes fill_same_family; a critic drawn from the "
        "generating family is not an independent reviewer")
