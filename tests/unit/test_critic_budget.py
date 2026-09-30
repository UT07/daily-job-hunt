"""The critic was starved of tokens, so it returned nothing 57% of the time.

Measured over 3 days of production logs (2026-09-27..30), across
naukribaba-tailor-resume and naukribaba-generate-cover-letter:

    adjudicated            24 / 107   22%
    critic_call_failed     61 / 107   57%   <-- dominant
    critic_unparseable     12 / 107   11%
    single_candidate       10 / 107    9%
    no_critic_family        0 / 107    0%

The orchestration spec predicted no_critic_family would be the main cause. It
never fires at all — providers.py already falls back to any critic. Phase 1
existed to measure before Phase 2 fixed, and this is why.

CRITIC_MAX_TOKENS was a flat 1024. agents/model_registry.json records
min_output_tokens: 3000 for openai/gpt-oss-120b and openai/gpt-oss-20b — Groq's
primary entries, so a frequent critic pick — with the note "Reasoning models
consume the budget internally before emitting". At 1024 they never reach the
answer, _call_provider treats empty content as a failure, and the council falls
back to candidate 1.

The same fix was applied to the GENERATOR budget months ago
(_REASONING_HEADROOM_TOKENS, rewrite_budget) and the critic was left flat: two
of everything, and the guard on only one of them.

Sized per model rather than raised globally because Groq bills
prompt_tokens + max_tokens against 8k/minute. build_critique_prompt truncates
each candidate to 3000 chars, so two candidates plus the rubric is ~2k tokens;
2k + 3256 stays inside 8k. A flat raise for every provider would not have been
checkable against that ceiling.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")

from ai_helper import (  # noqa: E402
    CRITIC_ANSWER_TOKENS,
    CRITIC_MAX_TOKENS,
    MAX_OUTPUT_TOKENS_CAP,
    critic_budget,
)

REASONING = "openai/gpt-oss-120b"


def test_a_reasoning_critic_clears_its_registry_floor():
    """3000 to think plus room to answer. Below this it emits nothing."""
    assert critic_budget({"model": REASONING}) >= 3000 + CRITIC_ANSWER_TOKENS


def test_both_gpt_oss_entries_are_covered():
    for model in ("openai/gpt-oss-120b", "openai/gpt-oss-20b"):
        assert critic_budget({"model": model}) > CRITIC_MAX_TOKENS, model


def test_a_non_reasoning_critic_is_not_over_provisioned():
    """Groq bills prompt + max_tokens against 8k/minute. Handing every critic
    3256 would spend headroom that models without a reasoning phase do not
    need."""
    assert critic_budget({"model": "gemini-3.5-flash"}) < critic_budget({"model": REASONING})


def test_the_budget_stays_inside_groqs_minute_allowance():
    """~2k prompt (two candidates truncated to 3000 chars each, plus rubric)
    plus the budget must stay under 8000, or the fix trades an empty answer
    for a 429."""
    assert 2000 + critic_budget({"model": REASONING}) < 8000


def test_an_unknown_model_behaves_exactly_as_before():
    """A provider the registry has never seen must not change behaviour."""
    assert critic_budget({"model": "some/model-nobody-registered"}) == CRITIC_MAX_TOKENS


def test_missing_and_malformed_providers_do_not_raise():
    for bad in (None, {}, {"model": None}, {"model": ""}):
        assert critic_budget(bad) == CRITIC_MAX_TOKENS, bad


def test_the_budget_never_exceeds_the_global_cap():
    """Several free endpoints reject large budgets outright."""
    assert critic_budget({"model": REASONING}) <= MAX_OUTPUT_TOKENS_CAP


def test_it_can_only_widen_never_narrow():
    """Same guarantee rewrite_budget makes. A model with a small or absent
    floor must still get at least the historical constant."""
    for model in ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "gemini-3.5-flash", "x/y"):
        assert critic_budget({"model": model}) >= CRITIC_MAX_TOKENS, model


# --- both engines must use it ----------------------------------------------

def test_the_graph_sizes_the_critic_per_model():
    import inspect

    from agents import nodes
    src = inspect.getsource(nodes.critique_node)
    assert "critic_budget(critic)" in src, (
        "the graph still passes a flat CRITIC_MAX_TOKENS; a reasoning critic "
        "will keep returning nothing"
    )


def test_the_legacy_engine_sizes_the_critic_per_model():
    import inspect

    import ai_helper
    src = inspect.getsource(ai_helper._council_complete_legacy)
    assert "critic_budget(critic_provider)" in src


def test_the_shim_reexports_it():
    """agents/nodes.py imports through agents._ai_helper, not ai_helper. A name
    missing from the shim is an ImportError at module load — the whole Lambda
    fails to start, rather than one feature being absent."""
    from agents import _ai_helper
    assert hasattr(_ai_helper, "critic_budget")
