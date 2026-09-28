"""Failover ORDER, not just failover membership.

Measured 2026-09-28. The chain held 13 entries but only three independent
quotas — Groq (3 models), OpenRouter (8 models behind ONE account-wide
`free-models-per-day` cap), Gemini (5 models). Entry count and resilience are
different axes: all eight OpenRouter entries fail as a single unit the moment
that daily cap trips.

On that day the cap was already exhausted, and Gemini sat at positions 9-13 —
behind all five OpenRouter entries. Every call therefore spent five doomed
round-trips before reaching the one provider that could answer, and the CI
eval gate reported `families_served: ["groq"]` on a pool that genuinely had a
working second family available the whole time.

Ordering is the fix: the first failover hop must be the provider most likely
to answer, not the one that happens to be declared first.
"""
import sys

sys.path.insert(0, "lambdas/pipeline")
import ai_helper  # noqa: E402


def _names():
    return [p["name"] for p in ai_helper._build_provider_list()]


def _first_index(names, prefix):
    return next((i for i, n in enumerate(names) if n.startswith(prefix)), None)


def test_gemini_is_tried_before_openrouter():
    """Gemini's free tier is independent and generous; OpenRouter's is 50/day.

    Fails against the old order, where gemini/gemini-3.5-flash-lite was
    appended AFTER the openrouter loop and landed at index 8.
    """
    names = _names()
    gem = _first_index(names, "gemini/")
    orr = _first_index(names, "openrouter/")
    assert gem is not None, f"no gemini provider in the chain: {names}"
    assert orr is not None, f"no openrouter provider in the chain: {names}"
    assert gem < orr, (
        f"gemini is at {gem}, openrouter at {orr} — every call burns "
        f"{orr - gem if gem > orr else gem - orr} doomed hops on an account whose "
        f"free-models-per-day cap is routinely exhausted. Order: {names}"
    )


def test_groq_remains_the_primary():
    """Reordering the failover must not demote the primary.

    Groq is fastest (~300-800ms) and its quota is separate from OpenRouter's,
    so it stays first. This guards the fix from over-correcting.
    """
    names = _names()
    assert names[0].startswith("groq/"), f"expected groq first, got {names[0]}"


def test_withdrawn_openrouter_model_is_not_in_the_chain():
    """inclusionai/ling-3.0-flash-fin:free was withdrawn from OpenRouter.

    Confirmed 2026-09-28 against the live /models listing: it is absent from
    the 16 free models served, and CI logged `returned 404` for it on every
    call. A withdrawn id is a guaranteed-failing hop, not council depth.
    """
    models = [p["model"] for p in ai_helper._build_provider_list()]
    assert "inclusionai/ling-3.0-flash-fin:free" not in models, (
        "withdrawn model id still occupies a failover slot"
    )


def test_every_independent_quota_is_represented():
    """The council's resilience is its count of independent ACCOUNTS.

    Three key_params means three quotas that can fail independently. Asserting
    on key_param rather than model count is deliberate: 8 OpenRouter models
    share one cap and are one point of failure, not eight.
    """
    keys = {p["key_param"] for p in ai_helper._build_provider_list()}
    for required in (
        "/naukribaba/GROQ_API_KEY",
        "/naukribaba/GEMINI_API_KEY",
        "/naukribaba/OPENROUTER_API_KEY",
    ):
        assert required in keys, f"{required} missing — quota count dropped. Have: {sorted(keys)}"


# ---------------------------------------------------------------------------
# The A/B shuffle must not undo the ordering
# ---------------------------------------------------------------------------

def test_ab_shuffle_never_promotes_a_cooled_down_provider(monkeypatch):
    """Exploration is worthwhile only among providers that might answer.

    `ai_complete` reshuffles the failover tail on ~20% of calls. That was
    sound when the tail was unmeasured. It is not sound once a provider is in
    cooldown: the shuffle can promote a known-429'd OpenRouter entry ahead of
    a known-good Gemini, spending the call's latency budget on a provider the
    cooldown table already says will fail.

    Observed as a 1-in-7 flake in the unit suite before this guard existed.
    """
    ai_helper._reset_cooldowns()
    providers = ai_helper._build_provider_list()
    # Cool the whole OpenRouter account, exactly as a 429 would.
    orr = next(p for p in providers if p["name"].startswith("openrouter/"))
    ai_helper.note_provider_failure(orr, 429)

    # Force the shuffle branch on every call.
    monkeypatch.setattr(ai_helper.random, "random", lambda: 0.0)

    seen = []
    monkeypatch.setattr(
        ai_helper, "_call_provider",
        lambda p, *a, **k: (seen.append(p["name"]), None)[1],
    )
    try:
        ai_helper.ai_complete("hi")
    except RuntimeError:
        pass  # every provider stubbed to fail; we only care about the ORDER

    ai_helper._reset_cooldowns()

    cooled = [i for i, n in enumerate(seen) if n.startswith("openrouter/")]
    live = [i for i, n in enumerate(seen) if not n.startswith("openrouter/")]
    assert cooled and live, f"expected both cooled and live providers to be tried: {seen}"
    assert min(cooled) > max(live), (
        "a cooled-down OpenRouter provider was tried before a live one:\n  "
        + "\n  ".join(f"{i:2d}. {n}" for i, n in enumerate(seen))
    )


# ---------------------------------------------------------------------------
# NVIDIA NIM — re-enabled on a corrected measurement
# ---------------------------------------------------------------------------

def test_nvidia_is_a_fourth_independent_quota():
    """NVIDIA NIM has its own account, so it survives a Groq/OpenRouter outage.

    It was disabled earlier on 2026-09-28 for "503s under load, costing 75s per
    attempt". Re-measured the same day from an unblocked egress: the earlier
    run went out through a VPN that NVIDIA was throttling. Sequential load is
    12/12 across all three models; concurrency is 4/6 — and, decisively, the
    503s return in ~0.5s, not 75s. The cost estimate that justified disabling
    it was simply wrong, and a two-thirds-reliable hop that fails in half a
    second is worth having.
    """
    keys = {p["key_param"] for p in ai_helper._build_provider_list()}
    assert "/naukribaba/NVIDIA_API_KEY" in keys, (
        "NVIDIA_API_KEY unused — the council is back to three quotas"
    )


def test_nvidia_sits_between_gemini_and_openrouter():
    """Order by measured reliability: Gemini 0.9s/always, NVIDIA 3-5s/two-thirds,
    OpenRouter 50-requests-a-day.
    """
    names = _names()
    gem = _first_index(names, "gemini/")
    nv = _first_index(names, "nvidia/")
    orr = _first_index(names, "openrouter/")
    assert nv is not None, f"no nvidia provider in the chain: {names}"
    assert gem < nv < orr, (
        f"expected gemini({gem}) < nvidia({nv}) < openrouter({orr}); order: {names}"
    )


def test_the_slow_nemotron_stays_out():
    """nemotron-3.5-lightning-30b-a3b answers in 21-26s.

    Excluded on LATENCY, not availability — it was 4/4 sequential. A failover
    hop four to eight times slower than the alternatives above it is a bad
    trade when Gemini answers the same prompt in 0.9s. Recorded explicitly so
    the next person does not "fix" its absence by re-enabling it.
    """
    models = [p["model"] for p in ai_helper._build_provider_list()]
    assert "nvidia/nemotron-3.5-lightning-30b-a3b" not in models
