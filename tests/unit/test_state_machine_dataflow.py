"""Every path a Choice reads must still be in the state when it is reached.

test_state_machine_asl_valid.py proves the machines are well-formed: valid
fields, every state terminates, every Next resolves. The single-job machine
passed all of that and still failed on every Regenerate, because validity says
nothing about what is *in* the payload by the time a state runs.

Production failure, 2026-09-29 17:27, execution 19908d35:

    ExecutionFailed: An error occurred while executing the state
    'CheckArtifactScope'. Invalid path '$.resume_only': The choice state's
    condition path references an invalid value.

The execution input did contain resume_only:

    {"user_id": "...", "job_hash": "ec7fd11c559c", "skip_scoring": true,
     "job_id": "...", "resume_only": true}

but the state entering the Choice three steps later was:

    {"light_touch": false, "tailoring_depth": "moderate", "user_id": "...",
     "job_hash": "...", "tailor_result": {...}, "compile_result": {...}}

SkipToTailor is a Pass with a Parameters block and no ResultPath. In ASL that
builds a brand-new payload and the default ResultPath of "$" replaces the whole
state — anything not named in Parameters is gone for the rest of the run. The
Choice added by #126 read a key that a state three hops earlier had deleted.

This test walks each machine from StartAt, tracks which top-level keys exist
after every state, and asserts each Choice's Variable is still reachable. It is
deliberately conservative: it models only the two shapes that actually delete
keys (Pass/Task with Parameters or InputPath and no merging ResultPath) and
gives up on anything it cannot model rather than guessing, so a pass means
"no proven deletion" rather than "definitely fine".
"""
import json
import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "template.yaml"


def _definitions():
    """Top-level ASL definitions embedded in template.yaml, by StartAt."""
    text = TEMPLATE.read_text()
    out = []
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
        body = re.sub(r"\$\{[^}]+\}", "arn:aws:lambda:eu-west-1:0:function:x", body)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("States"), dict):
            out.append(parsed)
    return out


def _produced_keys(state):
    """Top-level keys a Pass/Task's Parameters block produces.

    "job_hash.$" and "job_hash" both produce the key job_hash.
    """
    params = state.get("Parameters")
    if not isinstance(params, dict):
        return None
    return {k[:-2] if k.endswith(".$") else k for k in params}


def _keys_after(state, incoming):
    """Top-level keys present after `state` runs, or None if unmodellable."""
    produced = _produced_keys(state)
    result_path = state.get("ResultPath", "$")

    if state.get("InputPath") not in (None, "$"):
        return None  # narrows the state in a way we do not model

    if result_path is None:
        return set(incoming)  # ResultPath:null discards the result, keeps input

    if isinstance(result_path, str) and result_path.startswith("$.") and result_path.count(".") == 1:
        # Merges under one new key; the input survives intact.
        return set(incoming) | {result_path[2:]}

    if result_path == "$":
        if produced is not None:
            # Parameters define the payload and it REPLACES the state.
            return produced
        if state.get("Type") == "Task":
            return None  # Lambda output replaces the state; contents unknown
        if state.get("Type") in ("Pass", "Choice", "Wait", "Succeed", "Fail"):
            return set(incoming)
        return None

    return None


def _referenced_key(variable):
    """Top-level key a Choice Variable reads, or None if not a $.key path."""
    if not isinstance(variable, str) or not variable.startswith("$."):
        return None
    return re.split(r"[.\[]", variable[2:])[0]


def _choice_variables(choice_rule):
    """Every Variable inside a Choice rule, including And/Or/Not nesting."""
    found = []
    if "Variable" in choice_rule:
        found.append(choice_rule)
    for key in ("And", "Or"):
        for sub in choice_rule.get(key, []) or []:
            found.extend(_choice_variables(sub))
    if isinstance(choice_rule.get("Not"), dict):
        found.extend(_choice_variables(choice_rule["Not"]))
    return found


