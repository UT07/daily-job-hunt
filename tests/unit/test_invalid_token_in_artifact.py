"""Regression tests for the AWS credential sanitizer in `_save_task`.

Background: when a Lambda's IAM role temp credentials expire mid-task, a
boto3 ClientError raised inside e.g. `tailor_resume.py` can be flattened
via `str(e)` and propagate up to `app.py`'s SQS handler, which calls
`_save_task(... {"status": "error", "error": str(e)})`. The boto3 error's
str representation can include the failing request's STS session token
in cleartext (`IQoJ...`), which then becomes readable to the user via
`GET /api/tasks/{task_id}`.

`_sanitize_aws_creds` is the defense-in-depth scrubber. These tests pin
its contract so the bug class can't recur.

Bug F1 from docs/superpowers/plans/2026-04-29-comprehensive-prod-health.md
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app import _sanitize_aws_creds


# A realistic example of the leaked content the user observed in prod —
# this is the canonical S3 InvalidToken response body shape, flattened
# into a single-line str(ClientError) representation.
_LEAKED_BODY = (
    "An error occurred (InvalidToken) when calling the GetObject operation: "
    "The provided token is malformed or otherwise invalid."
    "IQoJb3JpZ2luX2VjEEYaCXVzLWVhc3QtMSI/MEUCIQC6wMOl7S5vbxWwo7EbI3PQOegk"
    "Lh1xA8tQbUMK6QceeQIganD90jeLxFwZJJ4P8/9aANUPJytQ0z9EJIuzKufjK"
)


class TestSanitizeAwsCreds:
    def test_strips_iqoj_session_token(self):
        out = _sanitize_aws_creds(_LEAKED_BODY)
        assert "IQoJ" not in out, f"raw STS token leaked: {out!r}"
        assert "<REDACTED_STS_TOKEN>" in out

    def test_strips_aws_access_key_aki(self):
        out = _sanitize_aws_creds("found AKIAEXAMPLE000ABCDEF in logs")
        assert "AKIAEXAMPLE000ABCDEF" not in out
        assert "<REDACTED_AWS_KEY>" in out

    def test_strips_aws_access_key_asia(self):
        out = _sanitize_aws_creds("temp creds: ASIAEXAMPLE000ABCDEF")
        assert "ASIAEXAMPLE000ABCDEF" not in out
        assert "<REDACTED_AWS_KEY>" in out

    def test_strips_xml_token_tag(self):
        body = "<Error><Code>InvalidToken</Code><Token>secrettokenvalue</Token></Error>"
        out = _sanitize_aws_creds(body)
        assert "secrettokenvalue" not in out
        assert "<Token>REDACTED</Token>" in out

    def test_strips_xml_session_token_tag(self):
        body = "<SessionToken>IQoJxxxsecretvalue</SessionToken>"
        out = _sanitize_aws_creds(body)
        assert "secretvalue" not in out
        assert "REDACTED" in out

    def test_passes_through_non_strings(self):
        assert _sanitize_aws_creds(None) is None
        assert _sanitize_aws_creds(42) == 42
        assert _sanitize_aws_creds(True) is True
        assert _sanitize_aws_creds(3.14) == 3.14

    def test_recurses_into_dict(self):
        val = {
            "error": "boto3 raised: IQoJb3JpZ2luX2VjEEYaCXVzLWVhc3QtMSI",
            "code": 500,
            "ok": True,
        }
        out = _sanitize_aws_creds(val)
        assert "IQoJ" not in out["error"]
        assert "<REDACTED_STS_TOKEN>" in out["error"]
        assert out["code"] == 500
        assert out["ok"] is True

    def test_recurses_into_list(self):
        val = [
            "IQoJb3JpZ2luX2VjEEYaCXVzLWVhc3QtMSItoken",
            "AKIAEXAMPLE000ABCDEF",
            42,
            None,
        ]
        out = _sanitize_aws_creds(val)
        assert "IQoJ" not in out[0]
        assert "AKIAEXAMPLE000ABCDEF" not in out[1]
        assert out[2] == 42
        assert out[3] is None

    def test_recurses_into_nested(self):
        val = {"results": [{"error": "IQoJxxxxxxxxxxxxxxxxx"}]}
        out = _sanitize_aws_creds(val)
        assert "IQoJ" not in out["results"][0]["error"]

    def test_idempotent(self):
        once = _sanitize_aws_creds(_LEAKED_BODY)
        twice = _sanitize_aws_creds(once)
        assert once == twice

    def test_preserves_safe_strings(self):
        # No AWS-shaped tokens — should pass through verbatim
        clean = "Resume tailored successfully for Acme Corp"
        assert _sanitize_aws_creds(clean) == clean


class TestSaveTaskSanitizes:
    """Verify _save_task pipes both `error` and `result` through the sanitizer
    before issuing the Supabase upsert."""

    def test_error_is_sanitized_before_upsert(self, monkeypatch):
        import app
        captured_row = {}

        class FakeTable:
            def upsert(self, row, on_conflict=None):
                captured_row.update(row)
                return self

            def execute(self):
                return None

        class FakeClient:
            def table(self, _name):
                return FakeTable()

        fake_db = MagicMock()
        fake_db.client = FakeClient()
        monkeypatch.setattr(app, "_db", fake_db)

        app._save_task(
            task_id="t1",
            user_id="u1",
            data={"status": "error", "error": _LEAKED_BODY},
        )

        assert "IQoJ" not in (captured_row.get("error") or "")
        assert captured_row["status"] == "error"
        assert captured_row["task_id"] == "t1"

    def test_result_dict_is_sanitized(self, monkeypatch):
        import app
        captured_row = {}

        class FakeTable:
            def upsert(self, row, on_conflict=None):
                captured_row.update(row)
                return self

            def execute(self):
                return None

        class FakeClient:
            def table(self, _name):
                return FakeTable()

        fake_db = MagicMock()
        fake_db.client = FakeClient()
        monkeypatch.setattr(app, "_db", fake_db)

        app._save_task(
            task_id="t2",
            user_id="u2",
            data={
                "status": "done",
                "result": {"error": "IQoJxxxxxxxxxxxxxxxxxxx", "ok": False},
            },
        )

        assert "IQoJ" not in (captured_row["result"]["error"])
        assert captured_row["result"]["ok"] is False
