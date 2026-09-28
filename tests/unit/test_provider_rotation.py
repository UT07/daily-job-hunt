"""Quota-aware provider rotation.

The pool is wide so the council can rotate OFF a provider that has hit a
limit. On 2026-09-28 it could not: the hand-maintained list held 8 entries,
three of them were dead simultaneously (404, 404, 429), and selection kept
offering them — so every council call logged "Critic call failed — returning
first candidate" and a 3-model council ran as one.
"""
import sys

import pytest

sys.path.insert(0, "lambdas/pipeline")
import ai_helper  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    ai_helper._reset_cooldowns()
    yield
    ai_helper._reset_cooldowns()


GROQ = {"name": "groq/gpt-oss-120b", "model": "openai/gpt-oss-120b"}
GROQ2 = {"name": "groq/qwen3.8-27b", "model": "qwen/qwen3.8-27b"}
OR1 = {"name": "openrouter/gemma-4-31b-it", "model": "google/gemma-4-31b-it:free"}
OR2 = {"name": "openrouter/glm-5.2", "model": "z-ai/glm-5.2:free"}


def test_429_cools_the_whole_account_not_just_the_model():
    """OpenRouter's free pool shares ONE daily quota across every model, so a
    429 on gemma means glm is equally unavailable. Cooling only the model is
    what let selection keep offering a sibling that could not possibly work."""
    ai_helper.note_provider_failure(OR1, 429)
    assert ai_helper._is_available(OR1) is False
    assert ai_helper._is_available(OR2) is False, "sibling on the same account must also cool"
    assert ai_helper._is_available(GROQ) is True, "a different account is unaffected"


def test_404_cools_the_model_but_leaves_the_account_usable():
    """A withdrawn model id says nothing about the account."""
    ai_helper.note_provider_failure(OR2, 404)
    assert ai_helper._is_available(OR2) is False
    assert ai_helper._is_available(OR1) is True


def test_rate_limit_windows_differ_by_provider():
    """Groq's is a per-minute token budget; OpenRouter's is a daily quota.
    One cooldown for both would either waste Groq or hammer OpenRouter."""
    assert ai_helper._RATE_LIMIT_COOLDOWN_S["groq"] < 300
    assert ai_helper._RATE_LIMIT_COOLDOWN_S["openrouter"] >= 900


def test_selection_routes_around_a_cooling_provider():
    pool = [GROQ, GROQ2, OR1]
    ai_helper.note_provider_failure(GROQ, 429)      # cools the groq ACCOUNT
    picked = ai_helper._select_diverse_providers(pool, n=3)
    names = {p["name"] for p in picked}
    assert "groq/gpt-oss-120b" not in names
    assert "groq/qwen3.8-27b" not in names, "same account"
    assert names == {"openrouter/gemma-4-31b-it"}


def test_selection_fails_open_when_everything_is_cooling():
    """A stale cooldown estimate must never leave the council with nothing to
    call — one wasted request beats a dead council."""
    pool = [GROQ, OR1]
    for p in pool:
        ai_helper.note_provider_failure(p, 429)
    picked = ai_helper._select_diverse_providers(pool, n=2)
    assert len(picked) == 2


def test_success_clears_both_keys():
    ai_helper.note_provider_failure(GROQ, 429)
    ai_helper.note_provider_failure(GROQ, 500)
    assert ai_helper._is_available(GROQ) is False
    ai_helper.note_provider_success(GROQ)
    assert ai_helper._is_available(GROQ) is True
    assert ai_helper._is_available(GROQ2) is True


def test_critic_is_available_while_both_accounts_are_up():
    """Generators take one account, the critic must come from another family."""
    pool = ai_helper._build_provider_list()
    gens = ai_helper._select_diverse_providers(pool, n=2)
    fams = {ai_helper._model_family(g["model"]) for g in gens}
    critic = ai_helper._select_diverse_providers(pool, n=1, exclude_families=fams)
    assert critic, "no cross-family critic available with a healthy pool"
    assert ai_helper._model_family(critic[0]["model"]) not in fams


