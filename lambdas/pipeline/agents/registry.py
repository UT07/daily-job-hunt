"""Verified model registry, exposed as LangChain chat models.

Every entry in model_registry.json was proved by a live call at realistic
prompt size (see scripts/probe_models.py). This module turns those entries
into `langchain_openai.ChatOpenAI` instances -- all five providers speak the
OpenAI chat-completions dialect, so one adapter covers the whole pool and the
council gets LangChain's retry/callback/tracing surface for free.

Selection is family-aware. `family` is the vendor that TRAINED the weights,
not the one serving them: openrouter/nemotron and nvidia/nemotron are the same
family despite different providers, so a "cross-family critic" that picked one
to check the other would not actually be independent.
"""
import json
import os
import pathlib
import random

_REGISTRY_PATH = pathlib.Path(__file__).with_name("model_registry.json")
_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "qwen": "QWEN_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def load_registry() -> dict:
    return json.loads(_REGISTRY_PATH.read_text())


def all_models() -> list[dict]:
    """Every verified entry, regardless of whether its key is available."""
    return load_registry()["models"]


def available_models() -> list[dict]:
    """Verified entries whose provider credentials are actually present.

    A registry entry is a claim that the model worked when probed; it is not a
    claim that this process can reach it. Filtering here keeps a missing key
    from becoming a runtime failure inside the graph.
    """
    return [m for m in all_models() if os.environ.get(_KEY_ENV.get(m["provider"], ""))]


def families(models: list[dict] | None = None) -> set[str]:
    return {m["family"] for m in (models if models is not None else all_models())}


def select_diverse(n: int, exclude_families: set[str] | None = None,
                   pool: list[dict] | None = None, rng=random) -> list[dict]:
    """Pick up to n models, preferring one per family before repeating a family.

    Round-robin over families rather than sampling the flat list, because the
    pool is heavily skewed -- 14 of 26 entries are Qwen-family, so a uniform
    sample would usually return an all-Qwen "council" and the consensus would
    be measuring one vendor's opinion three times.
    """
    pool = [m for m in (pool if pool is not None else available_models())
            if m["family"] not in (exclude_families or set())]
    if not pool:
        return []
    by_family: dict[str, list[dict]] = {}
    for m in pool:
        by_family.setdefault(m["family"], []).append(m)
    for group in by_family.values():
        rng.shuffle(group)
    order = list(by_family)
    rng.shuffle(order)
    picked: list[dict] = []
    while len(picked) < n and any(by_family[f] for f in order):
        for fam in order:
            if by_family[fam] and len(picked) < n:
                picked.append(by_family[fam].pop())
    return picked


def as_chat_model(entry: dict, temperature: float = 0.3, max_tokens: int = 3000):
    """Build a LangChain ChatOpenAI bound to one registry entry."""
    from langchain_openai import ChatOpenAI

    key = os.environ.get(_KEY_ENV.get(entry["provider"], ""))
    if not key:
        raise RuntimeError(f"no credential for provider {entry['provider']}")
    return ChatOpenAI(
        model=entry["model"],
        base_url=entry["url"].rsplit("/chat/completions", 1)[0],
        api_key=key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=entry.get("timeout", 90),
        max_retries=0,   # the graph owns retry/failover; see agents/nodes.py
    )
