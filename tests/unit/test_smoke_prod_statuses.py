"""smoke_prod.py must not report a check that judged nothing as a pass.

Three defects this pins (CLAUDE.md rule 2, "what would this report on a no-op
run?"):

  * resumes-are-distinct PASSED when it found zero .tex objects for the rows
    it was asked about — the exact reading a broken key convention or an
    emptied bucket would produce.
  * it also PASSED with fewer than 20 rows, i.e. without judging anything.
    That is now a SKIP, reported distinctly, never counted as passed.
  * the docstring promised a compile check that did not exist. compile-
    roundtrip now invokes the deployed CompileLatex alias. It is exercised
    here against fakes only; it is never run against production from tests.
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
from contextlib import redirect_stdout

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
def smoke(monkeypatch):
    spec = importlib.util.spec_from_file_location("smoke_prod_under_test", ROOT / "scripts" / "smoke_prod.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.RESULTS.clear()
    return mod


# --- fakes -----------------------------------------------------------------

class _Query:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self._data, "count": len(self._data)})()


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Query(self.rows)


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})  # key -> bytes
        self.deleted = []
        self.put = []

    # listing
    def get_paginator(self, _op):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "ETag": f'"{hash(v)}"'}
                                    for k, v in s3.objects.items() if k.startswith(Prefix)]}
        return P()

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()
        self.put.append(Key)

    def get_object(self, Bucket, Key, **_):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key]), "ContentLength": len(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)


def _rows(n):
    return [{"job_hash": f"h{i:03d}", "user_id": "u1"} for i in range(n)]


# --- resumes-are-distinct --------------------------------------------------

def test_distinct_fails_when_no_tex_objects_are_found(smoke, monkeypatch):
    monkeypatch.setattr(smoke, "_db", lambda: FakeDB(_rows(25)))
    monkeypatch.setattr(smoke, "_s3", lambda: FakeS3())
    smoke.resumes_are_distinct()
    name, ok, detail = smoke.RESULTS[-1]
    assert ok is False, detail
    assert "25" in detail


def test_distinct_skips_rather_than_passes_on_too_few_rows(smoke, monkeypatch):
    monkeypatch.setattr(smoke, "_db", lambda: FakeDB(_rows(5)))
    monkeypatch.setattr(smoke, "_s3", lambda: FakeS3())
    smoke.resumes_are_distinct()
    name, ok, detail = smoke.RESULTS[-1]
    assert ok is None, f"too few rows must be a SKIP, got {ok!r}: {detail}"


def test_distinct_passes_on_a_healthy_corpus(smoke, monkeypatch):
    objs = {f"users/u1/resumes/h{i:03d}_tailored.tex": f"doc {i}".encode() for i in range(25)}
    monkeypatch.setattr(smoke, "_db", lambda: FakeDB(_rows(25)))
    monkeypatch.setattr(smoke, "_s3", lambda: FakeS3(objs))
    smoke.resumes_are_distinct()
    assert smoke.RESULTS[-1][1] is True, smoke.RESULTS[-1]


def test_distinct_fails_on_identical_documents(smoke, monkeypatch):
    objs = {f"users/u1/resumes/h{i:03d}_tailored.tex": b"same" for i in range(25)}
    monkeypatch.setattr(smoke, "_db", lambda: FakeDB(_rows(25)))
    monkeypatch.setattr(smoke, "_s3", lambda: FakeS3(objs))
    smoke.resumes_are_distinct()
    assert smoke.RESULTS[-1][1] is False


# --- the summary line ------------------------------------------------------

def _run_main(smoke, monkeypatch, checks, argv=("smoke_prod.py",)):
    monkeypatch.setattr(smoke, "CHECKS", checks)
    monkeypatch.setattr(smoke, "_unregistered_checks", lambda: [])
    monkeypatch.setattr("sys.argv", list(argv))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = smoke.main()
    return rc, buf.getvalue()


def test_summary_never_counts_a_skip_as_a_pass(smoke, monkeypatch):
    @smoke.check("always-passes", "x")
    def a():
        return "fine"

    @smoke.check("judges-nothing", "y")
    def b():
        raise smoke.Skip("nothing to judge")

    rc, out = _run_main(smoke, monkeypatch, [a, b])
    assert "SKIP" in out and "judges-nothing" in out
    assert "1 passed" in out and "2 passed" not in out
    assert "1 SKIPPED" in out
    assert "NOT a full pass" in out
    assert rc == 0


def test_summary_with_everything_passing_says_so(smoke, monkeypatch):
    @smoke.check("always-passes", "x")
    def a():
        return "fine"

    rc, out = _run_main(smoke, monkeypatch, [a])
    assert rc == 0 and "1 passed" in out and "SKIPPED" not in out


def test_summary_fails_on_failure(smoke, monkeypatch):
    @smoke.check("always-fails", "x")
    def a():
        raise AssertionError("broken")

    rc, out = _run_main(smoke, monkeypatch, [a])
    assert rc == 1 and "1 FAILED" in out


# --- compile-roundtrip -----------------------------------------------------

class FakeCfn:
    def __init__(self):
        self.calls = []

    def describe_stack_resource(self, StackName, LogicalResourceId):
        self.calls.append((StackName, LogicalResourceId))
        return {"StackResourceDetail": {"PhysicalResourceId": "naukribaba-compile-latex"}}


class FakeLambda:
    def __init__(self, s3, *, write_pdf=b"%PDF-1.5 fake", result=None, function_error=None):
        self.s3, self.write_pdf, self.result, self.function_error = s3, write_pdf, result, function_error
        self.invocations = []

    def invoke(self, FunctionName, Qualifier, InvocationType, Payload):
        event = json.loads(Payload)
        self.invocations.append((FunctionName, Qualifier, InvocationType, event))
        assert event["tex_s3_key"] in self.s3.objects, "tex must be uploaded before invoking"
        pdf_key = event["tex_s3_key"].replace(".tex", ".pdf")
        if self.write_pdf is not None:
            self.s3.objects[pdf_key] = self.write_pdf
        body = self.result if self.result is not None else {"pdf_s3_key": pdf_key, "page_violations": []}
        resp = {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(body).encode())}
        if self.function_error:
            resp["FunctionError"] = self.function_error
        return resp


def _wire(smoke, monkeypatch, s3, lam, cfn=None):
    cfn = cfn or FakeCfn()
    monkeypatch.setattr(smoke, "_s3", lambda: s3)
    monkeypatch.setattr(smoke, "_lambda", lambda: lam)
    monkeypatch.setattr(smoke, "_cfn", lambda: cfn)
    return cfn


def test_compile_roundtrip_passes_and_cleans_up(smoke, monkeypatch):
    s3 = FakeS3()
    lam = FakeLambda(s3)
    cfn = _wire(smoke, monkeypatch, s3, lam)
    smoke.compile_roundtrip()
    name, ok, detail = smoke.RESULTS[-1]
    assert ok is True, detail
    assert cfn.calls == [("job-hunt-api", "CompileLatexFunction")]
    fn, qualifier, itype, event = lam.invocations[0]
    assert (fn, qualifier, itype) == ("naukribaba-compile-latex", "live", "RequestResponse")
    assert event["tex_s3_key"].startswith("smoke/") and event["tex_s3_key"].endswith(".tex")
    assert sorted(s3.deleted) == sorted([event["tex_s3_key"], event["tex_s3_key"].replace(".tex", ".pdf")])
    assert not [k for k in s3.objects if k.startswith("smoke/")]


def test_compile_roundtrip_fails_on_reported_error_and_still_cleans_up(smoke, monkeypatch):
    s3 = FakeS3()
    lam = FakeLambda(s3, write_pdf=None, result={"error": "compilation_failed", "stderr": "! Undefined"})
    _wire(smoke, monkeypatch, s3, lam)
    smoke.compile_roundtrip()
    name, ok, detail = smoke.RESULTS[-1]
    assert ok is False and "compilation_failed" in detail
    assert not [k for k in s3.objects if k.startswith("smoke/")]
    assert any(k.endswith(".tex") for k in s3.deleted)


def test_compile_roundtrip_fails_on_unhandled_lambda_error(smoke, monkeypatch):
    s3 = FakeS3()
    lam = FakeLambda(s3, write_pdf=None, result={"errorMessage": "boom"}, function_error="Unhandled")
    _wire(smoke, monkeypatch, s3, lam)
    smoke.compile_roundtrip()
    assert smoke.RESULTS[-1][1] is False


def test_compile_roundtrip_fails_on_empty_pdf(smoke, monkeypatch):
    s3 = FakeS3()
    lam = FakeLambda(s3, write_pdf=b"")
    _wire(smoke, monkeypatch, s3, lam)
    smoke.compile_roundtrip()
    name, ok, detail = smoke.RESULTS[-1]
    assert ok is False and "empty" in detail.lower()


def test_compile_roundtrip_fails_when_pdf_is_not_a_pdf(smoke, monkeypatch):
    s3 = FakeS3()
    lam = FakeLambda(s3, write_pdf=b"<html>error</html>")
    _wire(smoke, monkeypatch, s3, lam)
    smoke.compile_roundtrip()
    assert smoke.RESULTS[-1][1] is False


def test_compile_roundtrip_is_registered_and_skipped_by_quick(smoke):
    assert smoke.compile_roundtrip in smoke.CHECKS
    assert "compile_roundtrip" in smoke._QUICK_SKIPS
