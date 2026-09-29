"""Regenerating a resume ran the whole pipeline, including work nobody asked for.

Measured on a real single-job run, 2026-09-29:

    TailorResume         448.8s   <- requested
    CompileResume          5.0s   <- requested
    GenerateCoverLetter   81.5s   <- not requested
    CompileCoverLetter     4.0s   <- not requested
    FindContacts          91.3s   <- not requested
    SaveJobComplete        0.5s
    TOTAL                631.1s

176s — 28% of a ten and a half minute wait — regenerating artifacts the user
did not ask for, and overwriting a cover letter they may have already edited.

The UI knew. JobWorkspace's handleRegen(type) is called with 'resume' or
'cover', uses it for the loading spinner, and then POSTs an empty body. The
backend therefore could not tell the difference and the state machine ran
CompileResume -> GenerateCoverLetter unconditionally.

Same shape as the other defects found today: the information existed at the
boundary and was discarded one layer before it was needed.
"""
import json
import pathlib
import re

TEMPLATE = pathlib.Path("template.yaml").read_text()
APP = pathlib.Path("app.py").read_text()
WORKSPACE = pathlib.Path("web/src/pages/JobWorkspace.jsx").read_text()


def _single_job_states() -> dict:
    """The SingleJobPipeline ASL, parsed out of the CFN template."""
    i = TEMPLATE.index("SingleJobPipelineStateMachine:")
    seg = TEMPLATE[i:i + 20000]
    start = seg.index('"States": {')
    depth, j = 0, start + len('"States": ') - 1
    for k in range(j, len(seg)):
        if seg[k] == "{":
            depth += 1
        elif seg[k] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(seg[j:k + 1])
    raise AssertionError("could not parse the States block")


def test_the_ui_sends_the_scope_it_already_knows():
    body = re.search(r"apiCall\(`/api/pipeline/re-tailor/\$\{job\.job_id\}`,\s*(\{[^)]*\})\)", WORKSPACE)
    assert body, "could not find the re-tailor call"
    assert body.group(1).strip() != "{}", (
        "handleRegen(type) still POSTs an empty body — the backend cannot tell "
        "a resume-only regenerate from a full one"
    )
    assert "scope" in body.group(1)


def test_the_endpoint_accepts_a_scope():
    i = APP.index('@app.post("/api/pipeline/re-tailor/{job_id}"')
    fn = APP[i:i + 3000]
    assert "scope" in fn, "re_tailor_job ignores the requested scope"
    assert "resume_only" in fn, "the scope is never translated into state machine input"


def test_the_state_machine_can_skip_the_cover_letter():
    states = _single_job_states()
    nxt = states["CompileResume"].get("Next")
    assert nxt != "GenerateCoverLetter", (
        "CompileResume still goes straight to GenerateCoverLetter — a resume-only "
        "regenerate cannot avoid 176s of unrequested work"
    )
    assert states[nxt]["Type"] == "Choice", f"expected a Choice after CompileResume, got {nxt}"


def test_the_skip_path_still_reaches_the_save_step():
    """Skipping the cover letter must not skip persisting the resume."""
    states = _single_job_states()
    choice = states[states["CompileResume"]["Next"]]
    targets = [c["Next"] for c in choice["Choices"]] + [choice["Default"]]
    assert any("Save" in t for t in targets), (
        f"no Save state reachable from the scope choice: {targets}"
    )


def test_the_default_is_still_the_full_pipeline():
    """An old client that sends no scope must behave exactly as before."""
    states = _single_job_states()
    choice = states[states["CompileResume"]["Next"]]
    assert choice["Default"] == "GenerateCoverLetter", (
        "dropping the cover letter by default would silently stop generating "
        "them for the daily pipeline"
    )
