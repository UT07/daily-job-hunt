"""Regression guard for the 2026-09-25 audit finding.

save_metrics.py's `_count_compiled_artifacts()` read a JSON field
(`compile_result.pdf_s3_key`) that never appears on a real
ProcessMatchedJobs Map iteration result, so
`DailyPipelineNoArtifactsAlarm`'s `ArtifactsCompiled` metric read 0.0 on
every one of 74 sampled days from 2026-05-01 to 2026-09-02 regardless of
what actually happened — including 2026-09-01, which had a real
`JobsMatched` Sum of 34 the same day (confirmed live via
`aws cloudwatch get-metric-statistics`, see fix-observability-report.md).

Root cause, wired in template.yaml: `ProcessMatchedJobs` is a Map state
with `ResultPath: "$.processed_jobs"`. Its ItemProcessor's only two
terminal states, `SaveJob` and `SaveJobAfterError`, both call
`SaveJobFunction` with no `ResultPath` override — so the Lambda's raw
return value REPLACES the whole iteration item (ASL's default ResultPath
is `$`). That means every entry in `processed_jobs` is exactly whatever
`lambdas/pipeline/save_job.py`'s `handler()` returns:
`{"job_hash", "user_id", "saved", "has_resume", "failed"}` — never
anything containing a nested `compile_result` key, because `compile_result`
was consumed and dropped by SaveJob one step earlier.

These tests parse the real artifacts (template.yaml's embedded ASL,
save_job.py's returned keys, and save_metrics.py's own source) instead of
hardcoding either side's field/metric names twice, so a future rename or
reshaping on either side of this contract fails a test here instead of
failing silently in production for another five months.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "template.yaml"
PIPELINE_DIR = REPO / "lambdas" / "pipeline"


# --- template.yaml parsing helpers -----------------------------------------

def _extract_asl_definitions():
    """Pull every embedded Step Functions definition out of template.yaml.

    Same technique as tests/unit/test_state_machine_asl_valid.py: find each
    `"StartAt":` occurrence, walk outward to its enclosing `{...}` block,
    swap CFN `${...}` !Sub placeholders (not valid JSON) for their bare
    inner text (still a valid JSON string, and keeps e.g. "SaveJobFunction"
    readable instead of collapsing every Resource to the same dummy ARN —
    this test needs to tell *which* function each state calls), then parse
    it as real JSON.
    """
    text = TEMPLATE.read_text()
    definitions = []
    for match in re.finditer(r'"StartAt"\s*:', text):
        brace = text.rindex("{", 0, match.start())
        depth = 0
        body = None
        for i, ch in enumerate(text[brace:], brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body = text[brace:i + 1]
                    break
        if body is None:
            continue
        body = re.sub(r"\$\{([^}]+)\}", r"\1", body)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("States"), dict):
            definitions.append(parsed)
    return definitions


def _daily_pipeline_definition():
    for definition in _extract_asl_definitions():
        if "ProcessMatchedJobs" in definition.get("States", {}):
            return definition
    raise AssertionError(
        "could not find a state machine definition containing "
        "ProcessMatchedJobs in template.yaml — has the daily pipeline "
        "been renamed/restructured?"
    )


def _process_matched_jobs_terminal_states():
    """Return (item_states, terminal_names) for ProcessMatchedJobs's
    ItemProcessor, where terminal_names are the states that both end the
    Map iteration (End: true) AND have no ResultPath — i.e. the states
    whose raw Lambda return becomes a processed_jobs[i] entry verbatim.
    """
    definition = _daily_pipeline_definition()
    map_state = definition["States"]["ProcessMatchedJobs"]
    assert map_state["Type"] == "Map"
    assert map_state["ResultPath"] == "$.processed_jobs", (
        "ProcessMatchedJobs no longer feeds $.processed_jobs — "
        "SavePipelineMetrics's Parameters and this test both assume it does"
    )
    item_states = map_state["ItemProcessor"]["States"]
    terminal = [
        name for name, state in item_states.items()
        if state.get("End") is True and "ResultPath" not in state
    ]
    assert terminal, (
        "expected at least one terminal, ResultPath-less state in "
        "ProcessMatchedJobs's ItemProcessor — if every terminal state now "
        "has a ResultPath, processed_jobs[i] is no longer a raw Lambda "
        "return and this whole test file's premise is stale"
    )
    return item_states, terminal


def _extract_resource_block(logical_id):
    """Return the raw YAML text of one top-level Resources entry, keyed by
    its logical id. Deliberately text-based (not a full CFN-aware YAML
    load with !Ref/!GetAtt/!Sub constructors) since we only need a couple
    of flat scalar properties out of an Alarm resource.
    """
    lines = TEMPLATE.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(logical_id)}:\s*$", line):
            start = i
            break
    assert start is not None, f"{logical_id} not found in template.yaml"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^  \S", lines[j]):  # next top-level (2-space) line
            end = j
            break
    return "\n".join(lines[start:end])


# --- lambdas/pipeline/*.py parsing helpers ----------------------------------

def _lambda_function_name_for_state(state):
    """Map a Task state's Resource — originally `${XyzFunction.Alias}`,
    already stripped to `XyzFunction.Alias` by _extract_asl_definitions —
    to 'Xyz'."""
    match = re.search(r"(\w+)Function\.Alias", state.get("Resource", ""))
    assert match, f"state has no recognizable Function.Alias Resource: {state}"
    return match.group(1)


# CFN logical-id prefix -> lambdas/pipeline/<module>.py. Only needs entries
# for functions that can terminate ProcessMatchedJobs's ItemProcessor.
_MODULE_FOR_FUNCTION = {
    "SaveJob": "save_job",
}


def _returned_dict_keys(module_name, function_name="handler"):
    """Parse a lambdas/pipeline module and return the literal string keys
    in its handler's top-level `return {...}` statement, without importing
    or executing the module.
    """
    source = (PIPELINE_DIR / f"{module_name}.py").read_text()
    tree = ast.parse(source, filename=f"{module_name}.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
                    keys = set()
                    for k in stmt.value.keys:
                        assert isinstance(k, ast.Constant) and isinstance(k.value, str), (
                            f"non-literal-string key in {module_name}.{function_name}'s "
                            f"return dict: {ast.dump(k)}"
                        )
                        keys.add(k.value)
                    return keys
    raise AssertionError(f"no `return {{...}}` dict literal found in {module_name}.{function_name}")


def _keys_read_by_count_compiled_artifacts():
    """Parse save_metrics.py's _count_compiled_artifacts and return every
    string literal passed to a top-level `job.get(...)` call — i.e. every
    field name it trusts a processed_jobs[i] entry to have directly (not
    counting lookups nested on a *different* variable, like
    `cover_compile.get(...)`).
    """
    source = (PIPELINE_DIR / "save_metrics.py").read_text()
    tree = ast.parse(source, filename="save_metrics.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_count_compiled_artifacts":
            keys = set()
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "job"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                ):
                    keys.add(call.args[0].value)
            return keys
    raise AssertionError("_count_compiled_artifacts not found in save_metrics.py")


def _module_level_string_constants(tree):
    """Module-level `NAME = "literal"` assignments, e.g. save_metrics.py's
    `_METRICS_NAMESPACE` / `_METRIC_ARTIFACTS_COMPILED` / `_METRIC_JOBS_MATCHED`.
    """
    consts = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value
    return consts


# --- the tests ---------------------------------------------------------------

def test_process_matched_jobs_still_feeds_processed_jobs_with_no_result_path():
    """Ground truth this whole file depends on hasn't silently changed
    shape (e.g. someone adding a ResultPath to SaveJob's Task, which would
    invalidate every assumption below in the other direction)."""
    _process_matched_jobs_terminal_states()  # raises on its own if wrong


def test_save_metrics_only_reads_fields_save_job_actually_returns():
    """The actual regression test for the 2026-09-25 audit bug.

    Before the fix: _count_compiled_artifacts read `job.get("compile_result")`,
    a key that never appears on a real processed_jobs[i] (see module
    docstring) — this assertion FAILS against that code (verified manually
    in fix-observability-report.md by re-running this test against the
    pre-fix file). After the fix, it reads `has_resume` / `failed`, both
    real keys on save_job.py's return.
    """
    item_states, terminal_names = _process_matched_jobs_terminal_states()
    real_keys = set()
    for name in terminal_names:
        function_name = _lambda_function_name_for_state(item_states[name])
        module = _MODULE_FOR_FUNCTION.get(function_name)
        assert module, (
            f"{name} calls {function_name}Function, which this test doesn't "
            f"know how to map to a lambdas/pipeline module — add it to "
            f"_MODULE_FOR_FUNCTION"
        )
        real_keys |= _returned_dict_keys(module)

    read_keys = _keys_read_by_count_compiled_artifacts()
    # cover_compile_result is a documented, currently-unreachable defensive
    # check — save_job.py has no cover-letter analog to has_resume yet
    # (see save_metrics.py's _count_compiled_artifacts docstring), so it's
    # tracked as a known follow-up rather than part of this contract.
    read_keys = read_keys - {"cover_compile_result"}

    unknown = read_keys - real_keys
    assert not unknown, (
        f"_count_compiled_artifacts() reads {sorted(unknown)} from each "
        f"processed_jobs entry, but the Lambda(s) that actually produce "
        f"that entry ({sorted(terminal_names)} -> {sorted(_MODULE_FOR_FUNCTION.values())}) "
        f"only ever return {sorted(real_keys)}. This is exactly the "
        f"2026-09-25 bug: DailyPipelineNoArtifactsAlarm's ArtifactsCompiled "
        f"metric silently reads 0 forever."
    )


def test_no_artifacts_alarm_metric_identity_matches_what_save_metrics_defines():
    """The other half of the save_metrics.py <-> template.yaml contract:
    CloudWatch Namespace/MetricName identity, as opposed to the per-item
    field-shape contract above. This half was never broken (both sides
    already agreed on "ArtifactsCompiled"), so unlike the test above this
    one isn't red-then-green — it's here so a *future* rename of the
    metric constant on only one side (an equally silent way to break this
    exact alarm) fails a test instead of failing in production.
    """
    alarm_block = _extract_resource_block("DailyPipelineNoArtifactsAlarm")
    alarm_namespace = re.search(r"^\s*Namespace:\s*(\S+)\s*$", alarm_block, re.MULTILINE).group(1)
    alarm_metric_name = re.search(r"^\s*MetricName:\s*(\S+)\s*$", alarm_block, re.MULTILINE).group(1)

    source = (PIPELINE_DIR / "save_metrics.py").read_text()
    consts = _module_level_string_constants(ast.parse(source, filename="save_metrics.py"))

    assert consts.get("_METRICS_NAMESPACE") == alarm_namespace, (
        f"save_metrics.py publishes to Namespace {consts.get('_METRICS_NAMESPACE')!r} "
        f"but DailyPipelineNoArtifactsAlarm watches Namespace {alarm_namespace!r}"
    )
    assert consts.get("_METRIC_ARTIFACTS_COMPILED") == alarm_metric_name, (
        f"save_metrics.py publishes MetricName "
        f"{consts.get('_METRIC_ARTIFACTS_COMPILED')!r} but "
        f"DailyPipelineNoArtifactsAlarm watches MetricName {alarm_metric_name!r}"
    )


def test_no_jobs_matched_alarm_metric_identity_matches_what_save_metrics_defines():
    """Same guard as above for the new DailyPipelineNoJobsMatchedAlarm
    (added alongside this fix) and the JobsMatched metric it watches."""
    alarm_block = _extract_resource_block("DailyPipelineNoJobsMatchedAlarm")
    alarm_namespace = re.search(r"^\s*Namespace:\s*(\S+)\s*$", alarm_block, re.MULTILINE).group(1)
    alarm_metric_name = re.search(r"^\s*MetricName:\s*(\S+)\s*$", alarm_block, re.MULTILINE).group(1)

    source = (PIPELINE_DIR / "save_metrics.py").read_text()
    consts = _module_level_string_constants(ast.parse(source, filename="save_metrics.py"))

    assert consts.get("_METRICS_NAMESPACE") == alarm_namespace
    assert consts.get("_METRIC_JOBS_MATCHED") == alarm_metric_name
