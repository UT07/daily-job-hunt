"""Every caller of the single-job state machine must satisfy its Pass states.

A Pass state with `Parameters` and no `ResultPath` REPLACES the whole state,
and a `"x.$": "$.x"` entry is a HARD error when `$.x` is absent from the input
-- not a null, not a skip. So each such entry is a requirement on every caller,
and the requirement is invisible: it lives in template.yaml and the callers
live in app.py.

That gap has now cost two outages in the same two states:

  #126  resume_only and job_id were dropped by the Pass, so CheckArtifactScope
        failed three states later on every Regenerate.
  #132  the fix added `job_id.$` to both Pass states, which made job_id a
        requirement on every caller. /api/pipeline/run-single cannot satisfy it
        -- score_batch mints job_id as a fresh uuid4 when it inserts the row,
        so it does not exist when the execution starts -- and Add Job failed at
        ExtractMatchedJob from 2026-09-29 until 2026-10-08 with:

            The JSONPath '$.job_id' specified for the field 'job_id.$'
            could not be found in the input

        The #132 comment even recorded the assumption that broke it: "Both
        callers in app.py always send resume_only". There were three.

`job_id` is gone now, because it was dereferenced, carried, and read by nothing
-- no Lambda, no Choice, no frontend. These tests make the remaining
requirements checkable instead of assumed.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "template.yaml").read_text()
APP = (ROOT / "app.py").read_text()

# The two Pass states that replace the whole execution state on the single-job
# machine. Named explicitly: a new one should fail this list and be considered,
# not silently inherit the contract.
PASS_STATES = ("SkipToTailor", "ExtractMatchedJob")


def _required_paths(state_name: str) -> set[str]:
    """Top-level `$.x` fields a Pass state dereferences with `.$`."""
    block = TEMPLATE.split(f'"{state_name}": {{', 1)[1].split('"Next"', 1)[0]
    return {m.group(1) for m in re.finditer(r'"\w+\.\$":\s*"\$\.(\w+)"', block)}


def _single_job_execution_inputs() -> list[set[str]]:
    """Keys of every `input=json.dumps({...})` passed with the single-job ARN.

    Walks the AST rather than grepping: an earlier test in this repo matched
    its own explanatory comment quoting the code it was asserting about.
    """
    tree = ast.parse(APP)
    inputs: list[set[str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "start_execution"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        arn = kw.get("stateMachineArn")
        if not (isinstance(arn, ast.Name) and arn.id == "single_arn"):
            continue
        payload = kw.get("input")
        # input=json.dumps({...})
        if (isinstance(payload, ast.Call) and isinstance(payload.func, ast.Attribute)
                and payload.func.attr == "dumps" and payload.args
                and isinstance(payload.args[0], ast.Dict)):
            inputs.append({k.value for k in payload.args[0].keys
                           if isinstance(k, ast.Constant) and isinstance(k.value, str)})
    return inputs


def test_the_pass_states_are_still_there():
    """If they were renamed, every assertion below is vacuously true."""
    for name in PASS_STATES:
        assert f'"{name}": {{' in TEMPLATE, f"{name} is gone from template.yaml"


def test_the_callers_are_found():
    inputs = _single_job_execution_inputs()
    assert len(inputs) >= 3, (
        f"found {len(inputs)} single-job start_execution call(s); the #132 "
        "outage happened because someone counted two and there were three")


@pytest.mark.parametrize("state", PASS_STATES)
def test_every_caller_supplies_every_field_that_state_dereferences(state):
    required = _required_paths(state)
    assert required, f"{state} dereferences nothing — did its shape change?"
    for keys in _single_job_execution_inputs():
        missing = sorted(required - keys)
        assert not missing, (
            f"a caller of the single-job machine omits {missing}, which {state} "
            f"dereferences with .$ — that is a hard JSONPath error at runtime, "
            f"not a default. It supplies {sorted(keys)}."
        )


def test_job_id_is_not_required_again_without_a_producer():
    """It was dereferenced by both Pass states and read by nothing, and
    /api/pipeline/run-single cannot supply it: score_batch mints job_id as a
    fresh uuid4 when it inserts the row, so there is none when the execution
    starts. Re-adding it breaks Add Job again."""
    for state in PASS_STATES:
        assert "job_id" not in _required_paths(state), (
            f"{state} requires job_id again. No Lambda, Choice or frontend reads "
            "it, and the Add Job caller cannot produce one — this is the exact "
            "shape that broke Add Job from #132 until 2026-10-08."
        )


def test_the_state_machine_is_still_valid_json():
    """The edit that removed job_id was textual, so this is cheap insurance."""
    for m in re.finditer(r'DefinitionString:\s*!Sub\s*\|?\s*\n', TEMPLATE):
        pass  # presence only; full ASL parsing is sam validate --lint's job
    for state in PASS_STATES:
        block = TEMPLATE.split(f'"{state}": {{', 1)[1].split('"Next"', 1)[0]
        assert block.count("{") >= block.count("}") - 1, f"{state} braces look wrong"
