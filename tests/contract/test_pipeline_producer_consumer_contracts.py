"""Generalized Lambda-to-Lambda producer/consumer contract tests.

tests/unit/test_save_metrics_pipeline_contract.py pins exactly ONE edge in
the daily pipeline's state machine: SaveJob's return -> save_metrics.py's
reads. This module generalizes that technique (parse template.yaml's real
embedded ASL + each Lambda's real AST; never hand-write "what the other
side should look like" on both sides, since two independently wrong
assumptions that happen to agree can never fail) across the OTHER
Task -> Task key-passing edges in `DailyPipelineStateMachine` that were
previously unguarded by anything except the fixture-based
tests/contract/test_daily_pipeline_chain.py — and that file's fixtures are
hand-typed dicts in conftest.py, not parsed from either producer or
consumer, so a rename on both "sides" of one of ITS checks would still
agree with itself and stay green forever. Nothing here imports or executes
Lambda code; every check is static AST/regex parsing, same discipline as
the file it generalizes.

Edges covered (see template.yaml's ProcessMatchedJobs Map + the states
immediately before it):

    merge_dedup   --new_job_hashes-->  chunk_hashes   (explicit Parameters)
    chunk_hashes  --chunks[i]-------->  score_batch    (raw Map item, no Parameters)
    score_batch   --matched_items[i]-> tailor_resume   (raw Map item, no Parameters)
    score_batch   --matched_items[i]-> ItemProcessor's own Choice/Parameters
                                        (skip_cover_letter, skip_contacts, etc.)
    tailor_resume --tex_s3_key------->  compile_latex   (explicit Parameters, resume doc_type)
    generate_cover_letter --tex_s3_key-> compile_latex  (explicit Parameters, cover_letter doc_type)
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "template.yaml"
PIPELINE_DIR = REPO / "lambdas" / "pipeline"


# --- template.yaml ASL extraction (same technique as test_save_metrics_pipeline_contract.py) ---

def _extract_asl_definitions() -> list[dict]:
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


def _daily_pipeline_states() -> dict:
    for definition in _extract_asl_definitions():
        if "ProcessMatchedJobs" in definition.get("States", {}):
            return definition["States"]
    raise AssertionError(
        "Could not find DailyPipelineStateMachine (a definition containing "
        "ProcessMatchedJobs) in template.yaml — has it been renamed?"
    )


def _process_matched_jobs_item_states() -> dict:
    states = _daily_pipeline_states()
    map_state = states["ProcessMatchedJobs"]
    assert map_state["Type"] == "Map"
    return map_state["ItemProcessor"]["States"]


def _collect_asl_refs(node) -> tuple[set[str], dict[str, set[str]]]:
    """Walk a parsed ASL fragment collecting every JSONPath reference found
    in a `"<field>.$": "$...."` Parameters entry, a Choice `"Variable"`, or
    an `"ItemsPath"` — the three shapes template.yaml uses to say "read this
    key from upstream data" (as opposed to `ResultPath`, which says where
    to WRITE a state's own output, and is deliberately not collected here).

    Returns (top_level_keys, nested_refs):
      - top_level_keys: keys referenced as exactly "$.foo" (read directly
        off the current Map iteration item / execution input).
      - nested_refs: {bucket: {keys}} for "$.bucket.foo" references (e.g.
        "$.tailor_result.tex_s3_key" -> nested_refs["tailor_result"] = {"tex_s3_key"}).

    Deeper paths (3+ segments) are ignored — none of the edges this file
    pins use them.
    """
    top_level: set[str] = set()
    nested: dict[str, set[str]] = {}

    def _record(value: str) -> None:
        if not isinstance(value, str) or not value.startswith("$."):
            return
        parts = value[2:].split(".")
        if len(parts) == 1 and parts[0]:
            top_level.add(parts[0])
        elif len(parts) == 2 and all(parts):
            nested.setdefault(parts[0], set()).add(parts[1])

    def _walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k.endswith(".$") or k in ("Variable", "ItemsPath"):
                    _record(v)
                _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return top_level, nested


# --- lambdas/pipeline/*.py source parsing (no import/execution) -------------

def _last_return_dict_keys(module_name: str, function_name: str = "handler") -> set[str]:
    """String keys of the Return-with-dict-literal statement that appears
    LAST (by source line) in `function_name`.

    Deliberately "last", not "first" (test_save_metrics_pipeline_contract.py's
    approach): several of these handlers early-return a smaller error/empty
    shape near the top (e.g. tailor_resume-style guard clauses) before the
    real success-path dict at the end. "Last by line number" reliably picks
    the success path across every module this is used on today; a module
    that legitimately returns its richest dict earlier than an error path
    would need a different helper, so re-check by hand if this assertion
    ever fires "no dict literal return found".
    """
    source = (PIPELINE_DIR / f"{module_name}.py").read_text()
    tree = ast.parse(source, filename=f"{module_name}.py")
    best: tuple[int, set[str]] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
                    keys = set()
                    for k in stmt.value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
                    if best is None or stmt.lineno > best[0]:
                        best = (stmt.lineno, keys)
    assert best, f"no `return {{...}}` dict literal found in {module_name}.{function_name}"
    return best[1]


def _appended_dict_keys(module_name: str, list_var: str, function_name: str = "handler") -> set[str]:
    """String keys of every dict literal passed to `<list_var>.append({...})`
    inside `function_name` — for handlers (score_batch.py) that build a
    result list item-by-item rather than returning a dict literal directly."""
    source = (PIPELINE_DIR / f"{module_name}.py").read_text()
    tree = ast.parse(source, filename=f"{module_name}.py")
    keys: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for stmt in ast.walk(node):
                is_append_call = (
                    isinstance(stmt, ast.Call)
                    and isinstance(stmt.func, ast.Attribute)
                    and stmt.func.attr == "append"
                    and isinstance(stmt.func.value, ast.Name)
                    and stmt.func.value.id == list_var
                    and stmt.args
                    and isinstance(stmt.args[0], ast.Dict)
                )
                if is_append_call:
                    found = True
                    for k in stmt.args[0].keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
    assert found, f"no `{list_var}.append({{...}})` call found in {module_name}.{function_name}"
    return keys


def _event_keys_read(module_name: str, function_name: str = "handler") -> set[str]:
    """Every key `function_name` reads off its `event` parameter, via either
    `event["key"]` (required) or `event.get("key", ...)` (optional-with-
    default — still a real production dependency: a `.get()` on a key that
    silently no-longer exists is exactly the ArtifactsCompiled bug shape
    this whole test suite exists to catch, so it counts as "reads" too)."""
    source = (PIPELINE_DIR / f"{module_name}.py").read_text()
    tree = ast.parse(source, filename=f"{module_name}.py")
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for stmt in ast.walk(node):
                if (
                    isinstance(stmt, ast.Subscript)
                    and isinstance(stmt.value, ast.Name)
                    and stmt.value.id == "event"
                    and isinstance(stmt.slice, ast.Constant)
                    and isinstance(stmt.slice.value, str)
                ):
                    keys.add(stmt.slice.value)
                elif (
                    isinstance(stmt, ast.Call)
                    and isinstance(stmt.func, ast.Attribute)
                    and stmt.func.attr == "get"
                    and isinstance(stmt.func.value, ast.Name)
                    and stmt.func.value.id == "event"
                    and stmt.args
                    and isinstance(stmt.args[0], ast.Constant)
                ):
                    keys.add(stmt.args[0].value)
    return keys


# ---------------------------------------------------------------------------
# Edge 1: merge_dedup.py --new_job_hashes--> ChunkJobHashes (chunk_hashes.py)
# ---------------------------------------------------------------------------

def test_merge_dedup_still_feeds_chunk_job_hashes_via_dedup_result():
    states = _daily_pipeline_states()
    chunk_state = states["ChunkJobHashes"]
    assert chunk_state["Resource"] == "ChunkHashesFunction.Alias"
    _, nested = _collect_asl_refs(chunk_state["Parameters"])
    assert "dedup_result" in nested, (
        "ChunkJobHashes no longer reads from $.dedup_result.* — "
        "MergeAndDedup's ResultPath and this test both assume it does."
    )


def test_chunk_job_hashes_only_reads_fields_merge_dedup_actually_returns():
    states = _daily_pipeline_states()
    _, nested = _collect_asl_refs(states["ChunkJobHashes"]["Parameters"])
    consumed = nested.get("dedup_result", set())

    produced = _last_return_dict_keys("merge_dedup")
    unknown = consumed - produced
    assert not unknown, (
        f"ChunkJobHashes's Parameters read {sorted(unknown)} from "
        f"$.dedup_result, but merge_dedup.py's handler only ever returns "
        f"{sorted(produced)}. Renaming merge_dedup's return key without "
        f"updating template.yaml silently feeds ChunkHashesFunction `null`."
    )


# ---------------------------------------------------------------------------
# Edge 2: chunk_hashes.py --chunks[i]--> ScoreChunk (score_batch.py), raw item
# ---------------------------------------------------------------------------

def test_score_chunk_state_has_no_parameters_override():
    """Ground truth this edge depends on: ScoreChunk must receive the raw
    Map item verbatim (no Parameters remapping) for "chunk_hashes's chunk
    dict IS score_batch's event" to hold."""
    item_states = _process_matched_jobs_item_states()
    states = _daily_pipeline_states()
    score_chunk = states["ScoreBatchMap"]["ItemProcessor"]["States"]["ScoreChunk"]
    assert score_chunk["Resource"] == "ScoreBatchFunction.Alias"
    assert "Parameters" not in score_chunk, (
        "ScoreChunk now has a Parameters block — chunk_hashes.py's chunk "
        "shape and score_batch.py's event reads are no longer directly "
        "comparable; update this test to follow the new remapping."
    )


