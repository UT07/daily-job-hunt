"""A compiled PDF's link must open: one presign path, minted when handed out.

Live run 2026-10-09 ~13:53 UTC (test user ee449fe1): three Studio compiles
(rebuild_sections tasks 5139e51e, 7c2570bf, bc46edc0) completed and returned
`users/<uid>/resumes/<hash>_tailored.pdf?...` links that S3 answered with
HTTP 403 InvalidAccessKeyId, while `GET /api/dashboard/jobs` links for the
same object worked. The stored task results in `pipeline_tasks` show why:

    ...972b82dc4141_tailored.pdf?AWSAccessKeyId=<REDACTED_AWS_KEY>
       &Signature=...&x-amz-security-token=<REDACTED_STS_TOKEN>%2FL6Oi...

`_do_rebuild_sections` minted the URL inside the SQS task, and `_save_task`
runs every result through `_sanitize_aws_creds`, which (correctly, for error
text) rewrites anything shaped like an ASIA key id or an IQoJ session token.
A presigned URL carries both by design. So the link the browser received named
an access key that does not exist.

The unit tests never saw it because tests/conftest.py's credentials are the
string "testing", which matches neither redaction pattern -- a double that
cannot fail the way production fails (CLAUDE.md #6). These tests sign with
credentials SHAPED like a Lambda role's.
"""
from __future__ import annotations

import ast
import os
import pathlib
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import AuthUser, get_current_user
from shared import s3_presign
from tests.unit.postgrest_double import FakeSupabase

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Shaped like the execution role's temporary credentials: an ASIA key id and
# an IQoJ-prefixed session token. Not real.
ROLE_KEY_ID = "ASIAEXAMPLEKEYID0001"
ROLE_TOKEN = "IQoJb3JpZ2luX2VjEXAMPLEexampleEXAMPLEexampleSESSIONtoken0123456789abcdef"


@pytest.fixture(autouse=True)
def patch_boto3_ssm():
    """Opt out of tests/unit/conftest.py's autouse `patch("boto3.client")`:
    these tests need botocore's real signer."""
    yield


@pytest.fixture
def role_credentials(monkeypatch):
    # boto3's default session resolves credentials once and caches them, so
    # an earlier test's session would sign with whatever it found first.
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)
    env = {
        "AWS_ACCESS_KEY_ID": ROLE_KEY_ID,
        "AWS_SECRET_ACCESS_KEY": "exampleSecretAccessKey0000000000000000000",
        "AWS_SESSION_TOKEN": ROLE_TOKEN,
        # tests/conftest.py's aws_credentials fixture sets this legacy name to
        # "testing" and never unsets it; botocore reads it too.
        "AWS_SECURITY_TOKEN": ROLE_TOKEN,
        "AWS_REGION": "eu-west-1",
        "AWS_DEFAULT_REGION": "eu-west-1",
        "S3_BUCKET": "utkarsh-job-hunt",
    }
    with patch.dict(os.environ, env):
        s3_presign.reset_client()
        yield
    s3_presign.reset_client()


def _signed_by_the_role(url: str) -> None:
    """The URL names the role's key id and carries its session token."""
    q = parse_qs(urlparse(url).query)
    key_id = (q.get("AWSAccessKeyId") or q.get("X-Amz-Credential") or [""])[0]
    token = (q.get("x-amz-security-token") or q.get("X-Amz-Security-Token") or [""])[0]
    assert key_id.startswith(ROLE_KEY_ID), url
    assert token == ROLE_TOKEN, url
    assert "REDACTED" not in url


# ── the live failure, end to end ───────────────────────────────────────────

USER = "ee449fe1-7c97-4ba2-96ea-54bf2a1ce20a"
JOB = "972b82dc4141"


@pytest.fixture
def studio(role_credentials, monkeypatch, tmp_path):
    """Drive the real rebuild task: real botocore client from the helper, with
    only its network calls (get/put object) replaced."""
    client = s3_presign.s3_client()
    stored = {}
    tex = b"\\documentclass{article}\\begin{document}x\\end{document}"
    monkeypatch.setattr(client, "get_object",
                        lambda **k: {"Body": type("B", (), {"read": lambda self: tex})()})
    monkeypatch.setattr(client, "put_object", lambda **k: stored.__setitem__(k["Key"], k["Body"]))

    # Any other S3 client the code builds gets the same stubbed instance, so
    # nothing here reaches the network whichever path the code takes.
    def _factory(name, *a, **k):
        if name != "s3":
            raise AssertionError(f"unexpected AWS client {name!r}")
        return client
    monkeypatch.setattr(boto3, "client", _factory)

    def _compile(path, out):
        p = pathlib.Path(out) / "r.pdf"
        p.write_bytes(b"%PDF-1.7")
        return str(p)

    monkeypatch.setattr(app_module, "compile_tex_to_pdf", _compile)
    monkeypatch.setattr(app_module, "_score_rebuilt_resume", lambda *a: None)
    import lambdas.pipeline.parse_sections as ps
    monkeypatch.setattr(ps, "rebuild_tex_from_sections", lambda s, b: b)

    db = FakeSupabase({
        "jobs": [{"job_id": JOB, "user_id": USER, "job_hash": JOB, "resume_s3_url": "old"}],
        "pipeline_tasks": [],
    })
    monkeypatch.setattr(app_module, "_db", db)
    return db


