"""Pins the authoritative `jobs` columns for score and artifacts.

The `jobs` table has accumulated multiple generations of near-duplicate
score/artifact columns (`match_score` vs `final_score`; `resume_s3_url` vs
`tailored_pdf_path` vs `resume_doc_url`; etc.) with nothing marking which one
is current. See docs/ROADMAP.md ("Which columns are authoritative") and
supabase/migrations/20260926150000_document_authoritative_job_columns.sql
for the full inventory and rationale.

This test does not hardcode "what the app uses" as a second list next to
"what is authoritative" — that would just compare two static lists that
happen to agree by construction and could never fail. Instead it parses the
real source files that write and read those columns (app.py's on-demand
artifact writer, and the dashboard's job-detail page) so that switching
either one to a legacy column breaks this test, the same class of mistake
the migration documents.
"""
import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_PY = REPO_ROOT / "app.py"
JOB_WORKSPACE = REPO_ROOT / "web/src/pages/JobWorkspace.jsx"

# The one hardcoded statement of "what is documented as authoritative" —
# matches docs/ROADMAP.md and the COMMENT ON COLUMN migration. Everything
# this file compares it against is parsed from the real source below, not
# from a second hand-maintained list.
AUTHORITATIVE_SCORE_COLUMN = "match_score"
AUTHORITATIVE_RESUME_ARTIFACT_COLUMN = "resume_s3_url"
AUTHORITATIVE_COVER_LETTER_ARTIFACT_COLUMN = "cover_letter_s3_url"

LEGACY_SCORE_COLUMNS = {"final_score", "score_status", "score_version", "scored_at"}
LEGACY_ARTIFACT_COLUMNS = {"tailored_pdf_path", "cover_letter_pdf_path", "resume_doc_url"}


def _update_job_artifacts_keys() -> set:
    """Every literal dict key ever passed to app.py's `_update_job_artifacts`.

    That helper is the single write path the on-demand (self-service)
    tailoring flow uses to persist a job's score/artifacts to Supabase
    (tailor, cover_letter and section-rebuild task handlers all funnel
    through it) — so its call sites are ground truth for "what the app
    writes", not a second hand-maintained list.
    """
    tree = ast.parse(APP_PY.read_text())
    keys: set = set()
    for node in ast.walk(tree):
        is_target_call = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_update_job_artifacts"
        )
        if not is_target_call:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                for k in arg.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    assert keys, "No _update_job_artifacts(..., {...}) call sites found in app.py"
    return keys


def _job_workspace_gate_column(tab_marker: str) -> str:
    """The `job.<column>` a job-detail tab gates its content on.

    JobWorkspace.jsx renders each tab's real content behind
    `{job.<column> ? (...) : (<empty state>)}` immediately after that tab's
    `activeTab === '<tab>'` block — i.e. the column that decides whether the
    dashboard thinks this job has that artifact.
    """
    src = JOB_WORKSPACE.read_text()
    anchor = src.index(tab_marker)
    window = src[anchor : anchor + 1000]
    match = re.search(r"job\.(\w+)\s*\?", window)
    assert match, f"No `job.<column> ? (` gate found after {tab_marker!r}"
    return match.group(1)


def _score_badge_column() -> str:
    """The column bound to the job-detail header's score badge."""
    src = JOB_WORKSPACE.read_text()
    match = re.search(r"<ScoreBadge\s+score=\{job\.(\w+)\}", src)
    assert match, "No <ScoreBadge score={job.<column>}> found in JobWorkspace.jsx"
    return match.group(1)


def test_update_job_artifacts_writes_only_authoritative_columns():
    keys = _update_job_artifacts_keys()

    assert AUTHORITATIVE_SCORE_COLUMN in keys
    assert AUTHORITATIVE_RESUME_ARTIFACT_COLUMN in keys
    assert AUTHORITATIVE_COVER_LETTER_ARTIFACT_COLUMN in keys

    written_legacy = keys & (LEGACY_SCORE_COLUMNS | LEGACY_ARTIFACT_COLUMNS)
    assert not written_legacy, (
        "app.py's on-demand tailoring flow writes legacy column(s) "
        f"{sorted(written_legacy)} instead of the authoritative "
        "match_score / resume_s3_url / cover_letter_s3_url."
    )


def test_dashboard_score_badge_reads_authoritative_column():
    assert _score_badge_column() == AUTHORITATIVE_SCORE_COLUMN


def test_dashboard_resume_tab_reads_authoritative_column():
    column = _job_workspace_gate_column("activeTab === 'resume'")
    assert column == AUTHORITATIVE_RESUME_ARTIFACT_COLUMN
    assert column not in LEGACY_ARTIFACT_COLUMNS


def test_dashboard_cover_letter_tab_reads_authoritative_column():
    column = _job_workspace_gate_column("activeTab === 'cover-letter'")
    assert column == AUTHORITATIVE_COVER_LETTER_ARTIFACT_COLUMN
    assert column not in LEGACY_ARTIFACT_COLUMNS
