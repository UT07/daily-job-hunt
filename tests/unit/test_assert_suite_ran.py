"""The floor check that keeps a skipped suite from reading as coverage.

scripts/assert_suite_ran.py is the check both CI test jobs rely on to tell
"42 passed" from "42 declined to run". PR #154 introduced it as an inline
heredoc in .github/workflows/test.yml, verified by hand against three reports
and thereafter unverifiable — a gate whose own checker is untested is the same
bug one level up. These cover the cases the CI log would otherwise have to prove
each time: a healthy run, a wholesale skip, an all-failed run, a report that was
never written, and a file that is not a junit report at all.
"""
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.assert_suite_ran import check_floor, main, read_counts  # noqa: E402


def _report(tmp_path: pathlib.Path, *, tests: int, skipped: int = 0,
            failures: int = 0, errors: int = 0) -> pathlib.Path:
    """A junit report in the shape pytest actually writes.

    Verified against a real run of tests/integration/ on 2026-09-30:
    <testsuites name="pytest tests"><testsuite name="pytest" errors=".."
    failures=".." skipped=".." tests=".." ...>.
    """
    path = tmp_path / "junit.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest tests">'
        f'<testsuite name="pytest" errors="{errors}" failures="{failures}" '
        f'skipped="{skipped}" tests="{tests}" time="0.05">'
        "</testsuite></testsuites>",
    )
    return path


def test_a_healthy_run_passes(tmp_path):
    assert check_floor(_report(tmp_path, tests=24), 24) == 0


def test_a_suite_skipped_wholesale_fails(tmp_path, capsys):
    """The case the floor exists for: exit 0 from pytest, zero coverage."""
    assert check_floor(_report(tmp_path, tests=24, skipped=24), 24) == 1
    assert "only 0" in capsys.readouterr().out


def test_one_missing_test_fails(tmp_path):
    """Off-by-one matters: the floor is a floor, not an approximation."""
    assert check_floor(_report(tmp_path, tests=24, skipped=1), 24) == 1


def test_extra_tests_pass(tmp_path):
    """Adding tests must not fail the gate; only losing them does."""
    assert check_floor(_report(tmp_path, tests=30), 24) == 0


def test_failures_do_not_count_as_executed(tmp_path):
    """23 passed + 1 failed is not 24 executed."""
    assert check_floor(_report(tmp_path, tests=24, failures=1), 24) == 1


def test_errors_count_as_failures(tmp_path):
    """A fixture that raised is an `errors` entry, not a `failures` one."""
    assert check_floor(_report(tmp_path, tests=24, errors=24), 24) == 1


def test_a_report_that_was_never_written_fails(tmp_path, capsys):
    """pytest died before writing anything — that proves nothing, so it fails."""
    assert check_floor(tmp_path / "absent.xml", 1) == 1
    assert "never written" in capsys.readouterr().out


def test_a_file_that_is_not_junit_fails(tmp_path, capsys):
    path = tmp_path / "junit.xml"
    path.write_text("not xml at all")
    assert check_floor(path, 1) == 1
    assert "not a readable junit report" in capsys.readouterr().out


def test_xml_without_a_testsuite_fails(tmp_path, capsys):
    """An empty <testsuites/> would otherwise sum to 0 tests and 0 failures."""
    path = tmp_path / "junit.xml"
    path.write_text('<testsuites name="pytest tests"></testsuites>')
    assert check_floor(path, 1) == 1
    assert "not a readable junit report" in capsys.readouterr().out


def test_a_bare_testsuite_root_is_read(tmp_path):
    """Valid junit that pytest does not emit, but other runners do."""
    path = tmp_path / "junit.xml"
    path.write_text('<testsuite name="x" tests="5" skipped="0" failures="0" errors="0"/>')
    assert read_counts(path).executed == 5


def test_several_testsuites_are_summed(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(
        "<testsuites>"
        '<testsuite tests="10" skipped="1" failures="0" errors="0"/>'
        '<testsuite tests="15" skipped="0" failures="2" errors="0"/>'
        "</testsuites>",
    )
    counts = read_counts(path)
    assert (counts.collected, counts.skipped, counts.failed) == (25, 1, 2)
    assert counts.executed == 22


def test_missing_counters_default_to_zero(tmp_path):
    """Some writers omit errors= entirely; that must not raise."""
    path = tmp_path / "junit.xml"
    path.write_text('<testsuites><testsuite tests="3"/></testsuites>')
    assert read_counts(path).executed == 3


def test_cli_reports_the_floor_it_was_given(tmp_path, capsys):
    report = _report(tmp_path, tests=5, skipped=5)
    assert main([str(report), "5", "integration tests"]) == 1
    assert "integration tests passed, floor is 5" in capsys.readouterr().out


def test_cli_rejects_wrong_argument_counts(tmp_path):
    assert main([str(_report(tmp_path, tests=1))]) == 2


def test_cli_passes_a_healthy_run(tmp_path):
    assert main([str(_report(tmp_path, tests=24)), "24"]) == 0


@pytest.mark.parametrize("floor", [0, 1])
def test_a_zero_floor_is_satisfiable_by_nothing(tmp_path, floor):
    """Documents why the workflow guard forbids a floor of 0.

    With floor=0 an entirely skipped suite passes, which is the exact state the
    check was written to catch.
    """
    result = check_floor(_report(tmp_path, tests=24, skipped=24), floor)
    assert result == (0 if floor == 0 else 1)