def test_nvidia_provides_failover_when_openrouter_is_exhausted():
    """The 2026-09-28 failure, and the fix for it.

    OpenRouter's free pool shares ONE daily allowance across every model on it.
    When it emptied, every remaining provider was Groq — two gpt-oss entries
    and one qwen3, i.e. two families. Two generators consumed both and no third
    family remained, so every council call logged "Critic call failed" and a
    three-model council ran as one.

    NVIDIA NIM hosts the same nemotron weights on SEPARATE billing. It adds no
    new families deliberately — it adds a second route to families the council
    already wanted, which is what survives a quota outage. This test replaces
    an earlier one that asserted the limit was structural; it was, until the
    pool gained an independent-quota host.
    """
    pool = ai_helper._build_provider_list()
    ai_helper.note_provider_failure({"name": "openrouter/anything", "model": "m"}, 429)

    usable = [p for p in pool if ai_helper._is_available(p)]
    families = {ai_helper._model_family(p["model"]) for p in usable}
    assert len(families) >= 3, (
        f"only {sorted(families)} usable with OpenRouter cooled — not enough "
        "for two generators plus a cross-family critic"
    )

    gens = ai_helper._select_diverse_providers(usable, n=2)
    gen_families = {ai_helper._model_family(g["model"]) for g in gens}
    critic = ai_helper._select_diverse_providers(usable, n=1, exclude_families=gen_families)
    assert critic, "no cross-family critic available with OpenRouter cooled"
    assert ai_helper._model_family(critic[0]["model"]) not in gen_families


def test_nvidia_and_openrouter_draw_on_different_quotas():
    """The whole point: a 429 on one must not cool the other. Cooling by
    account rather than by model is what makes that true."""
    pool = ai_helper._build_provider_list()
    nvidia = [p for p in pool if p["key_param"].endswith("NVIDIA_API_KEY")]
    assert nvidia, "no NVIDIA-hosted entries in the pool"

    ai_helper.note_provider_failure({"name": "openrouter/gemma", "model": "g"}, 429)
    assert all(ai_helper._is_available(p) for p in nvidia), (
        "an OpenRouter 429 cooled NVIDIA — they are separate accounts and "
        "must cool independently"
    )


def test_pool_is_wide_enough_to_rotate():
    pool = ai_helper._build_provider_list()
    fams = {ai_helper._model_family(p["model"]) for p in pool}
    # Thresholds track reality: 9 providers / 6 families after three models
    # were marked unsuitable for scoring (a code model, a 2.6B model, and a 7B
    # Arabic-focused model). Raising these numbers by re-admitting models that
    # cannot score a JD would make the assertion actively harmful.
    assert len(pool) >= 8, f"only {len(pool)} providers — too few to rotate"
    assert len(fams) >= 5, f"only {len(fams)} families — critic choice too narrow"


def test_metered_qwen_stays_opt_in(monkeypatch):
    """DashScope is billed per call, unlike the free tiers."""
    monkeypatch.setenv("ENABLE_PAID_QWEN", "false")
    assert not [p for p in ai_helper._build_provider_list() if p["name"].startswith("qwen/")]
    monkeypatch.setenv("ENABLE_PAID_QWEN", "true")
    assert [p for p in ai_helper._build_provider_list() if p["name"].startswith("qwen/")]


def test_missing_registry_degrades_to_the_core_pool(monkeypatch):
    monkeypatch.setattr(ai_helper.os.path, "dirname", lambda _: "/nonexistent")
    pool = ai_helper._build_provider_list()
    assert len(pool) >= 5, "core pool must survive a missing registry"


def test_unsuitable_models_never_reach_the_pool():
    """Regression: removing a model from the hand-maintained list is not enough.

    _registry_providers() merges agents/model_registry.json for rotation
    breadth. Until it checked suitable_for_scoring, a code-completion model and
    a 2.6B model that had been deleted from openrouter_models walked straight
    back in through that merge, and the AI Eval Gate stayed at 25.0%
    tier_accuracy against a 63.2% baseline even after the "fix".

    Marking data unsuitable does nothing if the code consuming it ignores the
    mark.
    """
    import json
    import pathlib

    reg = json.loads((pathlib.Path("lambdas/pipeline/agents/model_registry.json")).read_text())
    unsuitable = {m["model"] for m in reg["models"] if not m.get("suitable_for_scoring", True)}
    assert unsuitable, "fixture expects at least one model marked unsuitable"

    pool_models = {p["model"] for p in ai_helper._build_provider_list()}
    leaked = unsuitable & pool_models
    assert not leaked, f"unsuitable models reached the council pool: {sorted(leaked)}"
