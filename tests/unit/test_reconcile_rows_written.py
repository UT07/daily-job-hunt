"""`rows written` must mean a row changed, not that a PATCH was sent.

Commit 23866f6 asked PostgREST for `Prefer: count=exact` so that Content-Range
would say how many rows the filter hit -- and then never read it. `written` was
still `status_code < 300`, and a PATCH whose filter matches nothing is a 204
like any other. So a hash with no `jobs` row (an orphan S3 object, a user_id
mismatch, a hash in neither column) was reported as written, and
`retailor_bulk` exited 0 over résumés that were never linked: the same
"212 invisible PDFs" shape the script exists to prevent. CLAUDE.md #2.

The header is the only evidence, so a response WITHOUT it cannot be counted as
written either -- "probably fine" is the guess this file removes.
"""
import importlib.util
import pathlib
from unittest import mock

import pytest

_SCRIPT = pathlib.Path("scripts/reconcile_resume_rows.py")
_spec = importlib.util.spec_from_file_location("reconcile_resume_rows", _SCRIPT)
reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile)

HASH = "b3026c307c74"


def _resp(status, content_range=None, text=""):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    r.headers = {"Content-Range": content_range} if content_range is not None else {}
    return r


def _run(patch_response):
    """Drive `_reconcile` in links-only commit mode with one row, real code
    path, only the network and S3 replaced."""
    s3 = mock.Mock()
    s3.head_object.return_value = {}
    s3.generate_presigned_url.return_value = "https://example/presigned"
    policy = _resp(200)
    policy.json.return_value = [{"composition_policy": None}]
    with mock.patch.object(reconcile.boto3, "client", return_value=s3), \
         mock.patch.object(reconcile.httpx, "get", return_value=policy), \
         mock.patch.object(reconcile.httpx, "patch", return_value=patch_response) as patch:
        summary = reconcile._reconcile(
            [{"job_hash": HASH, "company": "Grinds360", "resume_s3_key": None}],
            url="https://db", headers={}, user_id="u1", commit=True,
            verbose=False, links_only=True)
    assert patch.called, "the double never reached the PATCH -- the test proves nothing"
    return summary


class TestRowsWrittenMeansRowsChanged:
    def test_a_patch_that_changed_one_row_is_written(self):
        s = _run(_resp(204, "*/1"))
        assert (s["written"], s["failed"], s["unmatched"]) == (1, 0, 0)

    def test_a_range_form_content_range_is_read_too(self):
        s = _run(_resp(204, "0-0/1"))
        assert s["written"] == 1

    def test_a_patch_that_matched_nothing_is_not_written(self):
        # The defect: 204 with */0 used to count as written.
        s = _run(_resp(204, "*/0"))
        assert s["written"] == 0
        assert s["unmatched"] == 1
        # And it must fail the run, or retailor_bulk still exits 0.
        assert s["failed"] == 1

    def test_no_content_range_cannot_be_counted_as_written(self):
        s = _run(_resp(204, None))
        assert s["written"] == 0
        assert s["failed"] == 1

    def test_a_rejected_patch_is_still_a_failure(self):
        s = _run(_resp(400, None, text="bad"))
        assert (s["written"], s["failed"]) == (0, 1)


@pytest.mark.parametrize("header,expected", [
    ("*/0", 0), ("*/1", 1), ("0-4/5", 5), ("*/*", None), ("", None), (None, None),
    ("garbage", None),
])
def test_rows_patched_parses_only_a_real_count(header, expected):
    assert reconcile._rows_patched(_resp(204, header)) == expected