def _run_compile_task(task_id="5139e51e"):
    result = app_module._dispatch_task("rebuild_sections", {"job_id": JOB, "sections": {}}, user_id=USER)
    app_module._save_task(task_id, USER, {"status": "done", "result": result})
    return task_id


def test_the_compiled_pdf_link_the_browser_polls_for_is_signed_by_the_role(studio):
    tid = _run_compile_task()
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=USER, email="e@x.y")
    try:
        with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
            r = TestClient(app_module.app).get(f"/api/tasks/{tid}")
    finally:
        app_module.app.dependency_overrides.clear()
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["pdf_s3_key"] == f"users/{USER}/resumes/{JOB}_tailored.pdf"
    _signed_by_the_role(result["pdf_url"])
    assert urlparse(result["pdf_url"]).path.endswith(f"/users/{USER}/resumes/{JOB}_tailored.pdf")


def test_no_credential_bearing_url_is_persisted_in_a_task_result(studio):
    """What is stored is the key. A URL stored there is either scrubbed into
    a dead link or leaks a session token; both are wrong."""
    tid = _run_compile_task()
    (row,) = studio.rows("pipeline_tasks", task_id=tid)
    assert "pdf_url" not in row["result"]
    assert "X-Amz" not in str(row["result"]) and "AWSAccessKeyId" not in str(row["result"])


def test_the_scrubber_still_scrubs_error_text():
    """The fix is to stop storing URLs, not to weaken the scrubber."""
    out = app_module._sanitize_aws_creds(f"denied for {ROLE_KEY_ID} token {ROLE_TOKEN}")
    assert ROLE_KEY_ID not in out and ROLE_TOKEN not in out


# ── the helper ─────────────────────────────────────────────────────────────

def test_the_helpers_client_uses_the_default_chain_with_no_explicit_keys():
    with patch.object(s3_presign.boto3, "client") as factory:
        s3_presign.reset_client()
        try:
            s3_presign.s3_client()
        finally:
            s3_presign.reset_client()
    (args, kwargs), = factory.call_args_list
    assert args == ("s3",)
    assert set(kwargs) <= {"region_name", "config"}, kwargs


def test_the_helper_signs_with_the_session_token(role_credentials):
    _signed_by_the_role(s3_presign.presign_get("users/u/resumes/a.pdf"))


def test_a_client_built_from_explicit_keys_signs_a_url_s3_will_reject(role_credentials):
    """Control for the test above: the failure mode it guards against is real.
    A client given only the key id and secret drops the session token, which is
    exactly the URL S3 answers with InvalidAccessKeyId."""
    keyed = boto3.client(
        "s3", region_name="eu-west-1",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    url = s3_presign.presign_get("users/u/resumes/a.pdf", client=keyed)
    with pytest.raises(AssertionError):
        _signed_by_the_role(url)


def test_expiry_is_capped_at_seven_days(role_credentials):
    url = s3_presign.presign_get("k", expires=30 * 24 * 3600)
    q = parse_qs(urlparse(url).query)
    if "X-Amz-Expires" in q:
        assert int(q["X-Amz-Expires"][0]) <= 7 * 24 * 3600
    else:  # SigV2: an absolute epoch
        import time
        assert int(q["Expires"][0]) - time.time() <= 7 * 24 * 3600 + 5


def test_the_dashboard_and_the_studio_share_one_client(role_credentials):
    assert app_module._get_s3() is s3_presign.s3_client()


# ── structural: the next presign outside the helper fails CI ───────────────

def _production_py_files():
    skip = {".venv", "node_modules", ".git", "tests", "web", ".claude", "docs"}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if skip & set(rel.parts) or p.is_symlink() or any(q.is_symlink() for q in p.parents if ROOT in q.parents):
            continue
        yield p


def _calls(path):
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def test_the_scan_sees_the_modules_that_used_to_presign():
    """Population check (CLAUDE.md #7): a scan that never reaches these files
    passes for the wrong reason."""
    names = {p.relative_to(ROOT).as_posix() for p in _production_py_files()}
    assert {"app.py", "s3_uploader.py", "lambdas/pipeline/save_job.py", "shared/s3_presign.py"} <= names


def test_nothing_presigns_except_through_the_helper():
    offenders = []
    for path in _production_py_files():
        if path == ROOT / "shared" / "s3_presign.py":
            continue
        for call in _calls(path):
            if isinstance(call.func, ast.Attribute) and call.func.attr == "generate_presigned_url":
                offenders.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert offenders == [], f"presign through shared.s3_presign.presign_get: {offenders}"


def test_no_aws_client_is_built_with_explicit_credentials():
    banned = {"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}
    offenders = []
    for path in _production_py_files():
        for call in _calls(path):
            if {kw.arg for kw in call.keywords} & banned:
                offenders.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert offenders == [], offenders