def test_score_batch_only_reads_fields_chunk_hashes_actually_produces():
    produced = _last_return_dict_keys("chunk_hashes", function_name="handler")
    # chunk_hashes.py returns {"chunks": [...], "total": ..., "num_chunks": ...} —
    # the per-item shape lives inside the "chunks" list, not the top-level
    # return dict, so pull it from the literal chunk dict built in the loop.
    source = (PIPELINE_DIR / "chunk_hashes.py").read_text()
    tree = ast.parse(source)
    chunk_item_keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            for k in node.args[0].keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    chunk_item_keys.add(k.value)
    assert chunk_item_keys, "Could not find the chunk dict literal in chunk_hashes.py"
    assert "chunks" in produced, "chunk_hashes.py handler no longer returns a top-level 'chunks' key"

    consumed = _event_keys_read("score_batch")
    unknown = consumed - chunk_item_keys
    assert not unknown, (
        f"score_batch.py's handler reads {sorted(unknown)} from its event, "
        f"but each chunk_hashes.py chunk only ever contains "
        f"{sorted(chunk_item_keys)}."
    )


# ---------------------------------------------------------------------------
# Edge 3: score_batch.py --matched_items[i]--> TailorResume + the
# ItemProcessor's own Choice/Parameters gates (skip_cover_letter, etc.)
# ---------------------------------------------------------------------------

