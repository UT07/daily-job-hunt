from unittest.mock import patch

from agents import providers

FAKE = [
    {"name": "groq/a", "model": "openai/gpt-oss-120b", "key_param": "/k/a", "url": "u"},
    {"name": "or/b", "model": "z-ai/glm-5.2:free", "key_param": "/k/b", "url": "u"},
]


def test_select_generators_returns_distinct_families():
    with patch.object(providers, "_build_provider_list", return_value=FAKE):
        gens = providers.select_generators(2)
    assert len({providers.family_of(g) for g in gens}) == 2


def test_select_critic_excludes_generator_families():
    with patch.object(providers, "_build_provider_list", return_value=FAKE):
        critic = providers.select_critic({providers.family_of(FAKE[0])})
    assert critic is not None
    assert providers.family_of(critic) != providers.family_of(FAKE[0])


def test_call_one_returns_candidate_shape():
    hit = {"content": "hello", "provider": "groq/a", "model": "m"}
    with patch.object(providers, "_call_provider", return_value=hit):
        out = providers.call_one(FAKE[0], "p", "s", 0.3, 100)
    assert out == hit


def test_call_one_returns_none_on_provider_failure():
    with patch.object(providers, "_call_provider", return_value=None):
        assert providers.call_one(FAKE[0], "p", "s", 0.3, 100) is None
