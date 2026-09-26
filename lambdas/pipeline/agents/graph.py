"""Council graph assembly.

    START -> guard_input -> plan -> generate (fan-out) -> critique -> guard_output -> gate -> finalize -> END
                                        ^                                                       |
                                        +----------------------- repair <----------------------+

Generators run concurrently via the Send API. The legacy implementation looped
over them serially; this is the measurable win that makes the port more than a
relabelling.

guard_input sits before `plan` and is never re-entered by the repair loop
(repair routes back to `plan`, not to `guard_input`) -- input validation is a
one-shot check on the caller's prompt, not something a repair round should
redo. guard_output sits after `critique` and feeds `quality_gate`, which is
what actually arms the repair loop: before this node was wired in, nothing
ever populated `guard_report`, so `quality_gate` always saw `None` and always
returned "finalize". See guard_output_node's docstring for the check_output
adapter it performs, and quality_gate's for the two-attempt repair bound.
"""
import logging
import os
import uuid
from collections import OrderedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents.nodes import (
    critique_node,
    finalize_node,
    generate_node,
    guard_input_node,
    guard_output_node,
    plan_node,
    quality_gate,
    repair_node,
)
from agents.state import CouncilState

logger = logging.getLogger()


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


# ---------------------------------------------------------------------------
# Checkpointer
# ---------------------------------------------------------------------------
#
# build_council_graph() used to compile with a bare `MemorySaver()` at module
# scope and nothing ever evicted it. Every council run (council_complete_
# langgraph, below) mints a fresh uuid4 and uses it as the graph's thread_id;
# Lambda invocations are ephemeral, so nothing ever comes back to resume or
# re-read an old thread. MemorySaver never forgets anything on its own, so a
# warm container that has handled N invocations is holding N never-revisited
# threads' worth of checkpoint state for as long as it stays warm -- an
# unbounded, monotonic leak. Two independent audits flagged this as live in
# production.
#
# The fix actually shipped by this task is BoundedMemorySaver, not a
# Postgres-backed saver. `_db_url()` / `_postgres_saver()` are kept as the
# real upgrade seam -- get_checkpointer() already routes to them the moment a
# URL is configured -- but there is currently no `/naukribaba/SUPABASE_DB_URL`
# SSM parameter: the app talks to Supabase over PostgREST
# (SUPABASE_URL + SUPABASE_SERVICE_KEY) everywhere else in this codebase,
# never raw Postgres. Verified against the live parameter store while
# building this (`aws ssm describe-parameters`): only SUPABASE_URL and
# SUPABASE_SERVICE_KEY exist under /naukribaba/. Building the Postgres path
# against a connection string the project does not have would be untestable
# and was explicitly ruled out for this task -- see
# .superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-9-10-report.md
# for the full writeup, the measured layer-budget cost of the dependency,
# and what provisioning a direct connection would take.


class BoundedMemorySaver(MemorySaver):
    """In-memory checkpointer that evicts the oldest thread once a cap is hit.

    Every `put()` (LangGraph calls this once per checkpointed node
    transition) is delegated to the real `MemorySaver.put()` unchanged, then
    the thread it belongs to is marked most-recently-used. Once the number of
    DISTINCT threads being tracked exceeds `max_threads`, the single oldest
    thread is evicted via the base class's own `delete_thread()` -- a public
    method `InMemorySaver` ships for exactly this (it clears that thread's
    entries out of `storage`, `writes` and `blobs`), not a private internal
    this class reaches around. `aput` is not overridden separately:
    `InMemorySaver.aput` already just calls `self.put(...)`, which resolves
    to this override through normal method resolution.

    This is a leak fix, not durable checkpointing: state still does not
    survive a cold start, and a run older than `max_threads` invocations ago
    on the same warm container can no longer be resumed. Nothing in this
    codebase resumes a thread after its originating `.invoke()` call returns
    (Lambda invocations are ephemeral and council_complete_langgraph never
    revisits its own trace_id), so bounding retention trades away a
    capability nothing was actually using for a hard ceiling on growth.
    """

    def __init__(self, max_threads: int = 200, **kwargs):
        super().__init__(**kwargs)
        self.max_threads = max_threads
        self._thread_order: OrderedDict[str, None] = OrderedDict()

    def put(self, config, checkpoint, metadata, new_versions):
        result = super().put(config, checkpoint, metadata, new_versions)
        thread_id = config["configurable"]["thread_id"]
        # Move to the most-recently-used end (or insert new). Dict-keyed on
        # thread_id, so the several put() calls one council run makes to the
        # SAME thread_id (one per node transition) count once, not N times.
        self._thread_order.pop(thread_id, None)
        self._thread_order[thread_id] = None
        while len(self._thread_order) > self.max_threads:
            oldest, _ = self._thread_order.popitem(last=False)
            self.delete_thread(oldest)
        return result