def test_process_matched_jobs_items_path_matches_score_result_matched_items():
    states = _daily_pipeline_states()
    top, nested = _collect_asl_refs({"ItemsPath": states["ProcessMatchedJobs"]["ItemsPath"]})
    assert nested.get("score_result") == {"matched_items"}, (
        "ProcessMatchedJobs.ItemsPath no longer points at "
        "$.score_result.matched_items — this whole edge's premise changed."
    )


def test_tailor_resume_state_has_no_parameters_override():
    item_states = _process_matched_jobs_item_states()
    tailor_state = item_states["TailorResume"]
    assert tailor_state["Resource"] == "TailorResumeFunction.Alias"
    assert "Parameters" not in tailor_state, (
        "TailorResume now remaps its input — matched_items[i] and "
        "tailor_resume.py's event reads are no longer directly comparable."
    )


def test_matched_items_shape_covers_every_top_level_key_the_map_iteration_reads():
    """Every `$.<key>` (single-segment) reference anywhere in
    ProcessMatchedJobs's ItemProcessor — Parameters AND Choice `Variable`
    fields alike — must be a key score_batch.py actually puts on each
    matched_items entry. These states never replace `$` with a ResultPath
    before reading these particular keys (TailorResume/CompileResume/etc.
    all write to nested ResultPaths), so the original Map item is still
    `$` when each reference fires.
    """
    item_states = _process_matched_jobs_item_states()
    top, _nested = _collect_asl_refs(item_states)

    produced = _appended_dict_keys("score_batch", "matched_items")
    unknown = top - produced
    assert not unknown, (
        f"ProcessMatchedJobs's ItemProcessor reads top-level key(s) "
        f"{sorted(unknown)} directly off the Map item, but score_batch.py's "
        f"matched_items.append({{...}}) only ever sets {sorted(produced)}. "
        "A Choice state or Parameters block referencing a key score_batch "
        "stopped producing would silently evaluate falsy/null forever "
        "(the same shape as the ArtifactsCompiled incident)."
    )
    # And the direction that matters for tailor_resume.py specifically,
    # since it receives the raw item as its whole event (previous test):
    consumed_by_tailor = _event_keys_read("tailor_resume")
    unknown_for_tailor = consumed_by_tailor - produced
    assert not unknown_for_tailor, (
        f"tailor_resume.py's handler reads {sorted(unknown_for_tailor)} from "
        f"its event, but score_batch.py's matched_items entries only ever "
        f"contain {sorted(produced)}."
    )


