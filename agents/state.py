"""State carried through the council graph.

Candidate deliberately mirrors the dict shape returned by
lambdas.pipeline.ai_helper._call_provider so generate nodes can append
provider results without translating them.
"""
from typing import Annotated, Any, TypedDict


class Candidate(TypedDict):
    content: str
    provider: str
    model: str


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
    guard_report: dict[str, Any] | None
    repair_attempts: int
    trace_id: str
