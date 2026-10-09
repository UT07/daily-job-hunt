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
    """Pick n providers, preferring distinct model families.

    `fill_same_family=True` is set HERE and nowhere else. When the pool has
    fewer live families than generators requested — routine on a fast batch,
    where account-wide 429 cooldowns collapse it to Gemini alone — the strict
    rule returned ONE provider, and one candidate means nothing to adjudicate.
    Measured 2026-10-07: 415 single_candidate against 129 adjudicated.

    Two models from one family is weaker than two families and far stronger
    than no comparison. `select_critics` deliberately does NOT pass it: a
    critic from the family that generated is not an independent reviewer.
    """
    return _select_diverse_providers(_build_provider_list(), n=n,
                                     fill_same_family=True)


def select_critics(exclude_families: set[str], n: int = 1) -> list[dict]:
    """Up to n critics, each from a family that did not generate.

    More than one because a single critic is a single point of failure, and in
    production it failed most of the time. Measured 2026-09-30 across one batch:
    every adjudication that did not happen came from the critic slot, not from
    generation -- `critic nvidia/nemotron-3-super-120b-a12b returned nothing`,
    `nvidia/nemotron-3-ultra-550b-a55b returned nothing`, and one
    `critic_unparseable` whose raw answer was the model thinking aloud ("We need
    to evaluate two candidates based on criteria..."). The council gave up on
    the first failure and shipped candidate 1 unreviewed.

    Families, not models: a second NVIDIA entry would fail the same way. The
    fallback to "any provider" when every family has generated is unchanged.
    """
    all_providers = _build_provider_list()
    picked = _select_diverse_providers(all_providers, n=n, exclude_families=exclude_families)
    if not picked:
        picked = _select_diverse_providers(all_providers, n=n)
    return picked


def select_critic(exclude_families: set[str]) -> dict | None:
    """The first available critic, or None. Kept for callers wanting just one."""
    picked = select_critics(exclude_families, n=1)
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
