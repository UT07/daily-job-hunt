"""Council graph nodes.

Each node takes a state dict and returns a partial-state update. Nodes never
import ai_helper directly — agents.providers is the only seam.
"""
import logging

from agents.providers import call_one, family_of, select_critic, select_generators
from lambdas.pipeline.ai_helper import _parse_critic_scores

logger = logging.getLogger()

# Reasoning models emit chain-of-thought before content; too small a budget
# yields empty content that is indistinguishable from a dead provider.
CRITIC_MAX_TOKENS = 1024

CRITIQUE_SYSTEM = "You are an impartial AI output evaluator. Return only valid JSON."


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
    result = call_one(
        payload["provider"],
        payload["prompt"],
        payload.get("system", ""),
        payload.get("temperature", 0.3),
        max_tokens=4096,
    )
    # An empty list keeps the reducer total — a failed branch contributes
    # nothing rather than a None that would break concatenation.
    return {"candidates": [result] if result else []}


def _build_critique_prompt(candidates: list[dict], task_description: str) -> str:
    blocks = [
        f"--- CANDIDATE {i} ({c['provider']}:{c['model']}) ---\n{c['content'][:3000]}"
        for i, c in enumerate(candidates, 1)
    ]
    return (
        f"You are evaluating {len(candidates)} candidate outputs for this task:\n"
        f"{task_description}\n\n"
        "Rate each candidate 0-100 on:\n"
        "1. ACCURACY: Does it follow ALL instructions? No banned phrases, no fabrication?\n"
        "2. COMPLETENESS: Are all required sections/structure present?\n"
        "3. QUALITY: Active voice, specific metrics, no filler, proper formatting (\\textbf preserved)?\n"
        "4. ADHERENCE: Does it match the specific job description, not generic?\n\n"
        "Average the four dimensions into a single score per candidate.\n\n"
        + "\n\n".join(blocks)
        + "\n\nReturn ONLY a JSON array of integer scores in candidate order, e.g. [85, 72]. No other text."
    )


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
        _build_critique_prompt(candidates, state.get("task_description", "")),
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
