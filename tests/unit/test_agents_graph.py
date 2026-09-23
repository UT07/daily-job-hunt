from unittest.mock import patch

from agents import graph as graph_mod

CAND_A = {"content": "alpha", "provider": "groq/a", "model": "openai/gpt-oss-120b"}
CAND_B = {"content": "beta", "provider": "or/b", "model": "z-ai/glm-5.2:free"}
P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b"}
P2 = {"name": "or/b", "model": "z-ai/glm-5.2:free"}


def test_graph_compiles():
    assert graph_mod.build_council_graph() is not None


def test_end_to_end_picks_highest_scoring_candidate():
    calls = iter([CAND_A, CAND_B, {"content": "[10, 95]", "provider": "c", "model": "m3"}])
    with patch("agents.nodes.select_generators", return_value=[P1, P2]), \
         patch("agents.nodes.select_critic", return_value={"name": "c", "model": "meta/x"}), \
         patch("agents.nodes.call_one", side_effect=lambda *a, **k: next(calls)):
        out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)
    assert out["content"] == "beta"


def test_return_shape_matches_legacy_contract():
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND_A):
        out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=1)
    assert set(out) == {"content", "provider", "model"}


def test_raises_when_every_generator_fails():
    import pytest
    with patch("agents.nodes.select_generators", return_value=[P1, P2]), \
         patch("agents.nodes.call_one", return_value=None):
        with pytest.raises(RuntimeError, match="all generators failed"):
            graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)
