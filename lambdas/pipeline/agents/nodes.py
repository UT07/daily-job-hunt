"""Council graph nodes.

Each node takes a state dict and returns a partial-state update. Nodes never
import ai_helper directly — agents.providers and agents._ai_helper (the
flat/test import shim) are the only seams.
"""
import logging
import time

from langgraph.types import Overwrite

from agents._ai_helper import (
    critic_budget,
    CRITIQUE_SYSTEM,
    _parse_critic_scores,
    build_critique_prompt,
    prefer_complete,
)
from agents.providers import (
    all_providers,
    call_one,
    family_of,
    select_critic,  # noqa: F401 -- still exported for callers wanting one
    select_critics,
    select_generators,
)
from guardrails.input_guards import INSTRUCTION_HIERARCHY, check_input, fence, maybe_scrub_pii
from guardrails.output_guards import check_output

logger = logging.getLogger()


def _tid(state: dict) -> str:
    """This run's trace_id, for the log line, or "-" when there isn't one.

    Every `[council]` line carries it so a CloudWatch line can be matched to
    the LangSmith run (and the checkpointer thread) it came from --
    council_complete_langgraph logs the same id once per run and returns it to
    its caller, which persists it beside the result. "-" rather than a blank
    keeps the field positionally readable when a caller invokes a node
    directly, as the tests do.
    """
    return state.get("trace_id") or "-"


# CRITIC_MAX_TOKENS, CRITIQUE_SYSTEM and build_critique_prompt are re-exported
# (not just used) from this import — the legacy sequential council in
# ai_helper.py owns the one copy of the scoring rubric, so both engines can't
# silently drift while they run side by side during migration. Keep them
# imported by name (not `ai_helper.X`) so `nodes.CRITIC_MAX_TOKENS` etc. and
# existing patch targets keep resolving.


