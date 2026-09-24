"""Winner-parity contract between the legacy council and the LangGraph port.

This module is the gate for flipping COUNCIL_ENGINE to "langgraph" in
production. Read this docstring before trusting it for that: it states
precisely what parity IS and IS NOT established below.

WHAT IS ESTABLISHED
    Given identical provider behaviour, both engines return the same
    WINNING CONTENT:
      * under normal conditions, with a stubbed deterministic selection
        (test_both_engines_select_the_same_winner)
      * when a generator's assigned provider fails and the engine falls
        back to a different family (test_both_engines_agree_when_a_generator_falls_back)
      * with the REAL, unstubbed _select_diverse_providers running in both
        engines rather than a canned stub (test_engines_agree_with_real_unstubbed_provider_selection)

WHAT IS DELIBERATELY NOT ESTABLISHED
    * Critic IDENTITY parity. The two engines choose their critic from a
      DIFFERENT excluded-family set when a fallback has occurred:
        - legacy (lambdas/pipeline/ai_helper.py, _council_complete_legacy)
          excludes the families of the ORIGINALLY SELECTED generators
          (`gen_families`), even if one of them actually produced its
          candidate via a fallback to a different family.
        - the graph (agents/nodes.py::critique_node) excludes the families
          of the candidates that ACTUALLY SUCCEEDED (`used`), which can
          differ from the originally-assigned generator families after a
          fallback.
      This is a RULED, INTENTIONAL divergence, not a defect: a critic
      should be independent of the outputs it is judging, and the graph's
      "actually succeeded" rule achieves that more faithfully than
      legacy's "originally assigned" rule. Do NOT "fix" this by making
      either engine match the other. Tests in this module assert WINNER
      CONTENT parity only, never critic identity.
    * Repair-loop behaviour. Legacy has no repair loop at all, so there is
      nothing on that side to compare against. The graph's bounded
      reflexion loop (quality_gate / repair_node) is exercised only by
      test_graph_only_repair_loop_bounded_and_returns_valid_winner, which
      is explicitly labelled graph-only and asserts no parity.

The legacy council and the graph are driven with the same deterministic fake
providers; any divergence in the selected WINNER is a port defect.
"""
from unittest.mock import patch

import pytest

from agents import graph as graph_mod
from lambdas.pipeline import ai_helper

P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b", "key_param": "/k/a", "url": "u"}
P2 = {"name": "or/b", "model": "z-ai/glm-5.2:free", "key_param": "/k/b", "url": "u"}
P3 = {"name": "meta/c", "model": "meta/llama-4", "key_param": "/k/c", "url": "u"}
# Distinct family from P1/P2/P3 ("mixtral-8x22b") -- reachable only via the
# generator-fallback path in the tests below, never picked directly.
P4 = {"name": "meta/d", "model": "meta/mixtral-8x22b", "key_param": "/k/d", "url": "u"}

CAND_A = {"content": "alpha", "provider": "groq/a", "model": "openai/gpt-oss-120b"}
CAND_B = {"content": "beta", "provider": "or/b", "model": "z-ai/glm-5.2:free"}
CAND_D = {"content": "delta", "provider": "meta/d", "model": "meta/mixtral-8x22b"}


def _fake_call(provider, prompt, system="", temperature=0.3, max_tokens=4096):
    if provider["name"] == "groq/a":
        return CAND_A
    if provider["name"] == "or/b":
        return CAND_B
    return {"content": "[20, 88]", "provider": "meta/c", "model": "meta/llama-4"}


@pytest.mark.parametrize("winner_content", ["beta"])
def test_both_engines_select_the_same_winner(winner_content, monkeypatch):
    # council_complete (the dispatcher) reads this flag; without clearing it
    # an ambient COUNCIL_ENGINE=langgraph would route the "legacy" call below
    # through agents.graph instead, silently skipping the patches on
    # ai_helper and hitting real providers.
    monkeypatch.delenv("COUNCIL_ENGINE", raising=False)

    with patch.object(ai_helper, "_build_provider_list", return_value=[P1, P2, P3]), \
         patch.object(ai_helper, "_call_provider", side_effect=_fake_call), \
         patch.object(ai_helper, "_select_diverse_providers",
                      side_effect=lambda ps, n, exclude_families=None: (
                          [P3] if exclude_families else [P1, P2][:n])):
        legacy = ai_helper.council_complete("p", "s", "desc", n_generators=2)

    with patch("agents.providers._build_provider_list", return_value=[P1, P2, P3]), \
         patch("agents.providers._call_provider", side_effect=_fake_call), \
         patch("agents.providers._select_diverse_providers",
               side_effect=lambda ps, n, exclude_families=None: (
                   [P3] if exclude_families else [P1, P2][:n])):
        modern = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)

    assert legacy["content"] == modern["content"] == winner_content


