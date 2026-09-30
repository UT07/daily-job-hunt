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
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "lambdas" / "pipeline" / "tailor_resume.py"


def _checks_contributing_to(var: str) -> set[str]:
    """Every `_check_*` called while building `var`, by assignment or .extend()."""
    tree = ast.parse(SRC.read_text())
    found: set[str] = set()

    def names_in(node) -> set[str]:
        out = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name and name.startswith("_check_"):
                    out.add(name)
        return out

    for node in ast.walk(tree):
        # var = <expr>
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == var for t in node.targets
        ):
            found |= names_in(node.value)
        # var.extend(<expr>) / var.append(<expr>)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("extend", "append")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == var):
            for arg in node.args:
                found |= names_in(arg)
    return found


def test_both_rubrics_are_discovered():
    """Guard the guard: an empty set on either side makes the comparison vacuous."""
    original = _checks_contributing_to("quality_warnings")
    retry = _checks_contributing_to("retry_quality")
    assert len(original) >= 3, original
    assert len(retry) >= 3, retry


def test_the_retry_is_scored_on_the_same_checks_as_the_original():
    original = _checks_contributing_to("quality_warnings")
    retry = _checks_contributing_to("retry_quality")
    missing = original - retry
    assert not missing, (
        f"the retry rubric omits {sorted(missing)} that the original counts, so "
        "`len(retry_quality) < len(quality_warnings)` compares a body against an "
        "easier standard than itself and can accept a retry that fixed nothing"
    )
    extra = retry - original
    assert not extra, (
        f"the retry rubric counts {sorted(extra)} that the original does not, which "
        "biases the comparison the other way and discards retries that did improve"
    )


def test_fabrication_specifically_is_counted_on_both_sides():
    """Named because it is the regression, and the costliest omission.

    A fabrication is a blocking violation in the council's guard. A retry rubric
    that cannot see it lets this path accept what that guard rejects.
    """
    for var in ("quality_warnings", "retry_quality"):
        assert "_check_fabrication" in _checks_contributing_to(var), var
