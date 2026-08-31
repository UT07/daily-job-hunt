"""Guards for the AI council's model configuration.

Background (2026-08-31 audit): every one of the 7 configured providers was
returning 404/410/429 because the free model IDs had been retired or moved
behind paywalls by their vendors. The council was 0/7 alive, which silently
broke scoring (1,099 of 1,205 jobs stuck at score_status='pending') and made
tailoring fail with "Council: all generators failed" — which in turn killed
the whole daily Step Functions run.

Nothing in CI caught it because no test asserted anything about the model
IDs, and the runtime treats a dead provider as an ordinary failover.

These tests can't call the vendor APIs (no keys in CI, and we don't want CI
depending on a third party's uptime), so they pin the things that CAN be
checked offline: that known-retired IDs never come back, that reasoning
models get a workable token budget, and that family dedup actually dedups.
"""
import pytest

from lambdas.pipeline import ai_helper


# Model IDs confirmed dead on 2026-08-31 by live probe against the production
# SSM keys. Each returned a hard failure, not a transient one:
#   llama-3.3-70b-versatile            groq        404 does not exist
#   meta/llama-3.3-70b-instruct        nvidia      410 Gone
#   qwen/qwen3.6-plus:free             openrouter  404 free tier deprecated
#   meta-llama/llama-3.3-70b-instruct:free         404 unavailable for free
#   z-ai/glm-4.5-air:free                          404 unavailable for free
#   google/gemma-3-27b-it:free                     404 unavailable for free
RETIRED_MODEL_IDS = {
    "llama-3.3-70b-versatile",
    "meta/llama-3.3-70b-instruct",
    "qwen/qwen3.6-plus:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "z-ai/glm-4.5-air:free",
    "google/gemma-3-27b-it:free",
}


def test_provider_list_contains_no_retired_models():
    """The council must not ship a model ID we've confirmed is dead."""
    configured = {p["model"] for p in ai_helper._build_provider_list()}
    still_dead = configured & RETIRED_MODEL_IDS
    assert not still_dead, (
        f"Council still configures retired model(s): {sorted(still_dead)}. "
        "These return 404/410 and make the council fail closed."
    )


def test_provider_list_is_not_empty():
    providers = ai_helper._build_provider_list()
    assert len(providers) >= 3, (
        f"Council has only {len(providers)} providers; needs at least 3 so "
        "_select_diverse_providers can pick distinct families."
    )


def test_council_has_at_least_three_distinct_families():
    """Diversity is the point of the council — verify it's achievable."""
    families = {ai_helper._model_family(p["model"]) for p in ai_helper._build_provider_list()}
    assert len(families) >= 3, (
        f"Only {len(families)} distinct model families configured ({sorted(families)}). "
        "The council degenerates to the same model voting with itself."
    )


@pytest.mark.parametrize(
    "a,b",
    [
        ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
        ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b"),
    ],
)
def test_same_family_models_collapse(a, b):
    """Size variants of one model are the SAME family.

    If they don't collapse, _select_diverse_providers can pick both and the
    council loses the independence that makes its vote meaningful.
    """
    assert ai_helper._model_family(a) == ai_helper._model_family(b), (
        f"{a!r} and {b!r} resolve to different families "
        f"({ai_helper._model_family(a)!r} vs {ai_helper._model_family(b)!r})"
    )


def test_critic_token_budget_is_reasoning_safe():
    """Reasoning models spend the budget before emitting any content.

    gpt-oss-120b burned 298 of 300 tokens on `reasoning` and returned
    content='' with finish_reason='length'. _call_provider treats empty
    content as a failure, so a reasoning critic silently degrades the
    council to "return the first candidate". The budget must leave room
    for reasoning AND the answer.
    """
    assert ai_helper.CRITIC_MAX_TOKENS >= 512, (
        f"CRITIC_MAX_TOKENS={ai_helper.CRITIC_MAX_TOKENS} is too small for a "
        "reasoning model; it will return empty content and fail the critic."
    )
