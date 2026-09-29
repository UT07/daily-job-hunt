"""The tailored .tex is keyed by job_hash, not job_id.

Measured against the live bucket on 2026-09-29, over 1,000 jobs and 1,794 S3
objects:

    users/.../{job_id}_tailored.tex    (what the code built)      0   0.0%
    users/.../{job_hash}_tailored.tex                           721  72.1%

Zero percent. Both sections endpoints derived the key from job_id — a UUID —
while every object in the bucket is keyed by job_hash. GET /sections therefore
404'd for every job, so the Resume Studio could not open a single real one, and
_do_rebuild_sections could not have found a .tex to rebuild from either.

Two rounds of green unit tests and a six-lens adversarial review all missed it,
because every one of them mocked S3. Only listing the real bucket found it.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
import app  # noqa: E402

USER = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
JOB_ID = "72e49215-116d-462d-ab85-a6e9fc146836"      # a UUID
JOB_HASH = "786a593fe1bd2c2079e45850af257a91"        # what S3 is actually keyed by


def _db_returning(data):
    db = MagicMock()
    chain = MagicMock()
    for m in ("select", "eq", "maybe_single"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value = MagicMock(data=data)
    db.client.table.return_value = chain
    return db


def test_key_is_built_from_job_hash_not_job_id():
    with patch.object(app, "_db", _db_returning({"job_hash": JOB_HASH})):
        key = app._tailored_tex_key(USER, JOB_ID)
    assert key == f"users/{USER}/resumes/{JOB_HASH}_tailored.tex"
    assert JOB_ID not in key, "the UUID is in the key — this is the 0%-hit-rate bug"


def test_a_missing_hash_falls_back_to_the_job_id():
    """Not a silent None: a wrong key produces a clean 404 the caller already
    handles, whereas None would raise somewhere less obvious."""
    with patch.object(app, "_db", _db_returning({})):
        key = app._tailored_tex_key(USER, JOB_ID)
    assert key == f"users/{USER}/resumes/{JOB_ID}_tailored.tex"


def test_a_lookup_failure_falls_back_rather_than_raising():
    db = MagicMock()
    db.client.table.side_effect = RuntimeError("supabase down")
    with patch.object(app, "_db", db):
        key = app._tailored_tex_key(USER, JOB_ID)
    assert key.endswith(f"{JOB_ID}_tailored.tex")


def test_no_db_still_returns_a_key():
    with patch.object(app, "_db", None):
        assert app._tailored_tex_key(USER, JOB_ID).endswith("_tailored.tex")


def test_no_endpoint_builds_the_key_inline_any_more():
    """Both call sites must go through the helper.

    They drifted apart once already — get_job_sections and
    _do_rebuild_sections each hard-coded the same wrong convention, so fixing
    one would have left the other 404ing.
    """
    import pathlib
    import re
    src = pathlib.Path("app.py").read_text()

    # No endpoint may assign the key from an inline f-string any more.
    inline = re.findall(r'tex_s3_key\s*=\s*f"', src)
    assert not inline, f"{len(inline)} call site(s) still build tex_s3_key inline"

    # Both call sites go through the helper.
    assert src.count("_tailored_tex_key(user_id, job_id)") == 1
    assert src.count("_tailored_tex_key(user.id, job_id)") == 1


def test_the_docstring_and_the_code_agreed_on_job_hash_all_along():
    """get_job_sections' docstring already documented the job_hash convention
    while the code three lines below used job_id. The documentation was right
    and the implementation never matched it — which is why reading the code
    looked fine. Pin them together so they cannot diverge again.
    """
    import inspect
    doc = inspect.getdoc(app.get_job_sections) or ""
    assert "{job_hash}_tailored.tex" in doc
    src = inspect.getsource(app.get_job_sections)
    assert "_tailored_tex_key(" in src, "the docstring's convention is not what the code does"