def _walk(definition, entry_keys):
    """Return [(state_name, missing_key, variable)] for unreachable Choice paths."""
    states = definition["States"]
    problems = []
    seen = set()
    queue = [(definition["StartAt"], frozenset(entry_keys))]

    while queue:
        name, keys = queue.pop()
        if (name, keys) in seen or name not in states:
            continue
        seen.add((name, keys))
        state = states[name]

        if state.get("Type") == "Choice":
            for rule in state.get("Choices", []):
                for leaf in _choice_variables(rule):
                    # IsPresent is the guard FOR a possibly-absent key; a rule
                    # that checks it is explicitly handling absence.
                    if "IsPresent" in leaf:
                        continue
                    guarded = any("IsPresent" in o for o in _choice_variables(rule))
                    if guarded:
                        continue
                    key = _referenced_key(leaf.get("Variable"))
                    if key is not None and keys is not None and key not in keys:
                        problems.append((name, key, leaf["Variable"]))

        nxt = _keys_after(state, keys) if state.get("Type") != "Choice" else set(keys)
        nxt = frozenset(nxt) if nxt is not None else None
        if nxt is None:
            continue  # cannot model further down this path

        targets = []
        if state.get("Next"):
            targets.append(state["Next"])
        if state.get("Type") == "Choice":
            targets += [c["Next"] for c in state.get("Choices", []) if c.get("Next")]
            if state.get("Default"):
                targets.append(state["Default"])
        for catch in state.get("Catch", []) or []:
            if catch.get("Next"):
                targets.append(catch["Next"])
        for t in targets:
            queue.append((t, nxt))

    return problems


# What app.py actually sends when starting the single-job machine. Both callers
# (re_tailor_job and re_tailor_jobs) send exactly these keys.
SINGLE_JOB_INPUT = {"user_id", "job_hash", "job_id", "skip_scoring", "resume_only"}


def _single_job_machine():
    for d in _definitions():
        if "CheckArtifactScope" in d.get("States", {}):
            return d
    pytest.fail("single-job machine not found — is CheckArtifactScope still there?")


def test_no_choice_reads_a_key_an_earlier_state_deleted():
    problems = _walk(_single_job_machine(), SINGLE_JOB_INPUT)
    assert not problems, "\n".join(
        f"  {name}: reads {var!r} but {key!r} is not in the state by then"
        for name, key, var in problems
    )


def test_the_pass_states_carry_the_scope_through():
    """The specific regression. Both Pass states replace the payload, so the
    scope has to be named in Parameters or it is silently dropped."""
    states = _single_job_machine()["States"]
    for name in ("SkipToTailor", "ExtractMatchedJob"):
        produced = _produced_keys(states[name])
        assert produced is not None, f"{name} no longer has Parameters"
        assert "resume_only" in produced, (
            f"{name} drops resume_only — CheckArtifactScope will fail with "
            "'Invalid path $.resume_only' on every Regenerate"
        )
        assert "job_id" in produced, f"{name} drops job_id"


def test_the_scope_choice_tolerates_a_missing_field():
    """A hand-started execution must take the full pipeline, not crash."""
    rule = _single_job_machine()["States"]["CheckArtifactScope"]["Choices"][0]
    leaves = _choice_variables(rule)
    assert any("IsPresent" in leaf for leaf in leaves), (
        "CheckArtifactScope has no IsPresent guard; an execution started "
        "without resume_only fails the whole machine"
    )


def test_the_detector_would_have_caught_the_real_bug():
    """Guard the guard: feed it the pre-fix shape and require a complaint.

    A data-flow checker that cannot fail is worth nothing, and this one has
    enough give-up branches to become vacuous by accident.
    """
    broken = json.loads(json.dumps(_single_job_machine()))
    broken["States"]["SkipToTailor"]["Parameters"] = {
        "job_hash.$": "$.job_hash",
        "user_id.$": "$.user_id",
        "light_touch": False,
        "tailoring_depth": "moderate",
    }
    broken["States"]["CheckArtifactScope"]["Choices"] = [
        {"Variable": "$.resume_only", "BooleanEquals": True, "Next": "SaveJobComplete"}
    ]
    problems = _walk(broken, SINGLE_JOB_INPUT)
    assert any(p[1] == "resume_only" for p in problems), (
        "the detector did not flag the exact production failure it exists for"
    )
