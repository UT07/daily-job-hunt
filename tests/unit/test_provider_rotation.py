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


def test_nvidia_is_disabled_and_stays_disabled():
    """NVIDIA NIM free tier cannot serve as a failover, measured 2026-09-28.

    It authenticates, and a single call returns 200 in ~5s — which is exactly
    what made it look viable and what an earlier version of this file asserted
    as a working failover. Sustained load disproved it: 2/6 back-to-back
    succeeded, 4/6 with a 3s gap, the rest 503. In CI the same capacity limit
    surfaced as reads timing out, and the eval job was cancelled at 20 minutes.

    A failover is hit hardest precisely when the primary is exhausted, so one
    that degrades under load is worse than absent: it costs 75s per attempt to
    learn nothing. The entries stay in the registry, disabled, so nobody
    re-probes with a single call and reaches the same wrong conclusion.
    """
    pool = ai_helper._build_provider_list()
    nvidia = [p for p in pool if p["key_param"].endswith("NVIDIA_API_KEY")]
    assert not nvidia, (
        f"NVIDIA entries are in the live pool: {[p['name'] for p in nvidia]}. "
        "They 503 under load; re-enable only with a sustained-load measurement, "
        "not a one-shot probe."
    )


def test_gemini_keeps_the_council_alive_when_openrouter_is_exhausted():
    """The 2026-09-28 failure, and the fix that cost nothing.

    OpenRouter's free pool shares one daily allowance across every model on it.
    When it emptied, only Groq remained — two families, both consumed by the
    two generators, so select_critic fell back to "any provider" and the critic
    could be the same family as a generator. A critic reviewing its own
    family's output is not reviewing.

    Google AI Studio is an independent quota and was already available: the key
    had been in SSM since 2026-09-24 for pgvector embeddings, and the council
    had simply never used it. Unlike NVIDIA NIM, it holds under load —
    24/24 against NVIDIA's 2/6 — which is why it is trusted and NVIDIA is not.
    """
    pool = ai_helper._build_provider_list()
    ai_helper.note_provider_failure({"name": "openrouter/anything", "model": "m"}, 429)
    usable = [p for p in pool if ai_helper._is_available(p)]
    families = {ai_helper._model_family(p["model"]) for p in usable}

    assert "gemini" in families, "gemini must survive an OpenRouter outage — different account"
    assert len(families) >= 3, f"only {sorted(families)} — no room for a cross-family critic"

    gens = ai_helper._select_diverse_providers(usable, n=2)
    gen_families = {ai_helper._model_family(g["model"]) for g in gens}
    critic = ai_helper._select_diverse_providers(usable, n=1, exclude_families=gen_families)
    assert critic, "no cross-family critic with OpenRouter cooled"
    assert ai_helper._model_family(critic[0]["model"]) not in gen_families


def test_gemini_and_groq_cool_independently():
    """Separate accounts must not share a cooldown, or the failover is not one."""
    pool = ai_helper._build_provider_list()
    gemini = [p for p in pool if p["key_param"].endswith("GEMINI_API_KEY")]
    assert gemini, "no gemini entries in the pool"
    ai_helper.note_provider_failure({"name": "groq/gpt-oss-120b", "model": "m"}, 429)
    assert all(ai_helper._is_available(p) for p in gemini), "a Groq 429 cooled gemini"


def test_gemini_models_share_one_family():
    """All five collapse to "gemini", so selection cannot pick three of them and
    call that diverse."""
    pool = ai_helper._build_provider_list()
    fams = {ai_helper._model_family(p["model"]) for p in pool if "gemini" in p["name"]}
    assert fams == {"gemini"}, f"gemini entries split across families: {fams}"


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
