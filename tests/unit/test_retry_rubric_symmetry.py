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
    # added 2026-10-08: Yale's quantified-result term, advisory by measurement
    "_check_unquantified",
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


# ---------------------------------------------------------------------------
# The number that LEAVES the handler must describe the body that ships
# ---------------------------------------------------------------------------
# Same asymmetry one step further on. Since 2026-10-07 the handler returns
# `quality_warnings` so shared.resume_verdict can grade the resume, and the
# accepted retry swaps the body out AFTER the first measurement was taken. If
# the reported warnings are not swapped with it, the row describes the document
# that was thrown away -- and because a retry is only accepted when it has
# FEWER warnings, the error always flatters: a resume stored as clean whose
# shipped body was not the one measured.
#
# Mutation-tested: deleting `shipped_quality = retry_quality` left every other
# test in this repo passing.


def _assignments_to(node, target_name):
    """Every ast.Assign in `node` whose target is the bare name `target_name`."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == target_name for t in n.targets)]


def test_the_retry_swaps_the_reported_warnings_with_the_body():
    """Structural: wherever the retry body is accepted, the warnings follow.

    Checked as "in the same block" rather than "somewhere in the function",
    because the whole failure mode is the two assignments drifting apart.
    """
    handler = _function("handler")
    assert handler is not None

    blocks = []
    for node in ast.walk(handler):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list):
                blocks.append(stmts)

    accepting = [
        b for b in blocks
        if any(isinstance(s, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "ai_body" for t in s.targets)
               and isinstance(s.value, ast.Name) and s.value.id == "retry_body"
               for s in b)
    ]
    assert accepting, (
        "no block assigns `ai_body = retry_body` — the retry-acceptance branch "
        "moved, so this invariant is no longer checking anything"
    )
    for block in accepting:
        assigned = {t.id for s in block if isinstance(s, ast.Assign)
                    for t in s.targets if isinstance(t, ast.Name)}
        assert "shipped_quality" in assigned, (
            "the block that accepts `retry_body` does not reassign "
            "`shipped_quality`, so the returned warnings describe the body that "
            "was discarded — and since a retry is only accepted when it has "
            "FEWER warnings, the stored verdict is flattering, never harsh"
        )


def test_the_handler_reports_the_shipped_warnings_not_the_first_attempt():
    """`shipped_quality`, not `quality_warnings`, is what leaves the function."""
    handler = _function("handler")
    returns = [n for n in ast.walk(handler)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    assert returns, "handler no longer returns a dict literal"
    reported = {
        k.value: v for r in returns
        for k, v in zip(r.value.keys, r.value.values)
        if isinstance(k, ast.Constant)
    }
    assert "quality_warnings" in reported, (
        "the writing measurement is not returned — shared.resume_verdict grades "
        "it `unmeasured`, which is how it silently became a log line before"
    )
    value = reported["quality_warnings"]
    assert isinstance(value, ast.Name) and value.id == "shipped_quality", (
        "the returned warnings must be `shipped_quality` (the body that ships), "
        f"not {ast.dump(value)[:60]} — `quality_warnings` holds the FIRST "
        "attempt's findings even when a retry replaced it"
    )


# ---------------------------------------------------------------------------
# What the council finalized best-effort WITH must leave the handler too
# ---------------------------------------------------------------------------
# Measured 2026-10-07 over a 212-résumé batch: 87 runs (41%) exhausted the
# council's repair budget and shipped with a block-severity violation still
# present. The figure had to be reconstructed from CloudWatch by hand, for one
# batch, because `guard_report` never left the graph and `quality_gate` logged
# only that the budget was spent. Same shape as `quality_warnings` before it:
# measured, and then dropped before the return.


def test_the_handler_reports_what_the_guard_still_objected_to():
    handler = _function("handler")
    returns = [n for n in ast.walk(handler)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    reported = {k.value: v for r in returns
                for k, v in zip(r.value.keys, r.value.values)
                if isinstance(k, ast.Constant)}
    assert "guard_violations" in reported, (
        "the surviving block-severity violations are not returned, so nothing "
        "outside this function can tell a clean document from one that "
        "exhausted the repair budget — which is 41% of them"
    )


class TestSurvivingBlocking:
    """`None` (never measured) and `[]` (measured, clean) are different facts."""

    @staticmethod
    def _fn():
        import tailor_resume
        return tailor_resume._surviving_blocking

    def test_no_report_at_all_is_not_a_clean_report(self):
        assert self._fn()(None) is None, (
            "the legacy engine has no guard nodes; reporting [] would claim a "
            "clean verdict from an engine that never looked")

    def test_a_clean_report_is_an_empty_list(self):
        assert self._fn()({"passed": True, "violations": [], "blocking": []}) == []

    def test_only_the_blocking_subset_is_surfaced(self):
        got = self._fn()({"passed": False,
                          "violations": ["fabrication: 'Kotlin'", "banned_phrase: 'robust'"],
                          "blocking": ["fabrication: 'Kotlin'"]})
        assert got == ["fabrication: 'Kotlin'"]

    def test_a_report_predating_severity_falls_back_to_all_violations(self):
        """No `blocking` key means severity was discarded at serialisation.
        Reporting nothing for those would make this change look like it had
        cleaned them up."""
        got = self._fn()({"passed": False, "violations": ["fabrication: 'Scala'"]})
        assert got == ["fabrication: 'Scala'"]
