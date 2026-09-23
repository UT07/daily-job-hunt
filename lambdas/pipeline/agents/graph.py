"""Council graph assembly.

    START -> plan -> generate (fan-out) -> critique -> gate -> finalize -> END
                ^                                        |
                +------------------ repair <-------------+

Generators run concurrently via the Send API. The legacy implementation looped
over them serially; this is the measurable win that makes the port more than a
relabelling.
"""
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents.nodes import (
    critique_node,
    finalize_node,
    generate_node,
    plan_node,
    quality_gate,
    repair_node,
)
from agents.state import CouncilState


def _fan_out(state: dict) -> list[Send]:
    """Dispatch one generate branch per selected provider."""
    return [
        Send("generate", {
            "provider": provider,
            "prompt": state["prompt"],
            "system": state.get("system", ""),
            "temperature": state.get("temperature", 0.3),
        })
        for provider in state["generators"]
    ]


def build_council_graph(checkpointer=None):
    builder = StateGraph(CouncilState)
    builder.add_node("plan", plan_node)
    builder.add_node("generate", generate_node)
    builder.add_node("critique", critique_node)
    builder.add_node("repair", repair_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", _fan_out, ["generate"])
    builder.add_edge("generate", "critique")
    builder.add_conditional_edges(
        "critique", quality_gate, {"finalize": "finalize", "repair": "repair"}
    )
    # Repair loops back through plan so providers get redrawn on retry (a
    # family that just failed gets a fresh pick). That routing does NOT by
    # itself reset candidates: add_candidates merges every node's
    # contribution to that channel regardless of which node wrote it, so a
    # plain `{"candidates": []}` return -- from plan_node or repair_node --
    # is a no-op concatenation. The actual reset happens explicitly in
    # repair_node, which returns an Overwrite to bypass the reducer and
    # replace the channel's value directly before the next round runs.
    builder.add_edge("repair", "plan")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_council_graph()
    return _GRAPH


def council_complete_langgraph(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
) -> dict:
    """Drop-in replacement for council_complete. Returns a Candidate dict."""
    trace_id = str(uuid.uuid4())
    final = _get_graph().invoke(
        {
            "prompt": prompt,
            "system": system,
            "task_description": task_description,
            "n_generators": n_generators,
            "temperature": temperature,
            "candidates": [],
            "repair_attempts": 0,
            "trace_id": trace_id,
        },
        config={"configurable": {"thread_id": trace_id}},
    )
    winner = final.get("winner")
    if not winner:
        raise RuntimeError("Council: all generators failed")
    return winner
