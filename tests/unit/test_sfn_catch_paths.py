"""A key a state produces must exist on EVERY path into a state that reads it.

The daily machine's ProcessMatchedJobs Map writes ResultPath "$.processed_jobs".
Its Catch wrote "$.error" and carried on to PostScoreTailoredJobs and then
SavePipelineMetrics, whose Parameters read "processed_jobs.$": "$.processed_jobs".
On that path the key was never written, and an absent path in Parameters is a
States.Runtime error — which a States.ALL Catch does NOT catch. So the one
failure the Catch existed to absorb would have failed the execution anyway,
from a different state, with an error naming the wrong cause.

This walks each top-level machine as a graph (success edges, Catch edges,
Choice branches) and computes, for every state, the set of keys GUARANTEED to
be present on every path into it (an intersection over predecessors). Only
keys some state in the machine itself writes are judged; keys from the
execution input or from a Task whose ResultPath is "$" (LoadUserConfig) are
assumed present, because nothing here can see them. That makes the check
narrow but exact for the defect class: "a key written by a state that this
path skipped, or caught out of".

Known blind spot, measured by mutation: if a ResultPath is RENAMED so that
nothing writes a key any more, its readers are assumed to get it from the
input and the walker stays green. The input is invisible here (LoadUserConfig
replaces the state with a Lambda's return value), so it cannot tell.

test_state_machine_dataflow.py is the sibling for Choice variables lost to a
Pass with Parameters; this covers Catch edges and Parameters reads.
"""
from __future__ import annotations

import pytest

from tests.unit.test_state_machine_dataflow import _definitions


def _top_key(path: str) -> str | None:
    if not isinstance(path, str) or not path.startswith("$.") or path.startswith("$$"):
        return None
    return path[2:].split(".")[0].split("[")[0]


def _reads(state: dict) -> set[str]:
    out = set()

    def walk(params):
        if isinstance(params, dict):
            for k, v in params.items():
                if k.endswith(".$"):
                    key = _top_key(v)
                    if key:
                        out.add(key)
                else:
                    walk(v)
        elif isinstance(params, list):
            for v in params:
                walk(v)

    walk(state.get("Parameters"))
    for field in ("ItemsPath", "InputPath"):
        key = _top_key(state.get(field))
        if key:
            out.add(key)
    for choice in state.get("Choices", []) or []:
        key = _top_key(choice.get("Variable"))
        if key:
            out.add(key)
    return out


def _result_key(result_path):
    """('add', key) | ('replace', None) | ('keep', None) for a ResultPath."""
    if result_path is None:
        return ("default", None)
    if result_path == "$":
        return ("replace", None)
    key = _top_key(result_path)
    return ("add", key) if key else ("keep", None)


def _edges(name: str, state: dict, keys: frozenset, produced: set[str]):
    """Yield (next_state, keys guaranteed on that edge)."""
    t = state.get("Type")
    # Catch edges carry the state's INPUT plus the error at the catch ResultPath.
    for c in state.get("Catch", []) or []:
        mode, key = _result_key(c.get("ResultPath"))
        if mode == "add":
            yield c["Next"], keys | {key}
        elif mode in ("replace", "default"):
            yield c["Next"], frozenset()
        else:
            yield c["Next"], keys
    if t == "Choice":
        for ch in state.get("Choices", []):
            yield ch["Next"], keys
        if "Default" in state:
            yield state["Default"], keys
        return
    if "Next" not in state:
        return
    mode, key = _result_key(state.get("ResultPath"))
    if mode == "add":
        out = keys | {key}
    elif mode == "replace":
        out = frozenset()
    elif mode == "default" and t == "Pass":
        shape = state.get("Parameters") or state.get("Result")
        if isinstance(shape, dict):
            out = frozenset(k[:-2] if k.endswith(".$") else k for k in shape) & produced
        else:
            out = keys
    elif mode == "default" and t in ("Task", "Map", "Parallel"):
        out = frozenset()  # the result replaces the whole state
    else:
        out = keys
    yield state["Next"], out


def _produced(states: dict) -> set[str]:
    out = set()
    for s in states.values():
        for rp in [s.get("ResultPath")] + [c.get("ResultPath") for c in s.get("Catch", []) or []]:
            mode, key = _result_key(rp)
            if mode == "add":
                out.add(key)
    return out


