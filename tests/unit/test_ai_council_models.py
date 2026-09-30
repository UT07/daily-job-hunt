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
        # Two HOSTS for one model, not two models. Cerebras serves Qwen 3.8 27B
        # as "qwen-3.8-27b" and Groq as "qwen/qwen3.8-27b" — the hyphen is the
        # only difference, and the prefix table matches "qwen3", so the
        # Cerebras spelling used to fall through to its own family. Two
        # generators on the same weights is one generator.
        ("qwen-3.8-27b", "qwen/qwen3.8-27b"),
        ("gpt-oss-120b", "openai/gpt-oss-120b"),
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


# ---------------------------------------------------------------------------
# The same guard, for the OTHER client.
#
# Everything above checks ai_helper._build_provider_list() — the pipeline
# council. Nothing checked ai_client.py, the client main.py and the local
# dry-run path use, and that is how NvidiaNIMProvider kept defaulting to
# meta/llama-3.3-70b-instruct after it went end-of-life. The model id was
# already in RETIRED_MODEL_IDS above, documented as "nvidia 410 Gone", and the
# default sat there for a month because no test looked at this file.
# ---------------------------------------------------------------------------

def _ai_client_default_models():
    """Default `model=` on every AIProvider subclass in ai_client.py."""
    import inspect

    import ai_client

    defaults = {}
    for name, obj in vars(ai_client).items():
        if not inspect.isclass(obj) or not issubclass(obj, ai_client.AIProvider):
            continue
        if obj is ai_client.AIProvider:
            continue
        param = inspect.signature(obj.__init__).parameters.get("model")
        if param is not None and param.default is not inspect.Parameter.empty:
            defaults[name] = param.default
    return defaults


def test_ai_client_defaults_find_some_providers():
    """Guard the guard: a broken scan must not pass silently."""
    assert len(_ai_client_default_models()) >= 3, _ai_client_default_models()


def test_no_ai_client_provider_defaults_to_a_retired_model():
    dead = {n: m for n, m in _ai_client_default_models().items() if m in RETIRED_MODEL_IDS}
    assert not dead, (
        f"ai_client provider(s) default to a model confirmed dead: {dead}. "
        "These return 404/410 on every call."
    )


def test_410_is_treated_as_permanent_in_both_clients():
    """A model retired on a published date never comes back.

    Without this, 410 fell to the transient branch and the model was retried
    every two minutes forever — the exact waste the cooldown table exists to
    prevent.
    """
    import ai_client

    assert 410 in ai_client.AIClient._DEAD_CODES

    import inspect

    src = inspect.getsource(ai_helper.note_provider_failure)
    assert "410" in src, (
        "ai_helper.note_provider_failure does not mention 410; an end-of-lifed "
        "model gets the short transient cooldown instead of the long one"
    )
