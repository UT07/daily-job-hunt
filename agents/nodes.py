"""Council graph nodes.

Each node takes a state dict and returns a partial-state update. Nodes never
import ai_helper directly — agents.providers is the only seam.
"""
import logging

from agents.providers import all_providers, call_one, family_of, select_critic, select_generators
from lambdas.pipeline.ai_helper import (
    CRITIC_MAX_TOKENS,
    CRITIQUE_SYSTEM,
    _parse_critic_scores,
    build_critique_prompt,
)

logger = logging.getLogger()

# CRITIC_MAX_TOKENS, CRITIQUE_SYSTEM and build_critique_prompt are re-exported
# (not just used) from this import — the legacy sequential council in
# ai_helper.py owns the one copy of the scoring rubric, so both engines can't
# silently drift while they run side by side during migration. Keep them
# imported by name (not `ai_helper.X`) so `nodes.CRITIC_MAX_TOKENS` etc. and
# existing patch targets keep resolving.


def plan_node(state: dict) -> dict:
    """Select generators from distinct model families."""
    n = state.get("n_generators", 2)
    generators = select_generators(n)
    if not generators:
        raise RuntimeError("Council: no providers available")
    logger.info("[council] Generators: %s", [g["name"] for g in generators])
    return {"generators": generators, "candidates": []}


def generate_node(payload: dict) -> dict:
    """One generator branch. Dispatched once per provider via Send."""
    provider = payload["provider"]
    prompt = payload["prompt"]
    system = payload.get("system", "")
    temperature = payload.get("temperature", 0.3)

    result = call_one(provider, prompt, system, temperature, max_tokens=4096)
    if not result:
        # Primary provider failed — retry through the rest of the pool before
        # giving up on this slot, same as legacy ai_helper.council_complete's
        # "try others from different families" fallback.
        #
        # Deliberate difference from legacy: council_complete excludes
        # families already used by OTHER generators, accumulated in a
        # `used_families` set across its serial loop. This node is one
        # branch of a parallel `Send` fan-out and cannot see its sibling
        # branches' state, so it can only exclude its OWN assigned family.
        # Two branches may therefore both fall back onto the same family and
        # produce duplicate candidates — that's an accepted tradeoff of the
        # parallel port, not a bug: the critic still scores both, and a
        # duplicate candidate is far cheaper than silently dropping a
        # generator slot. Do not "fix" this by trying to coordinate
        # exclusion across branches.
        own_family = family_of(provider)
        for fallback in all_providers():
            if family_of(fallback) == own_family:
                continue
            result = call_one(fallback, prompt, system, temperature, max_tokens=4096)
            if result:
                logger.info("[council] Generator fallback: %s succeeded", fallback["name"])
                break
    # An empty list keeps the reducer total — a failed branch contributes
    # nothing rather than a None that would break concatenation.
    return {"candidates": [result] if result else []}


def critique_node(state: dict) -> dict:
    """Score candidates with a critic from an unused model family."""
    candidates = state.get("candidates") or []
    if not candidates:
        raise RuntimeError("Council: all generators failed")
    if len(candidates) == 1:
        logger.info("[council] Only 1 candidate — skipping critique")
        return {"winner": candidates[0], "scores": []}

    used = {family_of({"model": c["model"]}) for c in candidates}
    critic = select_critic(used)
    if critic is None:
        return {"winner": candidates[0], "scores": []}

    verdict = call_one(
        critic,
        build_critique_prompt(candidates, state.get("task_description", "")),
        CRITIQUE_SYSTEM,
        temperature=0,
        max_tokens=CRITIC_MAX_TOKENS,
    )
    if not verdict:
        logger.warning("[council] Critic call failed — returning first candidate")
        return {"winner": candidates[0], "scores": []}

    scores = _parse_critic_scores(verdict["content"], len(candidates))
    if not scores:
        logger.warning("[council] Unparseable critic output — returning first candidate")
        return {"winner": candidates[0], "scores": []}

    best = max(range(len(scores)), key=lambda i: scores[i])
    logger.info("[council] Scores: %s, winner: candidate %d", scores, best + 1)
    return {"winner": candidates[best], "scores": scores}


def quality_gate(state: dict) -> str:
    """Route to repair or finalize. Bounded at two repair attempts."""
    report = state.get("guard_report") or {"passed": True}
    if report.get("passed", True):
        return "finalize"
    if state.get("repair_attempts", 0) >= 2:
        logger.warning("[council] Repair budget exhausted — finalizing best-effort")
        return "finalize"
    return "repair"


def repair_node(state: dict) -> dict:
    """Reflexion step: fold critic/guard violations back into the prompt."""
    violations = (state.get("guard_report") or {}).get("violations", [])
    feedback = "\n".join(f"- {v}" for v in violations)
    repaired = (
        f"{state['prompt']}\n\n"
        "IMPORTANT — your previous attempt was rejected for these reasons:\n"
        f"{feedback}\n"
        "Produce a corrected version that fixes every point above."
    )
    return {
        "prompt": repaired,
        "repair_attempts": state.get("repair_attempts", 0) + 1,
        # Discard the rejected batch so the reducer starts clean.
        "candidates": [],
    }


def finalize_node(state: dict) -> dict:
    """Terminal node. Winner is already chosen; this exists as a join point."""
    return {"winner": state.get("winner")}
