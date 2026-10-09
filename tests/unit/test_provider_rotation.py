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
    # Until 2026-10-08 this cooled GROQ and asserted groq/qwen3.8-27b went with
    # it ("same account"). Groq's docs meter each model separately, so a Groq
    # 429 is now model-scoped (tests/unit/test_cooldown_scope.py). OpenRouter
    # is the provider whose 429 is genuinely account-wide, so it carries the
    # account half of this assertion now.
    pool = [GROQ, GROQ2, OR1, OR2]
    ai_helper.note_provider_failure(GROQ, 429)      # cools that groq MODEL
    ai_helper.note_provider_failure(OR1, 429)       # cools the openrouter ACCOUNT
    picked = ai_helper._select_diverse_providers(pool, n=4)
    names = {p["name"] for p in picked}
    assert "groq/gpt-oss-120b" not in names
    assert "openrouter/glm-5.2" not in names, "same OpenRouter account"
    assert names == {"groq/qwen3.8-27b"}


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


def test_nvidia_is_in_the_pool_on_a_corrected_measurement():
    """NVIDIA NIM is the fourth independent quota. This test used to assert the
    opposite, and the reversal is the point.

    An earlier version disabled NVIDIA on 2026-09-28 with: "2/6 back-to-back
    succeeded, 4/6 with a 3s gap, the rest 503 ... it costs 75s per attempt to
    learn nothing." That run went out through a VPN endpoint NVIDIA was
    throttling. Re-measured the same day from an unblocked egress:

        sequential, 4 calls x 3 models   12/12   3-5s / 5-11s / 21-26s
        concurrent, 6 at once             4/6    503s returned in ~0.5s

    So the 503s under concurrency are real and the cost estimate was wrong by
    two orders of magnitude. A hop that answers two thirds of the time and
    fails in half a second is nearly free — and this account's limits are
    shared with nothing else in the chain, which is the property the council
    was short of.

    The lesson kept from the original test: measure under the load you will
    actually apply, and from the egress you will actually use.
    """
    pool = ai_helper._build_provider_list()
    nvidia = [p for p in pool if p["key_param"].endswith("NVIDIA_API_KEY")]
    assert nvidia, "NVIDIA_API_KEY unused — the council is back to three quotas"


def test_nvidia_timeout_stays_tight():
    """A long timeout on NVIDIA only ever pays for a hang.

    Successes land in 3-5s and 503s in ~0.5s, so anything beyond ~45s buys
    nothing and delays the next hop. The 60-75s budget the entries originally
    carried was sized for a latency profile never observed.
    """
    pool = ai_helper._build_provider_list()
    for p in pool:
        if p["key_param"].endswith("NVIDIA_API_KEY"):
            assert p["timeout"] <= 45, (
                f"{p['name']} timeout={p['timeout']}s — measured successes are "
                "3-5s and failures 0.5s; a longer budget only delays failover"
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
    had simply never used it. It holds under load — 24/24 — and answers in
    ~0.9s, which is why it sits ahead of every other failover.

    (An earlier version of this docstring contrasted it with "NVIDIA's 2/6".
    That NVIDIA figure was measured through a throttled VPN egress and does not
    hold; see test_nvidia_is_in_the_pool_on_a_corrected_measurement.)
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
    #
    # Families 5 -> 4 on 2026-10-09. The fifth was `inclusionai`, held up by a
    # single model -- ling-3.0-flash-sante -- that answered 404 "id withdrawn"
    # on every call from the production API. So the LIVE pool had four families
    # well before this line changed; the old floor was being met by a dead
    # entry, a check that could not tell "diverse" from "configured" (CLAUDE.md
    # #2). Keeping the corpse to stay above 5 is exactly the harm the paragraph
    # above warns about. Restoring a fifth family needs a newly PROBED model
    # (scripts/probe_models.py), not a lower bar or an old ID.
    assert len(pool) >= 8, f"only {len(pool)} providers — too few to rotate"
    assert len(fams) >= 4, f"only {len(fams)} families — critic choice too narrow"


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
