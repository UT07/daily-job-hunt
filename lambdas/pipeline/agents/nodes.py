"""Council graph nodes.

Each node takes a state dict and returns a partial-state update. Nodes never
import ai_helper directly — agents.providers and agents._ai_helper (the
flat/test import shim) are the only seams.
"""
import logging

from langgraph.types import Overwrite

from agents._ai_helper import (
    CRITIC_MAX_TOKENS,
    CRITIQUE_SYSTEM,
    _parse_critic_scores,
    build_critique_prompt,
)
from agents.providers import all_providers, call_one, family_of, select_critic, select_generators
from guardrails.input_guards import INSTRUCTION_HIERARCHY, check_input, fence
from guardrails.output_guards import check_output

logger = logging.getLogger()

# CRITIC_MAX_TOKENS, CRITIQUE_SYSTEM and build_critique_prompt are re-exported
# (not just used) from this import — the legacy sequential council in
# ai_helper.py owns the one copy of the scoring rubric, so both engines can't
# silently drift while they run side by side during migration. Keep them
# imported by name (not `ai_helper.X`) so `nodes.CRITIC_MAX_TOKENS` etc. and
# existing patch targets keep resolving.


def guard_input_node(state: dict) -> dict:
    """Reject injected input, then fence and declare the hierarchy.

    Raising rather than repairing is deliberate: an injection attempt is not
    a quality problem to iterate on, it is input to refuse. Letting it reach
    `repair_node` would hand an attacker a free reflexion loop to refine
    their payload against our own guard feedback.

    The exception message is the only artifact an on-call engineer sees in
    CloudWatch for a rejected job, so it names the task (which policy fired)
    and a bounded preview of the offending prompt (which text tripped it) --
    enough to tell a real attack from a false positive without correlating
    back to the original job record by hand. The input guards measured
    0 false positives across 3,842 real job descriptions (Task 20), which is
    the justification for failing the whole call rather than softening this
    to a warning.
    """
    task = state.get("task", "default")
    prompt = state["prompt"]
    result = check_input(prompt, task)
    if not result.passed:
        preview = prompt[:300].replace("\n", " ")
        raise ValueError(
            f"Input guard rejected the prompt for task={task!r}: "
            f"{result.to_dict()['violations']} | prompt_preview={preview!r}"
        )
    return {
        "prompt": fence(prompt),
        "system": f"{INSTRUCTION_HIERARCHY}\n\n{state.get('system', '')}".strip(),
    }


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


def guard_output_node(state: dict) -> dict:
    """Evaluate the winning candidate against the task's output policy.

    This is what arms `quality_gate`'s repair loop: before this node existed
    on the graph path, nothing ever wrote `guard_report`, so `quality_gate`
    always saw `None` (-> treated as passed) and always finalized on the
    first pass. Recomputing it fresh here, every round, from the ACTUAL
    winner content is also what makes the repair loop meaningful rather than
    a rubber stamp -- a stale or hand-seeded report would never reflect
    whether a repaired attempt actually fixed anything.

    `check_output`'s keyword is `base_skills_text`; `CouncilState`'s field
    (matching tailor_resume's own naming for the same value) is
    `base_skills`. This node is the adapter between the two names -- get it
    wrong and the fabrication check silently never runs, which is exactly
    the "policy flag that does nothing" defect class this task exists to
    close (see guardrails/policy.py's removal of the dead `fairness_cap`
    key for the other instance of it found during this task).
    """
    winner = state.get("winner") or {}
    result = check_output(
        winner.get("content", ""),
        state.get("task", "default"),
        base_skills_text=state.get("base_skills", ""),
        base_body=state.get("base_body", ""),
        header_markers=state.get("header_markers"),
    )
    return {"guard_report": result.to_dict()}


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
        # candidates is a reducer-backed channel: add_candidates concatenates
        # EVERY node's contribution to it regardless of which node wrote it,
        # so a plain `[]` here would merge to a no-op, not a reset, and the
        # rejected batch would still be sitting there for the next round to
        # pile onto. Overwrite bypasses the reducer and replaces the
        # channel's value directly, so this is an actual discard. It is also
        # the JSON-serialisable form (see langgraph.types.Overwrite), so it
        # survives a swap from the in-memory checkpointer to one that
        # persists state (e.g. Postgres).
        "candidates": Overwrite(value=[]),
    }


def finalize_node(state: dict) -> dict:
    """Terminal node. Winner is already chosen; this exists as a join point."""
    return {"winner": state.get("winner")}
