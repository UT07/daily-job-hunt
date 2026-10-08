"""A critic is never drawn from a family that generated.

THE DEFECT (audit, 2026-10-08). `select_critics` asked for providers outside
the generating families and, when none existed, fell back to
`_select_diverse_providers(all_providers, n)` with NO exclusion. So when every
live family had generated — routine since `fill_same_family` lets a Gemini-only
pool field two Gemini generators — a Gemini model judged two Gemini candidates
and the run was recorded as `critique_outcome="adjudicated"`, indistinguishable
from a cross-family verdict.

Commit 17b3b48 is explicit that the critic slot must never relax: "a critic
drawn from the family that generated is not an independent reviewer, which is
the entire purpose of the slot." So the fallback is removed, not relabelled:
no unused family means no critic, and critique_node records the existing
`no_critic_family` outcome. Asserted on the REAL select_critics over a stubbed
pool, not on a patched one — every other critique test patches it, which is
how the fallback went unexamined.
"""
import sys
from unittest.mock import patch

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")

import ai_helper  # noqa: E402
from agents import nodes, providers  # noqa: E402

GEMINI = [{"name": f"gemini/g-{i}", "model": f"gemini-{i}-flash"} for i in range(3)]
GROQ = [{"name": "groq/gpt-oss-120b", "model": "openai/gpt-oss-120b"}]


def setup_function():
    ai_helper._reset_cooldowns()


def teardown_function():
    ai_helper._reset_cooldowns()


def _fam(p):
    return ai_helper._model_family(p["model"])


def test_the_double_is_one_family():
    assert {_fam(p) for p in GEMINI} == {"gemini"}
    assert _fam(GROQ[0]) != "gemini"


def test_no_unused_family_means_no_critic():
    with patch.object(providers, "_build_provider_list", return_value=GEMINI):
        picked = providers.select_critics({"gemini"}, n=3)
    assert picked == [], (
        f"a critic from the generating family was returned: {[p['name'] for p in picked]}")


def test_an_unused_family_is_still_used():
    with patch.object(providers, "_build_provider_list", return_value=GEMINI + GROQ):
        picked = providers.select_critics({"gemini"}, n=3)
    assert picked and all(_fam(p) != "gemini" for p in picked)


def test_the_council_records_it_as_no_critic_family_and_calls_no_critic():
    cands = [{"content": "a", "provider": "gemini", "model": "gemini-0-flash"},
             {"content": "b", "provider": "gemini", "model": "gemini-1-flash"}]
    with patch.object(providers, "_build_provider_list", return_value=GEMINI), \
         patch.object(nodes, "call_one") as call_one:
        out = nodes.critique_node({"candidates": cands, "task_description": "t"})
    assert out["critique_outcome"] == "no_critic_family", out["critique_outcome"]
    call_one.assert_not_called()
