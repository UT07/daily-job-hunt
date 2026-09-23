"""Both engines must agree given identical provider behaviour.

The legacy council and the graph are driven with the same deterministic fake
providers; any divergence in the selected winner is a port defect.
"""
from unittest.mock import patch

import pytest

from agents import graph as graph_mod
from lambdas.pipeline import ai_helper

P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b", "key_param": "/k/a", "url": "u"}
P2 = {"name": "or/b", "model": "z-ai/glm-5.2:free", "key_param": "/k/b", "url": "u"}
P3 = {"name": "meta/c", "model": "meta/llama-4", "key_param": "/k/c", "url": "u"}

CAND_A = {"content": "alpha", "provider": "groq/a", "model": "openai/gpt-oss-120b"}
CAND_B = {"content": "beta", "provider": "or/b", "model": "z-ai/glm-5.2:free"}


def _fake_call(provider, prompt, system="", temperature=0.3, max_tokens=4096):
    if provider["name"] == "groq/a":
        return CAND_A
    if provider["name"] == "or/b":
        return CAND_B
    return {"content": "[20, 88]", "provider": "meta/c", "model": "meta/llama-4"}


@pytest.mark.parametrize("winner_content", ["beta"])
def test_both_engines_select_the_same_winner(winner_content):
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


def test_engine_flag_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("COUNCIL_ENGINE", raising=False)
    assert ai_helper._council_engine() == "legacy"


def test_engine_flag_honours_env_override(monkeypatch):
    monkeypatch.setenv("COUNCIL_ENGINE", "langgraph")
    assert ai_helper._council_engine() == "langgraph"