def _max_checkpoint_threads() -> int:
    """Cap on distinct threads BoundedMemorySaver retains at once.

    Tunable via env var so ops can adjust without a redeploy. 200 is a
    judgment call -- "enough to be useful if ever inspected mid-container-
    life, small enough that its checkpoint payloads (a prompt plus a handful
    of candidate strings each) stay a rounding error against Lambda's memory
    budget" -- not a figure measured against a specific incident.
    """
    try:
        return int(os.environ.get("COUNCIL_CHECKPOINT_MAX_THREADS", "200"))
    except ValueError:
        return 200


def _db_url() -> str | None:
    """Direct Postgres connection string, or None when unavailable.

    Returns None in every environment this codebase runs in today: there is
    no `/naukribaba/SUPABASE_DB_URL` SSM parameter (confirmed against the
    live parameter store; only SUPABASE_URL and SUPABASE_SERVICE_KEY exist).
    This is a real SSM lookup, not hardcoded to return None, so the day that
    parameter is provisioned this starts returning a URL with no code change
    here -- only `_postgres_saver` below needs a dependency added.
    """
    try:
        from ai_helper import get_param  # flat import — pytest / zip Lambda
    except ImportError:
        from lambdas.pipeline.ai_helper import get_param  # container image
    try:
        return get_param("/naukribaba/SUPABASE_DB_URL")
    except Exception:
        return None