# ---------------------------------------------------------------------------
# Edge 4 + 5: tailor_resume.py / generate_cover_letter.py --tex_s3_key-->
# CompileResume / CompileCoverLetter (compile_latex.py)
# ---------------------------------------------------------------------------

def test_compile_resume_only_reads_fields_tailor_resume_actually_returns():
    item_states = _process_matched_jobs_item_states()
    compile_resume = item_states["CompileResume"]
    assert compile_resume["Resource"] == "CompileLatexFunction.Alias"
    _, nested = _collect_asl_refs(compile_resume["Parameters"])
    consumed = nested.get("tailor_result", set())
    assert consumed, "CompileResume's Parameters no longer reference $.tailor_result.* — update this test"

    produced = _last_return_dict_keys("tailor_resume")
    unknown = consumed - produced
    assert not unknown, (
        f"CompileResume's Parameters read {sorted(unknown)} from "
        f"$.tailor_result, but tailor_resume.py's handler only ever returns "
        f"{sorted(produced)}."
    )


def test_compile_cover_letter_only_reads_fields_generate_cover_letter_actually_returns():
    item_states = _process_matched_jobs_item_states()
    compile_cl = item_states["CompileCoverLetter"]
    assert compile_cl["Resource"] == "CompileLatexFunction.Alias"
    _, nested = _collect_asl_refs(compile_cl["Parameters"])
    consumed = nested.get("cover_letter_result", set())
    assert consumed, "CompileCoverLetter's Parameters no longer reference $.cover_letter_result.* — update this test"

    produced = _last_return_dict_keys("generate_cover_letter")
    unknown = consumed - produced
    assert not unknown, (
        f"CompileCoverLetter's Parameters read {sorted(unknown)} from "
        f"$.cover_letter_result, but generate_cover_letter.py's handler "
        f"only ever returns {sorted(produced)}."
    )


def test_compile_latex_reads_the_field_both_upstream_states_pass_as_tex_s3_key():
    """compile_latex.py is shared by both CompileResume and
    CompileCoverLetter — pin the one field name both callers must agree it
    reads (a rename on compile_latex.py's side breaks BOTH silently)."""
    consumed = _event_keys_read("compile_latex")
    assert "tex_s3_key" in consumed, (
        "compile_latex.py no longer reads event['tex_s3_key'] — both "
        "CompileResume and CompileCoverLetter's Parameters send exactly "
        "that field name."
    )
