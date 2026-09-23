"""Adapter between graph nodes and the existing provider pool.

Nodes import only this module. Everything AWS-touching or network-touching
stays behind these four functions, so node tests need no credentials.
"""
from agents._ai_helper import (
    _build_provider_list,
    _call_provider,
    _model_family,
    _select_diverse_providers,
)
from agents.state import Candidate


def family_of(provider: dict) -> str:
    return _model_family(provider["model"])


def all_providers() -> list[dict]:
    """Full provider pool config.

    Exposed so nodes can do per-branch generator fallback (retrying a failed
    provider against the rest of the pool) without importing
    `_build_provider_list` directly — agents.providers stays the only seam
    onto ai_helper's provider config.
    """
    return _build_provider_list()


def select_generators(n: int) -> list[dict]:
    """Pick n providers from distinct model families."""
    return _select_diverse_providers(_build_provider_list(), n=n)


def select_critic(exclude_families: set[str]) -> dict | None:
    """Pick one provider from a family that did not generate.

    Falls back to any provider when every family already generated, which
    matches the behaviour of the legacy council.
    """
    all_providers = _build_provider_list()
    picked = _select_diverse_providers(all_providers, n=1, exclude_families=exclude_families)
    if not picked:
        picked = _select_diverse_providers(all_providers, n=1)
    return picked[0] if picked else None


def call_one(
    provider: dict,
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> Candidate | None:
    """Single provider call. Returns None on any failure."""
    return _call_provider(provider, prompt, system, temperature, max_tokens)