def _make_fallback_fake(call_log):
    """P1 always fails; P2 and P4 succeed; P3 is the critic.

    The critic score is derived from WHICH candidate content ("delta" vs
    "beta") appears first in the prompt, not from candidate position -- the
    graph's parallel generate branches do not guarantee candidate order, so
    a position-keyed score (as in _fake_call above) would be order-dependent
    here in a way that could flip the winner between runs.
    """
    def _fake(provider, prompt, system="", temperature=0.3, max_tokens=4096):
        call_log.append(provider["name"])
        if provider["name"] == "groq/a":
            return None  # forces the fallback path in both engines
        if provider["name"] == "or/b":
            return CAND_B
        if provider["name"] == "meta/d":
            return CAND_D
        # meta/c: the critic. Always rank the fallback-produced "delta"
        # candidate highest, regardless of its position in the prompt.
        idx_delta = prompt.find("delta")
        idx_beta = prompt.find("beta")
        scores = [90, 40] if idx_delta < idx_beta else [40, 90]
        return {"content": str(scores), "provider": "meta/c", "model": "meta/llama-4"}
    return _fake


def test_both_engines_agree_when_a_generator_falls_back(monkeypatch):
    """The fallback path is where legacy and the graph structurally diverge
    the most: legacy's per-generator loop shares a `used_families` set
    across the whole serial batch, while the graph's parallel `generate`
    branches cannot see each other and can only exclude their OWN assigned
    family (see agents/nodes.py::generate_node's docstring -- this is a
    documented, accepted tradeoff, not something to "fix"). Both must still
    surface the same winning content despite that.

    P1 ("groq/a") is the generator assigned to slot 1 and always fails, so
    both engines must retry through the rest of the pool and land on P4
    ("meta/d", a distinct family) to fill that slot. P2 ("or/b") is the
    second generator and succeeds directly, no fallback needed for it.
    """
    monkeypatch.delenv("COUNCIL_ENGINE", raising=False)
    provider_list = [P1, P4, P2, P3]  # order matters: P4 before P2 so the
    # fallback loop lands on P4 (a genuinely different provider) rather than
    # on P2 (which is also the second original generator, which would mask
    # the fallback behind a duplicate-of-a-planned-generator candidate).

    legacy_log = []
    with patch.object(ai_helper, "_build_provider_list", return_value=provider_list), \
         patch.object(ai_helper, "_call_provider", side_effect=_make_fallback_fake(legacy_log)), \
         patch.object(ai_helper, "_select_diverse_providers",
                      side_effect=lambda ps, n, exclude_families=None: (
                          [P3] if exclude_families else [P1, P2][:n])):
        legacy = ai_helper.council_complete("p", "s", "desc", n_generators=2)

    modern_log = []
    with patch("agents.providers._build_provider_list", return_value=provider_list), \
         patch("agents.providers._call_provider", side_effect=_make_fallback_fake(modern_log)), \
         patch("agents.providers._select_diverse_providers",
               side_effect=lambda ps, n, exclude_families=None: (
                   [P3] if exclude_families else [P1, P2][:n])):
        modern = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)

    assert legacy["content"] == modern["content"] == "delta"

    # Not vacuous: confirm the failing provider was actually invoked and a
    # genuinely different-family provider was then reached, in BOTH engines
    # -- this is the evidence that the fallback branch (not the happy path)
    # is what produced the winner above.
    for name, log in (("legacy", legacy_log), ("modern", modern_log)):
        assert "groq/a" in log, f"{name}: the failing assigned provider was never called"
        assert "meta/d" in log, f"{name}: the fallback provider was never reached"
        assert "or/b" in log, f"{name}: the non-failing second generator did not run normally"
        assert "meta/c" in log, f"{name}: the critic never ran"


DIVERSE_POOL = [
    {"name": "p-alpha", "model": "vendor1/alpha-model", "key_param": "/k/1", "url": "u"},
    {"name": "p-beta", "model": "vendor2/beta-model", "key_param": "/k/2", "url": "u"},
    {"name": "p-gamma", "model": "vendor3/gamma-model", "key_param": "/k/3", "url": "u"},
    {"name": "p-delta", "model": "vendor4/delta-model", "key_param": "/k/4", "url": "u"},
]
# All four providers are rigged to produce byte-identical output and to be
# scored identically by the critic. That is deliberate: _select_diverse_providers
# shuffles for real in this test (it is not stubbed), so the two engines can
# legitimately draw DIFFERENT 2-of-4 subsets on any given call -- that is
# not a bug. Making content provider-invariant means the winning CONTENT is
# invariant to which subset got drawn, so a literal cross-engine equality
# assertion is meaningful (guaranteed by construction) rather than lucky,
# while the real shuffle-then-family-dedup code still runs end to end: if
# it raised, returned zero providers, or otherwise broke, the calls below
# would fail loudly rather than this test silently passing.
SAME_CONTENT = "identical-output-regardless-of-which-provider-generated-it"


