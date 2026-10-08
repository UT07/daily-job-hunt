"""The legacy council never draws its critic from a family that generated.

`_council_complete_legacy` (reached via council_complete under
COUNCIL_ENGINE=legacy, and by any Lambda whose COUNCIL_ENGINE is unset) asked
for a critic outside the generator families and, when none existed, fell back
to `_select_diverse_providers(all_providers, n=1)` with NO exclusion. The
LangGraph engine had the same fallback in `select_critics`; 5e9794d removed it
there ("select_critics must never relax", the rule 17b3b48 states). This pins
the legacy engine to the same contract: no unused family means no critic call
and `critique_outcome="no_critic_family"`.

A second leak, legacy only: the exclusion was built from the ASSIGNED
generators, not from the candidates actually produced. When a generator fails
and its fallback succeeds from a third family, that third family wrote a
candidate yet was still eligible to judge it.

The critic is always picked by the REAL `_select_diverse_providers` over a
stubbed pool (two tests pin only the generator assignment, which otherwise
shuffles). The network call (`_call_provider`) is replaced by a double that
records every provider it is asked to call, so "no critic call" is observed,
not assumed. test_an_unused_family_still_critiques passes on the old code too:
it proves the double can produce an adjudication at all (CLAUDE.md #6).
"""
import sys
from unittest.mock import patch

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")

import ai_helper  # noqa: E402

GEMINI = {"name": "gemini/flash", "model": "gemini-2.5-flash"}
GROQ = {"name": "groq/gpt-oss-120b", "model": "openai/gpt-oss-120b"}
MISTRAL = {"name": "openrouter/mistral", "model": "mistralai/mistral-small-3.2"}


def setup_function():
    ai_helper._reset_cooldowns()


def teardown_function():
    ai_helper._reset_cooldowns()


def _fam(p):
    return ai_helper._model_family(p["model"])


class _Calls:
    """Answers generators; fails the named providers; records every call."""

    def __init__(self, failing=()):
        self.failing = set(failing)
        self.called: list[str] = []

    def __call__(self, provider, prompt, system="", temperature=0.3, max_tokens=4096):
        self.called.append(provider["name"])
        if provider["name"] in self.failing:
            return None
        if system == ai_helper.CRITIQUE_SYSTEM:
            return {"content": "[80, 90]",
                    "provider": provider["name"], "model": provider["model"]}
        return {"content": f"body from {provider['name']} " * 20,
                "provider": provider["name"], "model": provider["model"],
                "finish_reason": "stop"}


def test_the_double_is_three_families():
    assert len({_fam(GEMINI), _fam(GROQ), _fam(MISTRAL)}) == 3


def test_every_family_generated_means_no_critic_call():
    calls = _Calls()
    with patch.object(ai_helper, "_build_provider_list", return_value=[GEMINI, GROQ]), \
         patch.object(ai_helper, "_call_provider", side_effect=calls):
        out = ai_helper._council_complete_legacy("p", "s", "desc", n_generators=2)
    assert out["critique_outcome"] == "no_critic_family", out["critique_outcome"]
    assert sorted(calls.called) == sorted([GEMINI["name"], GROQ["name"]]), (
        f"a critic was called from a generating family: {calls.called}")


def test_a_fallback_generators_family_cannot_judge_its_own_candidate():
    # Generators are assigned gemini + groq. Whichever is groq fails, and the
    # generator fallback produces a candidate from mistral. The only family left
    # outside the ASSIGNED generators is mistral -- which wrote a candidate.
    calls = _Calls(failing={GROQ["name"]})
    with patch.object(ai_helper, "_build_provider_list", return_value=[GEMINI, GROQ, MISTRAL]), \
         patch.object(ai_helper, "_select_diverse_providers",
                      side_effect=_assign_gemini_and_groq_then_real), \
         patch.object(ai_helper, "_call_provider", side_effect=calls):
        out = ai_helper._council_complete_legacy("p", "s", "desc", n_generators=2)
    assert {c for c in calls.called} >= {MISTRAL["name"]}, calls.called
    assert out["critique_outcome"] == "no_critic_family", out["critique_outcome"]
    assert calls.called.count(MISTRAL["name"]) == 1, (
        f"mistral generated and was then called again as critic: {calls.called}")


_REAL_SELECT = ai_helper._select_diverse_providers


def _assign_gemini_and_groq_then_real(providers, n, exclude_families=None, **kw):
    # Pin the generator assignment (the real one shuffles); every later pick
    # -- the critic -- goes through the real selector.
    if not exclude_families and n == 2:
        return [GEMINI, GROQ]
    return _REAL_SELECT(providers, n, exclude_families=exclude_families, **kw)


def test_an_unused_family_still_critiques():
    calls = _Calls()
    with patch.object(ai_helper, "_build_provider_list", return_value=[GEMINI, GROQ, MISTRAL]), \
         patch.object(ai_helper, "_select_diverse_providers",
                      side_effect=_assign_gemini_and_groq_then_real), \
         patch.object(ai_helper, "_call_provider", side_effect=calls):
        out = ai_helper._council_complete_legacy("p", "s", "desc", n_generators=2)
    assert out["critique_outcome"] == "adjudicated", out["critique_outcome"]
    assert calls.called[-1] == MISTRAL["name"]
