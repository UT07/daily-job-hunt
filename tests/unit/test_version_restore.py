"""A version has to archive bytes, not a URL that will point somewhere else.

Measured 2026-09-29 against production:

- Artifacts are keyed users/{uid}/resumes/{job_hash}_tailored.pdf, with no
  version component.
- `aws s3api get-bucket-versioning --bucket utkarsh-job-hunt` returns empty:
  versioning is DISABLED.
- A job at resume_version=2 has exactly one .pdf and one .tex, each modified
  once. Regenerating overwrote the previous artifact in place.
- resume_versions held 9 rows carrying only *_s3_url, and 4 of them had
  resume_s3_url NULL.

So restore_resume_version copied a stale presigned URL back onto the job. Two
things were wrong with that even when it "worked": presigned URLs expire after
7 days with no key recorded to mint a new one, and the object the URL named had
already been overwritten — so the user got the CURRENT document back, labelled
as an older version, and the endpoint returned 200.

The user's read of the symptom was that two identical versions meant duplicate
files wasting storage. It was the opposite: one file, overwritten, and a
version list that could not point at different versions.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
import app  # noqa: E402

LIVE = "users/u1/resumes/abc123_tailored.pdf"


# --- key derivation ---------------------------------------------------------

def test_version_key_puts_archives_in_a_subdirectory():
    assert app._version_key(LIVE, 3) == "users/u1/resumes/versions/abc123_tailored_v3.pdf"


def test_version_key_keeps_the_extension_last():
    """The archive must still be a .pdf, or the browser will not render it."""
    assert app._version_key(LIVE, 1).endswith(".pdf")
    assert app._version_key("users/u1/resumes/abc_tailored.tex", 2).endswith(".tex")


def test_version_key_does_not_collide_across_versions():
    keys = {app._version_key(LIVE, n) for n in range(1, 6)}
    assert len(keys) == 5


def test_version_key_is_not_the_live_key():
    """The whole point: writing the archive must not overwrite the original."""
    assert app._version_key(LIVE, 2) != LIVE


# --- archival ---------------------------------------------------------------

def _s3_spy():
    s3 = MagicMock()
    return s3


def test_archive_copies_both_artifacts():
    s3 = _s3_spy()
    job = {"resume_s3_key": LIVE, "cover_letter_s3_key": "users/u1/resumes/abc123_cl.pdf"}
    with patch.object(app, "_get_s3", return_value=s3):
        out = app._archive_job_artifacts(job, 2)
    assert set(out) == {"resume_s3_key", "cover_letter_s3_key"}
    assert s3.copy_object.call_count == 2
    # CopySource must be the LIVE key and Key the archive, never reversed —
    # reversing them would overwrite the live artifact with the archive.
    for call in s3.copy_object.call_args_list:
        assert call.kwargs["CopySource"]["Key"] != call.kwargs["Key"]
        assert "/versions/" in call.kwargs["Key"]
        assert "/versions/" not in call.kwargs["CopySource"]["Key"]


def test_archive_skips_artifacts_that_do_not_exist():
    s3 = _s3_spy()
    with patch.object(app, "_get_s3", return_value=s3):
        out = app._archive_job_artifacts({"resume_s3_key": LIVE}, 1)
    assert out == {"resume_s3_key": "users/u1/resumes/versions/abc123_tailored_v1.pdf"}
    assert s3.copy_object.call_count == 1


def test_archive_reports_only_what_it_actually_wrote():
    """A failed copy must not be recorded as an archive.

    Recording it would produce a version row that passes the restorable check
    and then 502s on restore — worse than refusing up front.
    """
    s3 = _s3_spy()
    s3.copy_object.side_effect = RuntimeError("AccessDenied")
    with patch.object(app, "_get_s3", return_value=s3):
        out = app._archive_job_artifacts({"resume_s3_key": LIVE}, 1)
    assert out == {}


def test_archive_failure_does_not_raise():
    """It runs inside the regenerate the user asked for. Losing the archive is
    bad; failing the regenerate over it is worse."""
    s3 = _s3_spy()
    s3.copy_object.side_effect = RuntimeError("boom")
    with patch.object(app, "_get_s3", return_value=s3):
        app._archive_job_artifacts({"resume_s3_key": LIVE, "cover_letter_s3_key": "k"}, 9)


# --- the contract the endpoint must keep ------------------------------------

def test_restore_refuses_a_version_with_no_archived_keys():
    """The nine existing rows have no keys. Restoring one would serve the
    current document under an old version number and return 200."""
    import inspect
    src = inspect.getsource(app.restore_resume_version)
    assert 'version.get("resume_s3_key")' in src
    assert "409" in src, "an unrestorable version must be refused, not faked"


def test_restore_copies_objects_rather_than_urls():
    import inspect
    src = inspect.getsource(app.restore_resume_version)
    assert "copy_object" in src, (
        "restore still only rewrites URLs; the live key is what readers "
        "resolve, so the object under it has to change"
    )
    # The old behaviour — writing the version's URL onto the job — must be gone.
    assert '"resume_s3_url": version.get("resume_s3_url")' not in src


def test_regenerate_archives_before_it_overwrites():
    import inspect
    src = inspect.getsource(app.re_tailor_job)
    assert "_archive_job_artifacts" in src
    archive_at = src.index("_archive_job_artifacts")
    insert_at = src.index('table("resume_versions").insert')
    assert archive_at < insert_at, (
        "the snapshot row is written before the artifacts are copied, so it "
        "records keys that were never created"
    )
