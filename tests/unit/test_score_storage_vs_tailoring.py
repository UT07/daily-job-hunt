"""A job scored below the threshold was evaluated and then forgotten.

`score_batch` used to read:

    match_score = score_result.get("match_score", 0)
    if match_score < min_score:
        continue            # <- the AI call had already been made and paid for

so the score was discarded and no row was written. Afterwards nothing could
distinguish "we assessed this job and rejected it" from "we never saw it":
14,121 rows in jobs_raw against 1,401 in jobs, with no record of the
difference. The user's instruction on 2026-10-07 was explicit — "I don't want a
hardcap on the jobs, I want as many applicable jobs as possible".

The threshold now governs TAILORING, not storage. Every scored job is stored
with its score and tier; `matched_items` — what the state machine fans out to
produce artifacts — still respects it, so the existing "signal not volume"
rule on artifacts is unchanged and a D-tier job still produces nothing.

And score_status/scored_at are written. They were database defaults nothing
ever set: 1290 of 1401 rows said 'pending' while every one of them had a
match_score, and that misled an audit of this very table into reporting 92% of
jobs unscored. A column that is read and never written is worse than an absent
one, because it answers confidently and wrongly.
"""
import ast
import pathlib
import re

SOURCE = pathlib.Path("lambdas/pipeline/score_batch.py").read_text()
TREE = ast.parse(SOURCE)


def _handler_src() -> str:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "handler":
            return ast.get_source_segment(SOURCE, node) or ""
    raise AssertionError("handler not found")


HANDLER = _handler_src()


def _threshold_if_lines() -> list[int]:
    """Line numbers of REAL `if match_score < min_score` statements.

    Walked as AST rather than matched as text. The first version of this test
    used a regex over the source and matched the explanatory COMMENT that
    quotes the old code — the same substring-for-structure mistake the ATS
    heading check made the day before.
    """
    out = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        t = node.test
        if (isinstance(t.left, ast.Name) and t.left.id == "match_score"
                and any(isinstance(o, ast.Lt) for o in t.ops)
                and any(isinstance(c, ast.Name) and c.id == "min_score"
                        for c in t.comparators)):
            out.append(node.lineno)
    return out


def _insert_line() -> int:
    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute) and node.attr == "insert":
            seg = ast.get_source_segment(SOURCE, node) or ""
            if 'table("jobs")' in seg:
                return node.lineno
    raise AssertionError("the jobs insert was not found")


def test_the_record_is_written_before_the_threshold_is_applied():
    """Storage must not be downstream of the cut, or the cut deletes evidence."""
    cuts = _threshold_if_lines()
    assert cuts, "the threshold disappeared entirely — artifacts are now unbounded"
    insert_at = _insert_line()
    assert all(c > insert_at for c in cuts), (
        f"a `match_score < min_score` branch at line(s) "
        f"{[c for c in cuts if c < insert_at]} runs BEFORE the insert at line "
        f"{insert_at}, so a scored job is discarded without a row"
    )


def test_the_threshold_still_gates_tailoring():
    """Storing everything must not mean tailoring everything — the user's
    standing rule is signal over volume."""
    cut = HANDLER.index("if match_score < min_score:")
    appended = HANDLER.index("matched_items.append(")
    assert cut < appended, (
        "matched_items is built without the threshold — every D-tier job would "
        "be queued for a tailored resume"
    )


def test_score_status_and_scored_at_are_written():
    """The two dead columns that misled an audit of this table."""
    for col in ('"score_status"', '"scored_at"'):
        assert col in HANDLER, f"{col} is still never written"
    assert '"score_status": "scored"' in HANDLER


def test_every_scored_job_reaches_the_insert():
    """No `continue` may sit between scoring and the insert except the ones
    that mean 'there is nothing to store' — a None result or a skip."""
    between = HANDLER[HANDLER.index("score_result = score_single_job_deterministic"):
                      HANDLER.index('db.table("jobs").insert(job_record)')]
    continues = re.findall(r"^\s*continue\s*$", between, re.MULTILINE)
    assert len(continues) <= 1, (
        f"{len(continues)} early exits between scoring and storing; each one is "
        f"a scored job that leaves no trace"
    )
    assert "if score_result is None" in between, (
        "the one permitted early exit should be the no-result case"
    )
