"""Every writer of `match_score` must write `score_tier` with it.

The dashboard's tier filter queries the STORED `score_tier` column, so a score
without a tier is a row that no tier filter can return — and the deploy's
`score-tier-matches-score` check fails on it forever.

`score_batch.py` got this right from the start. `app.py`'s tailor path did not:
it wrote `"match_score": result.get("avg_score", 0)` and no tier, while
`_score_tier` sat 700 lines above it, already pinned to the pipeline's bands by
`test_score_tier_matches_the_pipeline`. CLAUDE.md #10 — the guard existed, the
data existed, and they never met. Measured 2026-10-08: one such row (`None@0`)
turned the whole deploy gate red, and `smoke_prod.py`'s docstring still claimed
"the current pipeline cannot produce this".

Searched repo-wide rather than in one file, because the defect WAS that one
file was never searched.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES = [
    ROOT / "app.py",
    ROOT / "lambdas/pipeline/score_batch.py",
    ROOT / "lambdas/pipeline/save_job.py",
    ROOT / "lambdas/pipeline/post_score.py",
]


# Calls that PERSIST a dict. The first scoping of this test walked every dict
# literal and flagged two that are not writes at all — a response payload
# returned to the browser (app.py's Studio score strip) and
# `compute_base_scores`, which only a script calls. Same mistake as counting
# Technical Skills entries as unquantified bullets: CLAUDE.md #7, scope the
# check to the population it is meant to judge.
_WRITE_CALLS = {"_update_job_artifacts", "insert", "update", "upsert", "_write_job_row"}


def _keys_of(node):
    return {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _persisted_dicts(path):
    """(lineno, keys) for every dict literal that reaches a DB write.

    Two shapes, because the code uses both: passed inline to a write call, and
    bound to a name that is later passed to one.
    """
    tree = ast.parse(path.read_text())
    out = []
    written_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None))
            if name not in _WRITE_CALLS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    out.append((arg.lineno, _keys_of(arg)))
                elif isinstance(arg, ast.Name):
                    written_names.add(arg.id)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(tgt, ast.Name) and tgt.id in written_names
                        for tgt in node.targets)):
            out.append((node.value.lineno, _keys_of(node.value)))
    return out


def _dicts_writing(path, key):
    return [(ln, keys) for ln, keys in _persisted_dicts(path) if key in keys]


def test_the_scan_finds_the_known_writers():
    """Anti-vacuity. If `_persisted_dicts` matched nothing the assertion below
    would pass against any code at all — which is how a check that judges the
    wrong population still looks green."""
    sb = _dicts_writing(ROOT / "lambdas/pipeline/score_batch.py", "match_score")
    assert sb, "score_batch's job_record was not recognised as a persisted dict"
    app = _dicts_writing(ROOT / "app.py", "match_score")
    assert app, "app.py's _update_job_artifacts payload was not recognised"


def test_a_response_payload_is_not_treated_as_a_write():
    """`compute_base_scores` returns a dict containing `match_score` and is
    called only by a script — flagging it would be a false positive, and the
    first version of this test did exactly that."""
    for lineno, keys in _persisted_dicts(ROOT / "lambdas/pipeline/score_batch.py"):
        assert "base_ats_score" not in keys, (
            f"compute_base_scores' return value at line {lineno} is being read "
            "as a database write")


def test_the_sources_exist():
    """A missing file would make every assertion below vacuous."""
    for p in SOURCES:
        assert p.exists(), f"{p} is gone — this suite is asserting nothing"


def test_every_dict_that_writes_match_score_also_writes_score_tier():
    offenders = []
    for path in SOURCES:
        for lineno, keys in _dicts_writing(path, "match_score"):
            if "score_tier" not in keys:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "these write match_score with no score_tier, so the dashboard's tier "
        f"filter can never return the row: {offenders}. app.py has _score_tier "
        "and score_batch has score_to_tier — use one."
    )


def test_the_two_tier_functions_still_exist():
    """The assertion above is only actionable while there is something to call."""
    app_fns = {n.name for n in ast.walk(ast.parse((ROOT / "app.py").read_text()))
               if isinstance(n, ast.FunctionDef)}
    assert "_score_tier" in app_fns
    sb_fns = {n.name for n in ast.walk(
        ast.parse((ROOT / "lambdas/pipeline/score_batch.py").read_text()))
        if isinstance(n, ast.FunctionDef)}
    assert "score_to_tier" in sb_fns