def guard_input_node(state: dict) -> dict:
    """Reject injected input, scrub PII, then fence and declare the hierarchy.

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

    PII scrubbing happens AFTER the injection check passes but BEFORE
    fencing, gated by the same per-task policy (`maybe_scrub_pii` reads
    `pii_scrub` off `policy_for(task)`) -- a job description that trips the
    injection guard is rejected outright, never scrubbed and forwarded.
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
    prompt = maybe_scrub_pii(prompt, task)
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
    logger.info(
        "[council] trace=%s Generators: %s", _tid(state), [g["name"] for g in generators]
    )
    return {"generators": generators, "candidates": []}


def generate_node(payload: dict) -> dict:
    """One generator branch. Dispatched once per provider via Send."""
    provider = payload["provider"]
    prompt = payload["prompt"]
    system = payload.get("system", "")
    temperature = payload.get("temperature", 0.3)
    # Sized by the caller (ai_helper.rewrite_budget) for tasks whose answer is
    # a whole document; 4096 is the historical default for everything else.
    max_tokens = payload.get("max_tokens", 4096)

    result = call_one(provider, prompt, system, temperature, max_tokens=max_tokens)
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
            result = call_one(fallback, prompt, system, temperature, max_tokens=max_tokens)
            if result:
                logger.info(
                    "[council] trace=%s Generator fallback: %s succeeded",
                    _tid(payload),
                    fallback["name"],
                )
                break
    # An empty list keeps the reducer total — a failed branch contributes
    # nothing rather than a None that would break concatenation.
    return {"candidates": [result] if result else []}


# Every way critique_node can end. Four of these five mean the winner was NOT
# adjudicated — it is simply candidates[0]. They were previously
# indistinguishable from a real verdict by anything downstream.
CRITIQUE_OUTCOMES = (
    "adjudicated",        # a critic scored the candidates and one won
    "single_candidate",   # only one survived generation; nothing to compare
    "no_critic_family",   # no model family left unused by the generators
    "critic_call_failed",  # the critic provider returned nothing
    "critic_unparseable",  # the critic answered in a shape we cannot read
)


def prefer_guard_clean(candidates: list[dict], state: dict) -> list[dict]:
    """Drop candidates the output guards block, unless that leaves nothing.

    The same argument `prefer_complete` makes about truncation, one level up.
    Its docstring: "a fragment win on style points -- the critic scores prose
    quality and has no idea the answer stops mid-section". A candidate missing
    its Experience section reads perfectly well too.

    This exists because making adjudication WORK exposed it. With one critic
    that almost always failed, the winner was candidate 1 and the guards saw
    whatever that happened to be. Retrying across three critics made real
    verdicts common, and the AI Eval Gate measured the consequence twice,
    reproducibly: guard_pass_rate 0.92 -> 0.88. The critic was doing its job --
    picking the best-written candidate -- and best-written is not the same as
    structurally sound.

    So the critic now chooses among documents that already pass. Fail-open for
    the same reason prefer_complete does: when EVERY candidate is blocked, the
    list is returned unchanged and the caller's own gate decides whether to
    ship it. Skipped below two candidates because there is nothing to choose
    between, and check_output is not free.
    """
    if len(candidates) < 2:
        return candidates
    clean = [
        c for c in candidates
        if check_output(
            c.get("content", ""),
            state.get("task", "default"),
            base_skills_text=state.get("base_skills", ""),
            base_body=state.get("base_body", ""),
            header_markers=state.get("header_markers"),
        ).passed
    ]
    if clean and len(clean) != len(candidates):
        logger.warning(
            "[council] Discarded %d guard-failing candidate(s) of %d before critique",
            len(candidates) - len(clean), len(candidates),
        )
    return clean or candidates


# How many critics to try before giving up. Three, not one, because the single
# critic was a single point of failure: measured 2026-09-30, every missed
# adjudication in a real batch came from this slot, never from generation.
CRITIC_ATTEMPTS = 3

# ...and a wall-clock ceiling across all of them, because a count is not a
# bound on the thing that actually breaks. Measured 2026-10-01: the AI Eval
# Gate job ran 20m16s against `timeout-minutes: 20` and was CANCELLED before it
# could write evals/report.json, so the gate reported a failure that was never
# about quality. Its log shows the mechanism --
# `nvidia/nemotron-3-ultra-550b-a55b failed: The read operation timed out` --
# a critic that fails by timing out costs its full provider timeout, and three
# of those per case is a different order of latency from one.
#
# The two failure modes have very different costs, and this budget separates
# them for free. An unparseable answer comes back FAST, because the model did
# reply; a dead provider costs ~60s. So a fast failure leaves room to retry and
# a slow one does not, which is the behaviour worth having rather than a flat
# attempt count. 90s allows the second attempt after one slow failure, or all
# three after fast ones.
CRITIC_TIME_BUDGET_S = 90.0


def critique_node(state: dict) -> dict:
    """Score candidates with a critic from an unused model family."""
    candidates = state.get("candidates") or []
    if not candidates:
        raise RuntimeError("Council: all generators failed")
    # A candidate the provider cut off is a partial document. It must not be
    # allowed to out-score a complete one on prose quality, which is all the
    # critic can see. Same rule the legacy council applies.
    candidates = prefer_complete(candidates)
    candidates = prefer_guard_clean(candidates, state)
    if len(candidates) == 1:
        logger.info("[council] outcome=single_candidate — only 1 candidate, "
                    "skipping critique")
        return {"winner": candidates[0], "scores": [],
                "critique_outcome": "single_candidate"}

    used = {family_of({"model": c["model"]}) for c in candidates}
    critics = select_critics(used, n=CRITIC_ATTEMPTS)
    if not critics:
        # This branch logged NOTHING. It is the one degradation that left no
        # trace at all, in a system whose only view of the council was its log
        # lines.
        logger.warning(
            "[council] outcome=no_critic_family — every family is already a "
            "generator (%s), so no cross-family critic is available; "
            "returning candidate 1 unadjudicated",
            ", ".join(sorted(f for f in used if f)),
        )
        return {"winner": candidates[0], "scores": [],
                "critique_outcome": "no_critic_family"}

    prompt = build_critique_prompt(candidates, state.get("task_description", ""))
    failures: list[str] = []
    saw_unparseable = False

    deadline = time.monotonic() + CRITIC_TIME_BUDGET_S
    for attempt, critic in enumerate(critics, 1):
        # Never on the first attempt: one critic is the behaviour this function
        # had before retries existed, and a budget that can skip it would make
        # a slow machine worse than no feature at all.
        if attempt > 1 and time.monotonic() >= deadline:
            failures.append(f"budget of {CRITIC_TIME_BUDGET_S:.0f}s exhausted "
                            f"after {attempt - 1} attempt(s)")
            logger.warning(
                "[council] critic budget exhausted after %d of %d attempt(s); "
                "not trying the rest", attempt - 1, len(critics),
            )
            break
        name = critic.get("name", "?")
        verdict = call_one(
            critic, prompt, CRITIQUE_SYSTEM, temperature=0,
            # Sized for THIS critic. A reasoning model spends the budget
            # thinking before it emits, and _call_provider treats empty content
            # as failure — which is why 57% of council rounds returned
            # critic_call_failed.
            max_tokens=critic_budget(critic),
        )
        if not verdict:
            failures.append(f"{name}: returned nothing")
            continue

        scores = _parse_critic_scores(verdict["content"], len(candidates))
        if not scores:
            saw_unparseable = True
            # Log the raw answer, truncated. "Unparseable" without the text is
            # not something anyone can act on, and in production it was the
            # model narrating its plan instead of emitting the JSON.
            failures.append(f"{name}: unreadable shape")
            logger.warning(
                "[council] critic %s (attempt %d of %d) answered in an "
                "unreadable shape. Raw: %r",
                name, attempt, len(critics), (verdict.get("content") or "")[:300],
            )
            continue

        best = max(range(len(scores)), key=lambda i: scores[i])
        logger.info("[council] outcome=adjudicated scores=%s winner=candidate %d "
                    "critic=%s (attempt %d of %d)",
                    scores, best + 1, name, attempt, len(critics))
        return {"winner": candidates[best], "scores": scores,
                "critique_outcome": "adjudicated"}

    # Every critic failed. The outcome vocabulary is unchanged because it is
    # recorded in jobs.critique_outcome and older rows must stay comparable —
    # but the log now says how many were tried and how each one failed. An
    # outcome that cannot distinguish "one critic was unlucky" from "every
    # family refused" is the shape CLAUDE.md rule 2 warns about.
    outcome = "critic_unparseable" if saw_unparseable else "critic_call_failed"
    logger.warning(
        "[council] outcome=%s — all %d critic(s) failed (%s); returning "
        "candidate 1 unadjudicated",
        outcome, len(critics), "; ".join(failures),
    )
    return {"winner": candidates[0], "scores": [], "critique_outcome": outcome}


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
        logger.warning(
            "[council] trace=%s Repair budget exhausted — finalizing best-effort",
            _tid(state),
        )
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
