"""User-influenced LaTeX must not be able to read (or write) files at compile time.

Audited 2026-10-08. tectonic will `\\input{/abs/path}` and only warn about it.
Measured locally with tectonic 0.15: a document containing
`X\\input{/tmp/.../secret.txt}Y` compiled with exit 0 and the PDF text read
"XTOPSECRETVALUE Y" -- with AND without `--untrusted`, which disables shell
escape but not file reads. Two routes carry attacker LaTeX to a compile: a
`.tex` résumé upload (its preamble survives tailoring) and model output.

So the control is a source check in shared/latex_safety.py, applied at upload
(400) and immediately before every compile (refuse). These tests cover the
detector, the places that must call it, and a real tectonic run.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
from unittest.mock import patch

import pytest

from shared.latex_safety import UnsafeLatex, assert_safe_latex, find_unsafe_latex

REPO = pathlib.Path(__file__).resolve().parents[2]

DOC = "\\documentclass{article}\n\\begin{document}\n%s\n\\end{document}\n"

ATTACKS = [
    r"\input{/etc/hosts}",
    r"\input /etc/hosts",
    r"\include{../../secrets}",
    r"\InputIfFileExists{/etc/hosts}{}{}",
    r"\IfFileExists{/etc/hosts}{yes}{no}",
    r"\newread\r \openin\r=/etc/hosts \read\r to\x \x",
    r"\readline\r to \x",
    r"\newwrite\w \immediate\openout\w=pwn.tex \immediate\write\w{x}",
    r"\write18{id}",
    r"\catcode`\|=0 |input{/etc/hosts}",
    r"\directlua{io.open('/etc/hosts')}",
    r"\usepackage{/tmp/evil}",
    r"\usepackage{../evil}",
    r"\RequirePackage{/tmp/evil}",
    r"\documentclass{/tmp/evil}",
    r"\lstinputlisting{/etc/hosts}",
    r"\verbatiminput{/etc/hosts}",
    r"\VerbatimInput{/etc/hosts}",
    r"\includegraphics{/etc/hosts}",
    r"\includegraphics[width=1in]{../../x.png}",
    r"\graphicspath{{/etc/}}",
    r"\csname input\endcsname{/etc/hosts}",
    r"\UseName{input}{/etc/hosts}",
    r"\makeatletter\@@input /etc/hosts",
    r"\makeatletter\@nameuse{input}",
    r"\input@path{{/etc/}}",
    r"^^5cinput{/etc/hosts}",
    r"\scantokens{\input{/etc/hosts}}",
    r"\ExplSyntaxOn \file_input:n {/etc/hosts}",
    r"\begin{filecontents}{x.tex}hi\end{filecontents}",
    r"\XeTeXpdffile \"/etc/hosts\"",
    r"\pdffiledump length 100 {/etc/hosts}",
    r"\special{pdf:image (/etc/hosts)}",
    r"\write@x",  # without \makeatletter TeX reads this as \write followed by @x
    r"\input@x",
]


@pytest.mark.parametrize("payload", ATTACKS)
def test_each_file_or_io_primitive_is_rejected(payload):
    assert find_unsafe_latex(DOC % payload), payload


@pytest.mark.parametrize("payload", ATTACKS)
def test_assert_raises_with_the_reason(payload):
    with pytest.raises(UnsafeLatex) as exc:
        assert_safe_latex(DOC % payload)
    assert exc.value.violations


@pytest.mark.parametrize("ok", [
    r"\usepackage[utf8]{inputenc}",
    r"\usepackage[T1]{fontenc}",
    r"\usepackage{hyperref}\href{https://example.com/a/../b}{link}",
    r"\inputencoding{utf8}",
    r"\textbf{Read} the \textit{write}-up; opened 3 files",
    r"\includegraphics[width=1in]{logo.png}",
    r"\newcommand{\jobentry}[3]{\textbf{#1} -- #2 \hfill \textit{#3}}",
    r"C\# and 100\% and \& and \$5",
    r"\makeatletter\renewcommand\@seccntformat[1]{}\makeatother",
])
def test_ordinary_resume_latex_is_not_rejected(ok):
    assert find_unsafe_latex(DOC % ok) == [], ok


def _real_tex_files() -> list[pathlib.Path]:
    skip = {"node_modules", ".git", ".venv"}
    return sorted(p for p in REPO.rglob("*.tex") if not skip & set(p.parts))


def test_no_real_tex_document_in_the_repo_is_rejected():
    """CLAUDE.md #16: measure the false-positive rate before shipping.
    0 is the only acceptable figure -- a rejected résumé cannot be compiled."""
    files = _real_tex_files()
    assert len(files) >= 9, "the population shrank; this check would pass vacuously"
    flagged = {str(p.relative_to(REPO)): find_unsafe_latex(p.read_text(errors="ignore")) for p in files}
    flagged = {k: v for k, v in flagged.items() if v}
    assert flagged == {}


# ---------------------------------------------------------------------------
# Every compile path refuses unsafe source
# ---------------------------------------------------------------------------


FULL = "".join(f"\\section{{{h}}} text\n" for h in ("Summary", "Skills", "Experience", "Projects", "Education"))


@pytest.mark.parametrize("payload, reaches_engine", [
    ("", True),  # soundness: a complete, safe resume gets past every other gate
    (r"\input{/etc/hosts}", False),
])
def test_latex_compiler_refuses_before_running_any_engine(tmp_path, payload, reaches_engine):
    import latex_compiler

    tex = tmp_path / "resume.tex"
    tex.write_text(DOC % (FULL + payload))
    with patch.object(latex_compiler, "_compile_work_copy", return_value="") as engine:
        pdf, _violations = latex_compiler.compile_tex_to_pdf_with_report(str(tex), str(tmp_path))
    assert engine.called is reaches_engine
    assert pdf == ""


def test_latex_compiler_tectonic_runs_untrusted(tmp_path):
    import latex_compiler

    tex = tmp_path / "a.tex"
    tex.write_text(DOC % "hi")
    with patch.object(latex_compiler.subprocess, "run") as run:
        latex_compiler._compile_with_tectonic(tex, tmp_path)
    assert "--untrusted" in run.call_args.args[0]


def test_latex_compiler_engine_wrappers_refuse_unsafe_source_themselves(tmp_path):
    """Defence in depth: the wrappers are callable directly, so they check too."""
    import latex_compiler

    tex = tmp_path / "a.tex"
    tex.write_text(DOC % r"\input{/etc/hosts}")
    with patch.object(latex_compiler.subprocess, "run") as run:
        assert latex_compiler._compile_with_tectonic(tex, tmp_path) == ""
        assert latex_compiler._compile_with_pdflatex(tex, tmp_path) == ""
    run.assert_not_called()


class _S3:
    def __init__(self, body: str):
        self.body = body
        self.puts = []

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.body.encode())}

    def put_object(self, **kw):
        self.puts.append(kw)


def test_pipeline_compile_lambda_refuses_and_says_why():
    import compile_latex

    s3 = _S3(DOC % r"\input{/etc/hosts}")
    with patch.object(compile_latex.boto3, "client", return_value=s3), \
            patch.object(compile_latex.subprocess, "run") as run:
        out = compile_latex.handler({"tex_s3_key": "u/j/resume.tex", "job_hash": "j", "user_id": "u"}, None)
    run.assert_not_called()
    assert s3.puts == []
    assert out["error"] == "unsafe_latex"
    assert out["violations"]


def test_pipeline_compile_lambda_runs_tectonic_untrusted():
    import compile_latex

    s3 = _S3(DOC % "hi")
    with patch.object(compile_latex.boto3, "client", return_value=s3), \
            patch.object(compile_latex.subprocess, "run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "boom"
        compile_latex.handler({"tex_s3_key": "u/j/resume.tex", "job_hash": "j", "user_id": "u"}, None)
    assert run.called, "the double is unsound: a safe document never reached the engine"
    assert all("--untrusted" in c.args[0] for c in run.call_args_list)


def test_every_tectonic_invocation_in_the_pipeline_lambda_is_guarded():
    """CLAUDE.md #10: three call sites run tectonic in compile_latex.py; a
    guard wired into one of them is the pick_latest_tailorable mistake."""
    src = (REPO / "lambdas" / "pipeline" / "compile_latex.py").read_text()
    assert src.count("subprocess.run(") == 1, "call tectonic only through _run_tectonic"
    runner = src[src.index("def _run_tectonic"):src.index("def handler")]
    assert "subprocess.run(" in runner and "assert_safe_latex(" in runner


# ---------------------------------------------------------------------------
# The real engine (CLAUDE.md #5)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
def test_against_real_tectonic_the_file_never_reaches_the_pdf(tmp_path):
    """Control first: prove the attack works on the bare engine, then that the
    guarded compiler stops it."""
    import pdfplumber

    import latex_compiler

    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRETVALUE\n")
    body = DOC % (f"X\\input{{{secret}}}Y")

    bare = tmp_path / "bare.tex"
    bare.write_text(body)
    subprocess.run(["tectonic", "-X", "compile", "--untrusted", str(bare)],
                   capture_output=True, timeout=120, cwd=tmp_path)
    leaked = pdfplumber.open(tmp_path / "bare.pdf").pages[0].extract_text()
    assert "TOPSECRETVALUE" in leaked, "control failed: the bare engine did not read the file"

    out = tmp_path / "out"
    out.mkdir()
    # Positive control through the SAME path: a safe complete resume compiles
    # (with --untrusted). Without it a missing outdir or a broken engine would
    # make the refusal below look like a pass -- which happened while writing
    # this test.
    ok = tmp_path / "ok_resume.tex"
    ok.write_text(DOC % FULL)
    assert latex_compiler.compile_tex_to_pdf(str(ok), str(out)), "control: a safe resume did not compile"

    guarded = tmp_path / "guarded_resume.tex"
    guarded.write_text(DOC % (FULL + f"X\\input{{{secret}}}Y"))
    assert latex_compiler.compile_tex_to_pdf(str(guarded), str(out)) == ""
    assert not (out / "guarded_resume.pdf").exists()


# ---------------------------------------------------------------------------
# Upload rejects at the door
# ---------------------------------------------------------------------------


@pytest.fixture
def upload_client(monkeypatch):
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    import app as app_module
    import resume_parser
    from auth import AuthUser, get_current_user

    db = MagicMock()
    db.upsert_resume.return_value = {"id": "r1"}
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_posthog", None)
    monkeypatch.setattr(resume_parser, "parse_resume_sections", lambda text, ai_client=None: {})
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id="user-1", email="u@x.com")
    yield TestClient(app_module.app), db, resume_parser
    app_module.app.dependency_overrides.clear()


SAFE_TEX = DOC % r"\section{Experience} Built things."


def test_upload_of_safe_tex_is_stored(upload_client):
    """Soundness of the double: the happy path really reaches the database."""
    client, db, _ = upload_client
    r = client.post("/api/resumes/upload", files={"file": ("cv.tex", SAFE_TEX.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    db.upsert_resume.assert_called_once()


def test_upload_of_tex_that_reads_files_is_rejected_with_400(upload_client, monkeypatch):
    client, db, resume_parser = upload_client
    parsed = []
    monkeypatch.setattr(resume_parser, "parse_resume_sections",
                        lambda text, ai_client=None: parsed.append(text) or {})
    bad = DOC % r"\input{/etc/passwd}"
    r = client.post("/api/resumes/upload", files={"file": ("cv.tex", bad.encode(), "text/plain")})
    assert r.status_code == 400
    assert "input" in r.text
    db.upsert_resume.assert_not_called()
    # Refused at the door, before the parser (which can spend a model call) runs.
    assert parsed == []


def test_a_pdf_whose_text_is_unsafe_latex_is_not_stored_as_latex(upload_client, monkeypatch):
    """A PDF whose extracted text is a LaTeX document is stored as tex_content
    and compiled later; the check must cover that path too."""
    client, db, resume_parser = upload_client
    monkeypatch.setattr(resume_parser, "extract_text_from_pdf",
                        lambda _b: DOC % r"\section{Experience} \input{/etc/passwd}")
    r = client.post("/api/resumes/upload", files={"file": ("cv.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400, r.text
    db.upsert_resume.assert_not_called()
