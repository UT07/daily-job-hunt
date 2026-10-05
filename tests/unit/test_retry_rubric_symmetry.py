"""The quality retry must be scored on the same rubric as the body it replaces.

`tailor_resume.handler` builds `quality_warnings` from the tailored body, and if
any fire it asks the model for one corrected attempt, accepting it when
`len(retry_quality) < len(quality_warnings)`.

That comparison is only meaningful if both sides count the same checks. They did
not. `quality_warnings` included `_check_fabrication`; `retry_quality` omitted
it. So the retry was scored on a strictly easier rubric than the body it was
replacing, and a retry that kept every fabricated skill counted as "improved"
whenever it dropped one banned phrase:

    original: 1 fabrication + 2 banned phrases -> 3 warnings
    retry:    1 fabrication + 2 banned phrases -> 2 counted   ACCEPTED

and the fabrication shipped. Since 55ebff2 a fabrication is a BLOCKING violation
in the council's own output guard, so this path could accept exactly what that
guard exists to reject.

The comment immediately above the comparison already guards against the same
asymmetry for truncation ("a shorter body trivially contains fewer banned
phrases"), which is why this is checked structurally rather than left to review:
the shape is easy to reintroduce one check at a time.

Asserted on the source rather than by driving `handler()`. Reaching that
comparison needs Supabase, S3, a council call and a compile step stubbed, and
CLAUDE.md rule 6 is explicit that a double which fails the way the bug fails is
worse than no test. The invariant here is "these two expressions call the same
set of checks", which is a property of the source.

2026-10-05: the invariant is now structural rather than merely checked. Both
sides call ONE builder, `_quality_warnings(body, base_body, fabrication_baseline)`,
so a check added to it reaches the original and the retry simultaneously and
there is no second list to forget. These tests therefore assert that the single
builder exists, that both call sites use it, and that no check is invoked
directly beside the comparison -- which is what reintroducing the bug would
look like. The original text above is kept because it explains why the shape
matters, not merely that it does.
"""
import ast
from pathlib import Path

SOURCE = Path("lambdas/pipeline/tailor_resume.py").read_text()
TREE = ast.parse(SOURCE)

# Every writing-quality check the rubric may legitimately call.
CHECK_NAMES = {
    "_check_banned_phrases", "_check_weak_openers",
    "_check_textbf_preservation", "_check_fabrication",
}


def _function(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _called_names(node):
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_a_single_builder_owns_the_rubric():
    """One function, or there are two lists again and they can drift."""
    builder = _function("_quality_warnings")
    assert builder is not None, (
        "_quality_warnings is gone — the rubric is being built inline again, "
        "which is the shape that let the retry be scored on an easier rubric"
    )
    called = _called_names(builder)
    missing = CHECK_NAMES - called
    assert not missing, f"the builder no longer calls: {sorted(missing)}"


def test_both_sides_of_the_comparison_use_that_builder():
    handler = _function("handler")
    assert handler is not None
    calls = [n for n in ast.walk(handler)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_quality_warnings"]
    assert len(calls) == 2, (
        f"expected the original and the retry to each call _quality_warnings, "
        f"found {len(calls)} call(s)"
    )


def test_no_check_is_called_directly_beside_the_comparison():
    """Adding a check to one side only is exactly how this broke before."""
    handler = _function("handler")
    direct = _called_names(handler) & CHECK_NAMES
    assert not direct, (
        f"{sorted(direct)} called directly in handler rather than through "
        "_quality_warnings — that is one side of the comparison again"
    )


def test_fabrication_specifically_is_counted():
    """The check whose omission caused the original defect."""
    builder = _function("_quality_warnings")
    assert "_check_fabrication" in _called_names(builder)
