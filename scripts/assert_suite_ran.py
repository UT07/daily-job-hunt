#!/usr/bin/env python3
"""Assert that a pytest run executed tests, instead of skipping itself green.

A suite that is skipped wholesale exits 0. So a CI job whose only signal is
pytest's exit status cannot tell "42 tests passed" from "42 tests declined to
run" — CLAUDE.md § Verification rules 2: a status that cannot distinguish doing
the work from doing nothing is not a status. This reads the junit report the run
wrote and fails when fewer than `floor` tests actually executed.

Used by .github/workflows/test.yml for the Browser E2E job (floor added in
PR #154, inlined as a heredoc) and the integration job (PR for the last
`|| true` in the repo). It lives in a file rather than in two heredocs so the
floor logic itself is unit-tested — tests/unit/test_assert_suite_ran.py covers
the all-skipped, all-failed, missing-report and no-testsuite cases. A gate whose
own checker is unverified is the bug one level up.

Usage:
    python scripts/assert_suite_ran.py <junit-xml> <floor>
"""

from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True)
class SuiteCounts:
    """Totals summed over every <testsuite> in one junit report."""

    collected: int
    skipped: int
    failed: int

    @property
    def executed(self) -> int:
        """Tests that ran AND passed.

        Failures are subtracted as well as skips: a run where every test
        errored out in a fixture has executed nothing worth calling coverage,
        and the test step itself will already have gone red.
        """
        return self.collected - self.skipped - self.failed


def read_counts(report: pathlib.Path) -> SuiteCounts:
    """Sum the counters of every testsuite in a pytest junit report.

    pytest writes <testsuites><testsuite .../></testsuites>, but a bare
    <testsuite> root is valid junit and other runners emit it, so both shapes
    are read rather than assumed.

    stdlib ElementTree is deliberate: the only input is the junit file the same
    CI job just wrote with --junitxml, so there is no untrusted XML here and no
    reason to add defusedxml to tests/requirements-test.txt for it.
    """
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    if not suites:
        raise ValueError(f"{report} contains no <testsuite> element")
    return SuiteCounts(
        collected=sum(int(s.get("tests", 0)) for s in suites),
        skipped=sum(int(s.get("skipped", 0)) for s in suites),
        failed=sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites),
    )


def check_floor(report: pathlib.Path, floor: int, *, label: str = "tests") -> int:
    """Return a process exit code: 0 when at least `floor` tests passed.

    Prints a GitHub Actions ::error:: annotation on failure so the reason shows
    on the job summary and not only in the log.
    """
    if not report.is_file():
        # pytest died before writing a report — usually a fixture or a build
        # step raised. The test step already failed; say why this one cannot
        # judge rather than burying that failure under a traceback here.
        print(f"::error::{report} was never written, so this run proved nothing.")
        return 1

    try:
        counts = read_counts(report)
    except (ET.ParseError, ValueError) as exc:
        print(f"::error::{report} is not a readable junit report: {exc}")
        return 1

    print(
        f"collected={counts.collected} executed={counts.executed} "
        f"skipped={counts.skipped} failed={counts.failed}",
    )
    if counts.executed < floor:
        print(
            f"::error::only {counts.executed} {label} passed, floor is {floor}. "
            "A suite that skips itself is not a gate.",
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(f"usage: {sys.argv[0]} <junit-xml> <floor> [label]", file=sys.stderr)
        return 2
    report, floor = pathlib.Path(argv[0]), int(argv[1])
    label = argv[2] if len(argv) == 3 else "tests"
    return check_floor(report, floor, label=label)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
