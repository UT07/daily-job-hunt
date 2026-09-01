"""Validate the embedded Step Functions definitions offline.

`sam validate` checks CloudFormation syntax but does NOT parse the Amazon
States Language inside DefinitionString. So an invalid ASL field passes all
15 CI gates and only fails at deploy time, mid-update, taking the stack
through UPDATE_ROLLBACK_COMPLETE:

    Invalid State Machine Definition: 'SCHEMA_VALIDATION_FAILED:
    Field '_comment_concurrency' is not supported at /States/ScoreBatchMap'

That cost a full ~15 minute deploy plus a rollback. ASL only permits a fixed
set of fields per state type — notably `Comment` (capital C), never an
arbitrary `_comment_*` key. These tests parse the definitions out of
template.yaml and check that offline, with no AWS credentials required.
"""
import json
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "template.yaml"

# Fields ASL allows, per state type. Sourced from the ASL spec.
COMMON = {"Type", "Comment", "Next", "End", "InputPath", "OutputPath",
          "ResultPath", "Parameters", "ResultSelector", "Retry", "Catch",
          "QueryLanguage", "Assign", "Output", "Arguments"}
ALLOWED = {
    "Task": COMMON | {"Resource", "TimeoutSeconds", "TimeoutSecondsPath",
                      "HeartbeatSeconds", "HeartbeatSecondsPath", "Credentials"},
    "Map": COMMON | {"Iterator", "ItemProcessor", "ItemsPath", "ItemReader",
                     "ItemSelector", "ItemBatcher", "ResultWriter",
                     "MaxConcurrency", "MaxConcurrencyPath", "ToleratedFailureCount",
                     "ToleratedFailurePercentage"},
    "Parallel": COMMON | {"Branches"},
    "Choice": COMMON | {"Choices", "Default"},
    "Pass": COMMON | {"Result"},
    "Wait": COMMON | {"Seconds", "SecondsPath", "Timestamp", "TimestampPath"},
    "Succeed": COMMON,
    "Fail": COMMON | {"Error", "ErrorPath", "Cause", "CausePath"},
}


def _extract_definitions():
    """Pull every embedded ASL definition out of template.yaml."""
    text = TEMPLATE.read_text()
    definitions = []
    for match in re.finditer(r'"StartAt"\s*:', text):
        brace = text.rindex("{", 0, match.start())
        depth = 0
        for i, ch in enumerate(text[brace:], brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body = text[brace:i + 1]
                    break
        else:
            continue
        # CFN !Sub placeholders aren't valid JSON — swap for a dummy ARN
        body = re.sub(r"\$\{[^}]+\}", "arn:aws:lambda:eu-west-1:0:function:x", body)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        # The regex also matches nested StartAt blocks inside Branches and
        # Iterators. Those are re-validated via recursion anyway, so keep only
        # objects that actually carry a States map.
        if isinstance(parsed, dict) and isinstance(parsed.get("States"), dict):
            definitions.append(parsed)
    return definitions


def _walk_states(states, path="States"):
    """Yield (path, name, state) for every state, recursing into nested ones."""
    for name, state in states.items():
        here = f"{path}/{name}"
        yield here, name, state
        for key in ("Iterator", "ItemProcessor"):
            if isinstance(state.get(key), dict) and "States" in state[key]:
                yield from _walk_states(state[key]["States"], f"{here}/{key}")
        for i, branch in enumerate(state.get("Branches", []) or []):
            if "States" in branch:
                yield from _walk_states(branch["States"], f"{here}/Branches[{i}]")


def test_definitions_are_parseable():
    defs = _extract_definitions()
    assert len(defs) >= 2, f"expected both state machines, found {len(defs)}"


def test_no_unsupported_fields_in_any_state():
    """The exact check that would have caught the 2026-09-01 deploy failure."""
    problems = []
    for definition in _extract_definitions():
        for path, _name, state in _walk_states(definition["States"]):
            stype = state.get("Type")
            allowed = ALLOWED.get(stype)
            if allowed is None:
                problems.append(f"{path}: unknown state Type {stype!r}")
                continue
            for field in state:
                if field not in allowed:
                    problems.append(f"{path}: field {field!r} not supported on {stype}")
    assert not problems, "Invalid ASL — deploy WILL fail and roll back:\n  " + "\n  ".join(problems)


def test_every_state_terminates():
    """Each state must have Next, End, or be a terminal type."""
    dangling = []
    for definition in _extract_definitions():
        for path, _name, state in _walk_states(definition["States"]):
            if state.get("Type") in ("Succeed", "Fail", "Choice"):
                continue
            if "Next" not in state and not state.get("End"):
                dangling.append(path)
    assert not dangling, f"states with neither Next nor End: {dangling}"
