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


def test_critic_can_still_be_found_after_a_whole_account_cools():
    """The exact 2026-09-28 shape: generators take one account, that account
    then rate-limits, and the critic must come from elsewhere."""
    pool = ai_helper._build_provider_list()
    ai_helper.note_provider_failure({"name": "openrouter/x", "model": "y"}, 429)
    gens = ai_helper._select_diverse_providers(pool, n=2)
    fams = {ai_helper._model_family(g["model"]) for g in gens}
    critic = ai_helper._select_diverse_providers(pool, n=1, exclude_families=fams)
    assert critic, "no critic available with OpenRouter cooling"
    assert ai_helper._model_family(critic[0]["model"]) not in fams


def test_pool_is_wide_enough_to_rotate():
    pool = ai_helper._build_provider_list()
    fams = {ai_helper._model_family(p["model"]) for p in pool}
    assert len(pool) >= 10, f"only {len(pool)} providers — too few to rotate"
    assert len(fams) >= 7, f"only {len(fams)} families — critic choice too narrow"


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
