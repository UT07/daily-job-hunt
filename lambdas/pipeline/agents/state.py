"""State carried through the council graph.

Candidate deliberately mirrors the dict shape returned by
agents._ai_helper._call_provider (the ai_helper shim's re-export) so
generate nodes can append provider results without translating them.
"""
from typing import Annotated, Any, TypedDict


class Candidate(TypedDict, total=False):
    content: str
    provider: str
    model: str
    # Set by _call_provider from the response's finish_reason. `truncated`
    # True means the provider stopped at max_tokens mid-answer, so `content`
    # is a fragment. total=False because tests and older checkpoints build
    # Candidate dicts with the first three keys only.
    finish_reason: str
    truncated: bool


def add_candidates(left: list[Candidate], right: list[Candidate]) -> list[Candidate]:
    """Reducer merging parallel generate branches into one candidate list."""
    return list(left) + list(right)


class CouncilState(TypedDict, total=False):
    # Inputs
    task: str                 # "score" | "tailor" | "cover_letter"
    prompt: str
    system: str
    task_description: str
    temperature: float
    n_generators: int
    max_tokens: int           # per-generator output budget; see ai_helper.rewrite_budget

    # Guard context, supplied by the caller for tailoring tasks
    base_skills: str
    base_body: str
    header_markers: list[str]

    # Accumulated across the graph. Every key a node reads MUST be declared
    # here — StateGraph silently drops anything outside the schema, which
    # surfaces as a KeyError in a downstream node rather than at the write.
    generators: list[dict]
    candidates: Annotated[list[Candidate], add_candidates]
    scores: list[int]
    winner: Candidate | None
    # WHY this winner was chosen. Until 2026-09-30 the four paths through
    # critique_node that give up and take candidates[0] were indistinguishable
    # from a real adjudication: the return shape differed only by scores being
    # empty, and one path logged nothing at all. Measured over 80 rounds, the
    # critic produced a usable verdict in 21 — so 74% of "council decisions"
    # were the first candidate, unreviewed, and no caller could tell.
    # See CRITIQUE_OUTCOMES for the values.
    critique_outcome: str
    guard_report: dict[str, Any] | None
    repair_attempts: int
    trace_id: str