def _postgres_saver(url: str):
    """Build a Postgres-backed checkpointer.

    Not reachable today: `_db_url()` always returns None until the SSM
    parameter above exists, and `langgraph-checkpoint-postgres` (this
    function's only import) is deliberately NOT in requirements.txt yet --
    measured at ~4MB true marginal layer cost when installed alongside the
    rest of layer/requirements.txt (see task-9-10-report.md), so budget is
    not what is blocking this, only the missing connection string is. Left
    genuinely implemented rather than stubbed, so turning this on later is
    "add the dependency to requirements.txt + layer/requirements.txt and
    provision the SSM parameter", not "write this function".
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    saver = PostgresSaver.from_conn_string(url)
    saver.setup()
    return saver


def get_checkpointer():
    """Durable checkpointer when a direct Postgres connection is configured,
    a bounded in-memory saver otherwise.

    Lambda invocations are ephemeral, so neither MemorySaver nor
    BoundedMemorySaver gives resume-after-timeout; a Postgres saver is what
    would make a partially-completed council run recoverable. Until
    `_db_url()` returns something, BoundedMemorySaver is what stands between
    this graph and the unbounded-growth bug this task closes (see the
    module-level comment above).

    The try/except around `_postgres_saver` is load-bearing, not defensive
    boilerplate: a checkpointer is infrastructure plumbing, not the thing the
    caller asked for, so any failure building one (bad connection string,
    network partition, missing dependency) must never surface as a failure
    to generate. It falls back to the SAME bounded saver as the no-URL case
    -- not a bare, unbounded MemorySaver -- so a DB that is flaky rather than
    cleanly absent can't reopen the leak this task closes.
    """
    url = _db_url()
    if not url:
        return BoundedMemorySaver(max_threads=_max_checkpoint_threads())
    try:
        return _postgres_saver(url)
    except Exception:
        logger.exception(
            "[council] Postgres checkpointer unavailable — falling back to bounded in-memory saver"
        )
        return BoundedMemorySaver(max_threads=_max_checkpoint_threads())


def build_council_graph(checkpointer=None):
    builder = StateGraph(CouncilState)
    builder.add_node("guard_input", guard_input_node)
    builder.add_node("plan", plan_node)
    builder.add_node("generate", generate_node)
    builder.add_node("critique", critique_node)
    builder.add_node("guard_output", guard_output_node)
    builder.add_node("repair", repair_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "guard_input")
    # guard_input_node raises ValueError on a detected injection rather than
    # returning a state update -- LangGraph propagates that straight out of
    # .invoke(), so no edge out of a rejected guard_input is ever taken and
    # `plan` never sees a prompt that should have been refused.
    builder.add_edge("guard_input", "plan")
    builder.add_conditional_edges("plan", _fan_out, ["generate"])
    builder.add_edge("generate", "critique")
    builder.add_edge("critique", "guard_output")
    builder.add_conditional_edges(
        "guard_output", quality_gate, {"finalize": "finalize", "repair": "repair"}
    )
    # Repair loops back through plan (NOT through guard_input -- see module
    # docstring) so providers get redrawn on retry (a family that just failed
    # gets a fresh pick). That routing does NOT by itself reset candidates:
    # add_candidates merges every node's contribution to that channel
    # regardless of which node wrote it, so a plain `{"candidates": []}`
    # return -- from plan_node or repair_node -- is a no-op concatenation.
    # The actual reset happens explicitly in repair_node, which returns an
    # Overwrite to bypass the reducer and replace the channel's value
    # directly before the next round runs.
    builder.add_edge("repair", "plan")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())


def _configure_langsmith_tracing() -> None:
    """Populate LANGCHAIN_API_KEY from SSM when tracing is on but the key
    isn't already in the environment.

    LANGCHAIN_TRACING_V2 and LANGCHAIN_PROJECT are plain (non-secret) Lambda
    environment variables set directly in template.yaml -- langsmith reads
    both straight from `os.environ` wherever a traced call happens, no SDK
    call required. LANGCHAIN_API_KEY is deliberately NOT also a template.yaml
    environment variable: it is a SecureString, and CloudFormation's
    `{{resolve:ssm-secure:...}}` dynamic reference does not support Lambda
    `Environment.Variables` as a target -- confirmed with `sam validate
    --lint` while building this (cfn-lint rule E1027: "SSM secure strings
    can only be used in resource properties"; Lambda environment variables
    are not one of the supported properties, so the plan's literal template
    snippet would have failed CloudFormation validation at deploy time).

    So the key reaches this process the same way every other secret in this
    codebase already does -- get_param() at runtime, gated by the
    SSMParameterReadPolicy IAM grant the three council-using functions
    already carry -- set into the environment once per warm container, which
    is where langsmith reads it from on every traced call after that.

    Never allowed to raise: a failed SSM lookup means no tracing for this
    run, never a failure to generate.
    """
    if os.environ.get("LANGCHAIN_TRACING_V2", "").strip().lower() != "true":
        return
    if os.environ.get("LANGCHAIN_API_KEY"):
        return
    try:
        from ai_helper import get_param  # flat import — pytest / zip Lambda
    except ImportError:
        from lambdas.pipeline.ai_helper import get_param  # container image
    try:
        os.environ["LANGCHAIN_API_KEY"] = get_param("/naukribaba/LANGCHAIN_API_KEY")
    except Exception:
        logger.warning(
            "[council] LangSmith tracing requested (LANGCHAIN_TRACING_V2=true) but "
            "LANGCHAIN_API_KEY could not be fetched from SSM — continuing without tracing"
        )


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _configure_langsmith_tracing()
        _GRAPH = build_council_graph(checkpointer=get_checkpointer())
    return _GRAPH


def council_complete_langgraph(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
    task: str = "default",
    base_skills: str = "",
    base_body: str = "",
    header_markers: list[str] | None = None,
) -> dict:
    """Drop-in replacement for council_complete. Returns a Candidate dict
    plus `trace_id` -- the id used as this run's LangGraph thread_id (and,
    once LangSmith tracing is enabled via the LANGCHAIN_* env vars, the id a
    human can use to find this run's thread in the LangSmith UI). Callers
    that want to correlate a result back to its trace persist this alongside
    the result (see score_batch.py's job_record).

    `task` and the three guard-context fields flow straight into the initial
    state -- CouncilState already declares all four (see agents/state.py),
    and guard_input_node/guard_output_node already read them via
    `state.get(...)`; this function was the missing link that never set them
    from the public entry point. `header_markers or []` normalises the
    common no-caller-supplied-it case to the list type CouncilState declares,
    rather than storing `None` in a `list[str]` field.
    """
    trace_id = str(uuid.uuid4())
    final = _get_graph().invoke(
        {
            "task": task,
            "prompt": prompt,
            "system": system,
            "task_description": task_description,
            "n_generators": n_generators,
            "temperature": temperature,
            "base_skills": base_skills,
            "base_body": base_body,
            "header_markers": header_markers or [],
            "candidates": [],
            "repair_attempts": 0,
            "trace_id": trace_id,
        },
        config={"configurable": {"thread_id": trace_id}},
    )
    winner = final.get("winner")
    if not winner:
        raise RuntimeError("Council: all generators failed")
    return {**winner, "trace_id": trace_id}
