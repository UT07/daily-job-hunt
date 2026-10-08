"""One answer to "is this resume any good", from the measurements we already take.

Everything in here was already being measured by 2026-10-07 and none of it was
being aggregated:

  page count          shared.page_check.check_pdf      -> compile_result
  composition rules   shared.composition_policy        -> tailor_result
  ATS extraction      shared.ats_extract_check         -> compile_result
  writing quality     tailor_resume._quality_warnings  -> a log line, then gone

So four checks ran, and no column, dashboard or alarm could answer the only
question that matters about a generated document. `quality_warnings` was the
worst of it: computed, logged at WARNING, used to pick between a first attempt
and a retry, and then dropped on the floor before the return.

The central rule here is CLAUDE.md #2 — a status that cannot tell "did the
work" from "did nothing" is a lie. So a check whose input is absent is
UNMEASURED, never clean, and a verdict containing any unmeasured check is
graded `unmeasured` rather than `pass`. That is deliberately inconvenient: it
means a resume cannot be called good until every check has actually run on it,
which is the pressure that keeps the instrumentation wired up instead of
quietly rotting back to a log line.

The distinction this module keeps separate, because conflating them is how
CLAUDE.md #13 happened:

  measured  — did the check run at all? (instrumentation health)
  blocking  — if it found something, does that sink the document? (severity)

A check can be required-to-run and still non-blocking when it fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

PASS = "pass"
WARN = "warn"
FAIL = "fail"
UNMEASURED = "unmeasured"

# Severity is a table, not scattered string literals, so that "what happens
# when this fires" is reviewable in one place and testable as data.
#
# Every check is required to RUN. That is the point: a resume whose writing was
# never checked is not a resume we can call good, and making the verdict say so
# is what stops the measurement being dropped again.
#
# `blocking` is the narrower claim — this finding alone makes the document unfit
# to send:
#   pages        the user's loudest complaint; a 4-page resume is not sendable
#   composition  the hard rules the user asked to state once and for all
#   ats          a resume an ATS cannot parse is worth nothing, however it reads
#   writing      NOT blocking: these are style findings whose false-positive
#                rate is not characterised (CLAUDE.md #16). The one genuinely
#                disqualifying member of that family — fabrication — is
#                enforced in tailor_resume.handler, not here: a generated body
#                still carrying a fabrication finding after every repair is
#                refused, the composed corpus ships, `used_fallback` is True
#                and `quality_warnings` is None (graded `unmeasured`). So a
#                fabrication never reaches this check on a shipped tailored
#                body. Until 2026-10-08 this comment claimed it "already
#                blocks upstream in guardrails.output_guards"; it did not —
#                the council finalizes best-effort once its repair budget is
#                spent (41% of runs), and the handler only reported it.
#                Blocking `writing` wholesale would let a banned phrase sink a
#                document, which is a repair, not a rejection.
CHECKS: dict[str, bool] = {
    "pages": True,
    "composition": True,
    "ats": True,
    "writing": False,
}


@dataclass(frozen=True)
class Check:
    name: str
    measured: bool
    violations: tuple[str, ...]
    blocking: bool

    @property
    def clean(self) -> bool:
        """Measured AND found nothing. An unmeasured check is never clean."""
        return self.measured and not self.violations


@dataclass(frozen=True)
class Verdict:
    grade: str
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def reasons(self) -> tuple[str, ...]:
        """Why it is not a pass, in the order a reader should act on them.

        Blocking findings first, then style findings, then the checks that
        never ran -- which read last because they are a statement about us,
        not about the document.
        """
        blocking = [f"{c.name}: {v}" for c in self.checks if c.blocking for v in c.violations]
        advisory = [f"{c.name}: {v}" for c in self.checks
                    if not c.blocking and c.measured for v in c.violations]
        absent = [f"{c.name}: never measured" for c in self.checks if not c.measured]
        return tuple(blocking + advisory + absent)

    def to_row(self) -> dict[str, Any]:
        """The shape stored on the job row.

        Flat and self-describing on purpose: a row that says only "fail" sends
        the reader back to the logs, and the logs are what this replaces.
        """
        return {
            "grade": self.grade,
            "reasons": list(self.reasons),
            "checks": {
                c.name: {
                    "measured": c.measured,
                    "blocking": c.blocking,
                    "violations": list(c.violations),
                }
                for c in self.checks
            },
        }


def _normalise(value: Any) -> tuple[bool, tuple[str, ...]]:
    """(measured, violations) from one check's reported result.

    `None` means the check did not run. `[]` means it ran and found nothing.
    Those are different facts and the whole module depends on not confusing
    them, which is why `[] or None` style coalescing is never used on these.
    """
    if value is None:
        return False, ()
    if isinstance(value, str):  # a single violation, defensively accepted
        return True, (value,)
    if isinstance(value, Iterable):
        return True, tuple(str(v) for v in value)
    # A bool or a number cannot be read as either fact, so it is not evidence.
    return False, ()


def grade(
    *,
    pages: Sequence[str] | None = None,
    composition: Sequence[str] | None = None,
    ats: Sequence[str] | None = None,
    writing: Sequence[str] | None = None,
) -> Verdict:
    """Aggregate the four measurements into one answer.

    Keyword-only: every caller passes four lists of strings and a positional
    signature would let two of them swap places without a single test noticing.

    Order of precedence, and each clause is here because the alternative lies:

      1. any check did not run        -> `unmeasured`.  Not `pass`: we do not
         know. Not `fail`: the document may be perfect. Ranked FIRST so a
         broken instrument cannot be reported as a clean document -- that is
         CLAUDE.md #12, as a return value.
      2. a blocking check fired       -> `fail`
      3. only advisory checks fired   -> `warn`
      4. everything ran and was clean -> `pass`
    """
    checks = tuple(
        Check(name=name, measured=m, violations=v, blocking=CHECKS[name])
        for name, m, v in (
            (n, *_normalise(val))
            for n, val in (("pages", pages), ("composition", composition),
                           ("ats", ats), ("writing", writing))
        )
    )

    if any(not c.measured for c in checks):
        return Verdict(UNMEASURED, checks)
    if any(c.blocking and c.violations for c in checks):
        return Verdict(FAIL, checks)
    if any(c.violations for c in checks):
        return Verdict(WARN, checks)
    return Verdict(PASS, checks)


def from_step_results(
    tailor_result: Mapping[str, Any] | None,
    compile_result: Mapping[str, Any] | None,
) -> Verdict:
    """Grade from the two Step Functions results, as `save_job` sees them.

    A missing step result is NOT four clean checks. `.get` on an absent mapping
    would hand every check `None`, which grades `unmeasured` -- correct, and
    stated explicitly here so it cannot be mistaken for an accident.

    Note which key each check comes from: `page_violations` and
    `ats_violations` are documented in compile_latex as always-present, so an
    empty list from there is a positive claim of compliance. `quality_warnings`
    is the one that has to be plumbed through tailor_resume's return value --
    before 2026-10-07 it existed only as a log line.
    """
    t = tailor_result or {}
    c = compile_result or {}
    return grade(
        pages=c.get("page_violations"),
        composition=t.get("composition_violations"),
        ats=c.get("ats_violations"),
        writing=t.get("quality_warnings"),
    )