def _make_diverse_fake(call_log):
    def _fake(provider, prompt, system="", temperature=0.3, max_tokens=4096):
        if "Rate each candidate" in prompt:
            call_log.append((provider["name"], "critique"))
            n = prompt.count("--- CANDIDATE")
            return {"content": str([50] * n), "provider": provider["name"], "model": provider["model"]}
        call_log.append((provider["name"], "generate"))
        return {"content": SAME_CONTENT, "provider": provider["name"], "model": provider["model"]}
    return _fake


def test_engines_agree_with_real_unstubbed_provider_selection(monkeypatch):
    """Does NOT stub _select_diverse_providers -- the real shuffle-then-
    family-dedup algorithm runs in both engines (only _build_provider_list
    and _call_provider are patched), so a bug in that selection logic
    itself, not just in the dispatch layer, would surface here.

    Looped several times so different real shuffles actually get exercised
    (random.shuffle draws independently per call, with no shared seed
    between the two engines or across iterations). On every iteration:
    both engines must still agree on the winning content (guaranteed by the
    provider-invariant fake -- see DIVERSE_POOL above), and call_log must
    show the real selection genuinely engaged more than one distinct
    provider as a generator, proving this isn't a vacuous single-candidate
    pass.
    """
    monkeypatch.delenv("COUNCIL_ENGINE", raising=False)

    for _ in range(5):
        legacy_log = []
        with patch.object(ai_helper, "_build_provider_list", return_value=list(DIVERSE_POOL)), \
             patch.object(ai_helper, "_call_provider", side_effect=_make_diverse_fake(legacy_log)):
            legacy = ai_helper.council_complete("p", "s", "desc", n_generators=2)

        modern_log = []
        with patch("agents.providers._build_provider_list", return_value=list(DIVERSE_POOL)), \
             patch("agents.providers._call_provider", side_effect=_make_diverse_fake(modern_log)):
            modern = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)

        assert legacy["content"] == modern["content"] == SAME_CONTENT

        for name, log in (("legacy", legacy_log), ("modern", modern_log)):
            generated = {n for n, role in log if role == "generate"}
            assert len(generated) >= 2, (
                f"{name}: expected 2 distinct real generators from real selection, got {log}"
            )


def test_graph_only_repair_loop_bounded_and_returns_valid_winner():
    """Graph-only -- legacy has no repair loop, so there is no parity claim
    here, unlike every other test in this module.

    Seeds a permanently-failing guard_report (it is never cleared, so
    quality_gate keeps routing to "repair" until its own attempt cap fires)
    to force the graph's headline addition -- the bounded reflexion loop --
    to actually run, and confirms it terminates at the documented 2-attempt
    bound (agents/nodes.py::quality_gate) instead of looping forever, still
    producing a well-formed {content, provider, model} winner.
    """
    with patch("agents.providers._build_provider_list", return_value=[P1]), \
         patch("agents.providers._call_provider", return_value=CAND_A):
        graph = graph_mod.build_council_graph()
        final = graph.invoke(
            {
                "prompt": "p",
                "system": "s",
                "task_description": "desc",
                "n_generators": 1,
                "temperature": 0.3,
                "candidates": [],
                "repair_attempts": 0,
                "trace_id": "test-graph-only-repair",
                "guard_report": {"passed": False, "violations": ["forced"]},
            },
            config={"configurable": {"thread_id": "test-graph-only-repair"}},
        )

    assert final["repair_attempts"] == 2, "expected exactly 2 repair rounds before the budget cap finalizes"
    winner = final.get("winner")
    assert winner is not None
    assert set(winner) == {"content", "provider", "model"}
    assert winner["content"] == CAND_A["content"]


def test_engine_flag_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("COUNCIL_ENGINE", raising=False)
    assert ai_helper._council_engine() == "legacy"


def test_engine_flag_honours_env_override(monkeypatch):
    monkeypatch.setenv("COUNCIL_ENGINE", "langgraph")
    assert ai_helper._council_engine() == "langgraph"