def missing_reads(definition: dict) -> list[str]:
    states = definition["States"]
    produced = _produced(states)
    universe = frozenset(produced)
    guaranteed: dict[str, frozenset] = {n: universe for n in states}
    guaranteed[definition["StartAt"]] = frozenset()
    reached = {definition["StartAt"]}
    changed = True
    while changed:
        changed = False
        incoming: dict[str, frozenset] = {}
        for name in reached:
            for nxt, keys in _edges(name, states[name], guaranteed[name], produced):
                incoming[nxt] = incoming[nxt] & keys if nxt in incoming else keys
        for nxt, keys in incoming.items():
            if nxt == definition["StartAt"]:
                keys = frozenset()
            if nxt not in reached or keys != guaranteed[nxt]:
                reached.add(nxt)
                guaranteed[nxt] = keys
                changed = True
    problems = []
    for name in sorted(reached):
        absent = (_reads(states[name]) & produced) - guaranteed[name]
        for key in sorted(absent):
            problems.append(f"{name} reads $.{key}, which some path into it never writes")
    return problems


def _daily():
    for d in _definitions():
        if "ProcessMatchedJobs" in d["States"]:
            return d
    pytest.fail("daily pipeline definition not found in template.yaml")


def test_the_walker_finds_machines():
    defs = _definitions()
    assert len(defs) >= 2, f"expected the daily and single-job machines, found {len(defs)}"


def test_the_walker_detects_the_original_defect():
    """The pre-fix shape: Catch writes $.error and continues to a reader."""
    broken = {
        "StartAt": "Load",
        "States": {
            "Load": {"Type": "Task", "Resource": "x", "ResultPath": "$", "Next": "Process"},
            "Process": {"Type": "Map", "ItemsPath": "$.items", "ResultPath": "$.processed_jobs",
                        "Next": "Metrics",
                        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "Metrics"}]},
            "Metrics": {"Type": "Task", "Resource": "x",
                        "Parameters": {"processed_jobs.$": "$.processed_jobs"}, "End": True},
        },
    }
    assert missing_reads(broken) == ["Metrics reads $.processed_jobs, which some path into it never writes"]


def test_the_walker_accepts_a_default_on_the_catch_path():
    fixed = {
        "StartAt": "Process",
        "States": {
            "Process": {"Type": "Map", "ResultPath": "$.processed_jobs", "Next": "Metrics",
                        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.processed_jobs", "Next": "Metrics"}]},
            "Metrics": {"Type": "Task", "Resource": "x",
                        "Parameters": {"processed_jobs.$": "$.processed_jobs"}, "End": True},
        },
    }
    assert missing_reads(fixed) == []


@pytest.mark.parametrize("which", ["daily", "single"])
def test_every_produced_key_read_after_a_catch_is_guaranteed(which):
    defs = _definitions()
    if which == "daily":
        definition = _daily()
    else:
        definition = next(d for d in defs if "ProcessMatchedJobs" not in d["States"])
    assert not missing_reads(definition), "\n".join(missing_reads(definition))


def test_one_job_cannot_fail_the_whole_processing_map():
    """Every Task in the per-job processor has a Catch.

    SaveJobAfterError had none. If it raised, the iteration failed, the Map
    failed for every job in it, and the run lost the record of all of them.
    """
    proc = _daily()["States"]["ProcessMatchedJobs"]["ItemProcessor"]["States"]
    uncaught = [n for n, s in proc.items() if s.get("Type") == "Task" and not s.get("Catch")]
    assert not uncaught, f"Tasks without a Catch in ProcessMatchedJobs: {uncaught}"


def test_a_failed_save_is_counted_as_a_failure():
    """The terminal Pass after a failed save must read as a failure to save_metrics.

    save_metrics._count_compiled_artifacts counts an entry as a resume only if
    has_resume is truthy and failed is not; anything else is resume_failures.
    """
    proc = _daily()["States"]["ProcessMatchedJobs"]["ItemProcessor"]["States"]
    nxt = proc["SaveJobAfterError"]["Catch"][0]["Next"]
    terminal = proc[nxt]
    assert terminal["Type"] == "Pass" and terminal.get("End") is True
    result = terminal.get("Result") or {}
    assert result.get("failed") is True and not result.get("has_resume")
    # It must not dereference anything: a Pass that names a field makes it
    # mandatory for every item (sfn_pass_state_contract).
    assert "Parameters" not in terminal


def test_the_processing_map_failing_fails_the_execution():
    """A Map-level failure is not absorbed into a run that reports success."""
    states = _daily()["States"]
    catches = states["ProcessMatchedJobs"].get("Catch", [])
    assert catches and catches[0]["Next"] == "NotifyError"
    assert states["NotifyError"]["Next"] == "PipelineFailedAfterError"
    assert states["PipelineFailedAfterError"]["Type"] == "Fail"
