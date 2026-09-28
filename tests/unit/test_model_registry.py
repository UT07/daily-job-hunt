"""The verified model registry.

Backs the "N-model consensus" claim with something checkable: every entry was
proved by a live call at realistic prompt size (scripts/probe_models.py), and
these tests guard the structure that claim depends on.
"""
import warnings
warnings.filterwarnings("ignore")

import pytest

from agents import registry


def test_registry_loads_and_is_not_empty():
    assert len(registry.all_models()) >= 20


def test_every_entry_has_its_provenance():
    """An entry without verification metadata is an unverifiable claim."""
    for m in registry.all_models():
        for field in ("name", "provider", "model", "family", "url",
                      "key_param", "verified_on", "probe_latency_s",
                      "min_output_tokens"):
            assert field in m, f"{m.get('name')} missing {field}"


def test_names_are_unique():
    names = [m["name"] for m in registry.all_models()]
    assert len(names) == len(set(names))


def test_family_is_the_trainer_not_the_host():
    """A cross-family critic is only independent if the WEIGHTS differ.

    nemotron served via OpenRouter is still NVIDIA-trained; classifying it by
    host would let the council pick it to critique another nemotron and call
    that independent.
    """
    by_model = {m["model"]: m["family"] for m in registry.all_models()}
    for model, fam in by_model.items():
        if "nemotron" in model:
            assert fam == "nvidia", f"{model} classified as {fam}"
        if "gpt-oss" in model:
            assert fam == "openai"
        if "qwen" in model or "qwq" in model:
            assert fam == "qwen"


def test_reasoning_models_get_a_higher_token_floor():
    """Production's single SCORE_MAX_TOKENS=2048 starved these in prod."""
    for m in registry.all_models():
        if m["reasoning"]:
            assert m["min_output_tokens"] >= 3000, m["name"]


def test_select_diverse_prefers_distinct_families():
    """The pool is skewed -- over half is one family -- so a uniform sample
    would routinely return a single-vendor 'council'.

    Passes an explicit pool. select_diverse() otherwise defaults to
    available_models(), which filters on credentials being present in the
    environment: on CI there are none, so the pool is empty and this asserted
    nothing. It passed locally only because an unrelated test file had called
    load_dotenv() earlier in the same pytest process and left the keys in
    os.environ -- a test of selection logic must not depend on that.
    """
    pool = registry.all_models()
    picked = registry.select_diverse(5, pool=pool)
    assert len(picked) == 5
    assert len({m["family"] for m in picked}) == 5


def test_select_diverse_honours_exclusions():
    excluded = {"qwen", "openai"}
    for m in registry.select_diverse(6, exclude_families=excluded, pool=registry.all_models()):
        assert m["family"] not in excluded


def test_select_diverse_cannot_exceed_the_pool():
    pool = registry.all_models()
    assert len(registry.select_diverse(999, pool=pool)) == len(pool)


def test_select_diverse_is_empty_when_everything_is_excluded():
    assert registry.select_diverse(
        3, exclude_families=registry.families(), pool=registry.all_models()) == []


def test_selection_needs_no_credentials(monkeypatch):
    """Guards the CI failure above: with every provider key unset, an explicit
    pool must still select. Only available_models() may depend on the env."""
    for env in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "QWEN_API_KEY",
                "NVIDIA_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    assert len(registry.select_diverse(3, pool=registry.all_models())) == 3


def test_available_models_needs_credentials(monkeypatch):
    for env in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "QWEN_API_KEY",
                "NVIDIA_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    assert registry.available_models() == []


def test_as_chat_model_refuses_without_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    entry = next(m for m in registry.all_models() if m["provider"] == "groq")
    with pytest.raises(RuntimeError, match="no credential"):
        registry.as_chat_model(entry)


def test_as_chat_model_strips_the_completions_suffix(monkeypatch):
    """ChatOpenAI wants the API root; the registry stores the full endpoint."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    entry = next(m for m in registry.all_models() if m["provider"] == "groq")
    llm = registry.as_chat_model(entry)
    assert str(llm.openai_api_base).rstrip("/").endswith("/v1")
    assert "chat/completions" not in str(llm.openai_api_base)


def test_unsuitable_models_are_flagged_not_silently_dropped():
    """Verification and capability are different claims.

    Every entry was PROVED to respond correctly at realistic prompt size; that
    says nothing about whether it can reason about a job description. Selecting
    the council pool on family diversity alone put a code-completion model and
    a 2.6B model in it, and the AI Eval Gate measured tier_accuracy falling
    63.2% -> 25.0%. They stay in the registry — they are genuinely verified —
    but carry the reason they must not be selected for scoring.
    """
    unsuitable = [m for m in registry.all_models() if not m.get("suitable_for_scoring", True)]
    assert unsuitable, "the two known-unsuitable models must stay flagged"
    for m in unsuitable:
        assert m.get("unsuitable_reason"), f"{m['name']} flagged with no reason"


def test_every_entry_states_its_suitability():
    for m in registry.all_models():
        assert "suitable_for_scoring" in m, f"{m['name']} does not say"
