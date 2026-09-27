"""API contract tests: response *shape* the frontend actually consumes.

Why this file exists: unit tests with mocked Supabase/S3 clients pass even
when a field the frontend reads has been renamed or dropped on the backend,
because the mock is written by the same person who (mis)remembers the
contract. Every test below either (a) parses the real frontend source to
discover which keys it reads/sends, then checks the backend actually
produces/accepts them, or (b) drives the real FastAPI endpoint through
TestClient with a fake DB/S3 and asserts the JSON that comes back still has
the fields the dashboard renders — so a rename on either side fails here
instead of silently emptying a card in production.

Covers, per the task brief: dashboard job list, job detail (+ artifacts),
search-config, score, health. Auth and DB are faked via app.py's own
extension points (`app_module._db`, `Depends(get_current_user)` override) —
no real Supabase/AWS credentials needed, no LLM calls.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = REPO_ROOT / "web" / "src"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# app.py reads these at import time (module-level .env loader) and at
# SupabaseClient.from_env() time — set safe dummies before import so the
# module imports cleanly with no real credentials, same pattern as
# tests/security/conftest.py.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "super-secret-jwt-key-for-testing-only-32chars!")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")


def _keys_after(text: str, anchor: str, varname: str, window: int = 400) -> set[str]:
    """Every `<varname>.<key>` accessed within `window` chars after `anchor`.

    Scopes a regex scan to just after one specific fetch call so reusing a
    generic variable name like `data` elsewhere in the same file (a second,
    unrelated endpoint call) doesn't bleed into the result.
    """
    idx = text.index(anchor)
    snippet = text[idx: idx + window]
    return set(re.findall(rf"\b{re.escape(varname)}\.([A-Za-z_][A-Za-z0-9_]*)", snippet))


def _keys_after_any(text: str, anchor: str, varname: str, window: int = 400) -> set[str]:
    """Union of `_keys_after` over every occurrence of `anchor` in `text`.

    Robust to which of several call sites (e.g. the same endpoint fetched
    from more than one place in a file) happens to carry the fields we care
    about, without hand-copying exact surrounding whitespace into the test.
    """
    keys: set[str] = set()
    start = 0
    while True:
        idx = text.find(anchor, start)
        if idx == -1:
            break
        keys |= set(re.findall(rf"\b{re.escape(varname)}\.([A-Za-z_][A-Za-z0-9_]*)", text[idx: idx + window]))
        start = idx + len(anchor)
    return keys


@pytest.fixture()
def app_module():
    """Import app.py once per test, restoring mutated globals afterward.

    app.py's endpoints read `_db` / `_resumes` / `_ai_client` / `_s3_client`
    as plain module globals (see app.py's own `_initialize_state`), so tests
    set them directly rather than trying to run the real FastAPI lifespan
    (which would need real Supabase/AWS/config.yaml resume files).
    """
    mod = importlib.import_module("app")
    saved = dict(
        _db=mod._db, _resumes=mod._resumes, _ai_client=mod._ai_client,
        _s3_client=getattr(mod, "_s3_client", None),
    )
    overrides_saved = dict(mod.app.dependency_overrides)
    yield mod
    mod._db = saved["_db"]
    mod._resumes = saved["_resumes"]
    mod._ai_client = saved["_ai_client"]
    mod._s3_client = saved["_s3_client"]
    mod.app.dependency_overrides.clear()
    mod.app.dependency_overrides.update(overrides_saved)


@pytest.fixture()
def client(app_module):
    from fastapi.testclient import TestClient
    return TestClient(app_module.app, raise_server_exceptions=False)


def _authed(app_module, user_id="user-1", email="u@example.com"):
    """Bypass Depends(get_current_user) with a fixed identity."""
    app_module.app.dependency_overrides[app_module.get_current_user] = (
        lambda: app_module.AuthUser(id=user_id, email=email)
    )


def _chained_table_mock(execute_result_data):
    """A MagicMock standing in for `db.client.table("jobs")...execute()`
    chains, terminating in an object with a `.data` attribute — mirrors
    tests/conftest.py's mock_supabase fixture but returns caller-supplied
    data instead of an empty list."""
    table = MagicMock()
    for method in ("select", "eq", "neq", "in_", "order", "limit", "maybe_single", "filter", "gte", "ilike"):
        getattr(table, method).return_value = table
    result = MagicMock()
    result.data = execute_result_data
    table.execute.return_value = result
    return table


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health_response_has_documented_shape(client, app_module):
    """Public, unauthenticated. Shape is hand-rolled (no response_model), so
    nothing else pins it — pin it here against app.py's own implementation."""
    app_module._resumes = {"sre_devops": "tex"}
    app_module._ai_client = MagicMock(providers=["groq", "deepseek"])

    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["resumes_loaded"], list)
    assert isinstance(data["ai_providers"], int)


# ---------------------------------------------------------------------------
# /api/dashboard/jobs (list) — Dashboard.jsx
# ---------------------------------------------------------------------------

DASHBOARD_JSX = WEB_SRC / "pages" / "Dashboard.jsx"


def test_dashboard_jsx_reads_jobs_and_total_from_job_list_response():
    """Ground truth for the assertion below: what Dashboard.jsx actually
    destructures from the /api/dashboard/jobs response, parsed from the
    real file rather than hand-copied."""
    text = DASHBOARD_JSX.read_text()
    keys = _keys_after(text, "await apiGet(`/api/dashboard/jobs?", "data")
    assert {"jobs", "total"} <= keys, (
        f"Dashboard.jsx's job-list fetch only appears to read {sorted(keys)} "
        "from `data` — update the anchor/window if the fetch call moved."
    )


def test_dashboard_jobs_endpoint_returns_jobs_and_total(client, app_module):
    """Runtime check: the real endpoint, with a faked DB, actually returns
    the top-level keys Dashboard.jsx reads (test above) — not just that the
    source code intends to."""
    _authed(app_module)
    fake_db = MagicMock()
    fake_db.get_jobs.return_value = ([{"job_id": "j1", "title": "Engineer", "company": "Acme"}], 1)
    app_module._db = fake_db

    resp = client.get("/api/dashboard/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["jobs"], list) and len(data["jobs"]) == 1
    assert data["total"] == 1
    assert "page" in data and "per_page" in data


def test_dashboard_jobs_endpoint_503s_without_db_not_empty_success(client, app_module):
    """Regression guard for the exact bug class named in its own docstring
    in app.py: DB not configured must surface as an error, not an empty
    'no jobs' success the frontend can't tell apart from a real zero-job
    account."""
    _authed(app_module)
    app_module._db = None

    resp = client.get("/api/dashboard/jobs")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /api/dashboard/jobs/{id} (detail) + artifacts (resume/cover-letter URLs)
# ---------------------------------------------------------------------------

JOB_TABLE_JSX = REPO_ROOT / "web" / "src" / "components" / "JobTable.jsx"

# Fields JobTable.jsx / JobWorkspace.jsx read directly off a job row (see
# tests/unit/test_authoritative_columns.py for the score/artifact subset of
# this list pinned against the *authoritative* column names specifically).
_JOB_ROW_FIELDS_FRONTEND_READS = [
    "job_id", "title", "company", "location", "match_score",
    "application_status", "is_expired", "source", "apply_url",
    "resume_s3_url", "cover_letter_s3_url",
]


def test_job_table_jsx_still_reads_these_fields():
    """Ground truth: fail loudly (here, not in prod) if JobTable.jsx stops
    reading one of these — otherwise the runtime test below could rot into
    asserting a passthrough nobody depends on."""
    text = JOB_TABLE_JSX.read_text()
    read = set(re.findall(r"\bjob\.([A-Za-z_][A-Za-z0-9_]*)", text))
    missing = [f for f in _JOB_ROW_FIELDS_FRONTEND_READS if f not in read]
    assert not missing, f"JobTable.jsx no longer reads job.{missing} — update this test's field list"


def test_dashboard_job_list_passes_through_every_field_the_table_reads(client, app_module):
    """The list endpoint calls _refresh_s3_urls() on every row before
    returning it — assert that pipeline doesn't drop or rename any field
    JobTable.jsx renders a column from."""
    _authed(app_module)
    fixture_row = {f: f"<{f}>" for f in _JOB_ROW_FIELDS_FRONTEND_READS}
    fixture_row["match_score"] = 72
    fixture_row["is_expired"] = False

    fake_db = MagicMock()
    fake_db.get_jobs.return_value = ([dict(fixture_row)], 1)
    app_module._db = fake_db
    app_module._s3_client = MagicMock()  # no *_s3_key set => _refresh_s3_urls leaves urls untouched

    resp = client.get("/api/dashboard/jobs")
    assert resp.status_code == 200
    row = resp.json()["jobs"][0]
    for field in _JOB_ROW_FIELDS_FRONTEND_READS:
        assert field in row, f"/api/dashboard/jobs dropped '{field}' — JobTable.jsx renders it"
    assert row["match_score"] == 72


def test_dashboard_job_detail_returns_single_row_with_frontend_fields(client, app_module):
    _authed(app_module)
    fixture_row = {f: f"<{f}>" for f in _JOB_ROW_FIELDS_FRONTEND_READS}
    app_module._db = MagicMock(client=MagicMock())
    app_module._db.client.table.return_value = _chained_table_mock(fixture_row)
    app_module._s3_client = MagicMock()

    resp = client.get("/api/dashboard/jobs/j1")
    assert resp.status_code == 200
    body = resp.json()
    for field in _JOB_ROW_FIELDS_FRONTEND_READS:
        assert field in body


def test_artifact_urls_are_actually_regenerated_from_s3_keys(client, app_module):
    """The 'artifacts' contract: when a job row carries an S3 *key*
    (resume_s3_key / cover_letter_s3_key), the dashboard endpoints must
    turn it into a working presigned *url* in the authoritative fields the
    frontend opens (JobTable.jsx / JobWorkspace.jsx read resume_s3_url /
    cover_letter_s3_url, never the raw key). This is the runtime half of
    tests/unit/test_authoritative_columns.py's static check."""
    _authed(app_module)
    fake_s3 = MagicMock()
    fake_s3.generate_presigned_url.return_value = "https://s3.example/presigned?sig=abc"
    app_module._s3_client = fake_s3

    row = {
        "job_id": "j1", "title": "t", "company": "c",
        "resume_s3_key": "users/u1/resumes/j1.pdf",
        "cover_letter_s3_key": "users/u1/cover_letters/j1.pdf",
    }
    fake_db = MagicMock()
    fake_db.get_jobs.return_value = ([row], 1)
    app_module._db = fake_db

    resp = client.get("/api/dashboard/jobs")
    assert resp.status_code == 200
    out = resp.json()["jobs"][0]
    assert out["resume_s3_url"] == "https://s3.example/presigned?sig=abc"
    assert out["cover_letter_s3_url"] == "https://s3.example/presigned?sig=abc"
    assert fake_s3.generate_presigned_url.call_count == 2


# ---------------------------------------------------------------------------
# /api/search-config
# ---------------------------------------------------------------------------

SETTINGS_JSX = REPO_ROOT / "web" / "src" / "pages" / "Settings.jsx"
ONBOARDING_JSX = REPO_ROOT / "web" / "src" / "pages" / "Onboarding.jsx"

# Mirror of every key the frontend ever sends via apiPut('/api/search-config', ...)
# across Settings.jsx's PreferencesSection (the `prefs` object) and
# JobSourcesSection, plus Onboarding.jsx's copy of the same `prefs` shape.
# If you add a field to either, add it here AND confirm app.py's _FIELD_MAP
# (update_search_config) maps it — otherwise it's silently dropped, exactly
# like the AddJob 422 incident this pattern is borrowed from
# (tests/contract/test_addjob_payload_validates.py).
SEARCH_CONFIG_PUT_KEYS = {
    "queries", "locations", "experience_levels", "days_back",
    "max_jobs_per_run", "min_match_score", "enabled_sources",
}


def test_frontend_search_config_payload_keys_are_still_accurate():
    """Sentinel: if Settings.jsx's `prefs` useState gains/loses a field, this
    hardcoded mirror must be updated too (same convention as
    test_addjob_payload_validates.py's ADDJOB_PAYLOAD)."""
    settings_text = SETTINGS_JSX.read_text()
    prefs_block_start = settings_text.index("const [prefs, setPrefs] = useState({")
    prefs_block_end = settings_text.index("})", prefs_block_start)
    prefs_block = settings_text[prefs_block_start:prefs_block_end]
    prefs_keys = set(re.findall(r"^\s*([a-z_]+):", prefs_block, re.MULTILINE))
    assert prefs_keys, "Could not parse Settings.jsx prefs useState — anchor may have moved"
    assert prefs_keys <= SEARCH_CONFIG_PUT_KEYS

    onboarding_text = ONBOARDING_JSX.read_text()
    assert "await apiPut('/api/search-config', prefs)" in onboarding_text, (
        "Onboarding.jsx no longer sends the same `prefs` shape to "
        "/api/search-config — re-check whether it now sends different keys."
    )

    assert "enabled_sources: enabledSources" in settings_text, (
        "Settings.jsx JobSourcesSection no longer sends enabled_sources by "
        "that name — update SEARCH_CONFIG_PUT_KEYS."
    )


def test_search_config_field_map_accepts_every_frontend_key():
    """Parse app.py's real _FIELD_MAP (update_search_config) via AST and
    assert every key the frontend actually sends maps to a DB column —
    a key missing from _FIELD_MAP is silently dropped (PUT succeeds, save
    does nothing), the same silent-failure shape as the incidents this
    whole test suite is written to catch."""
    import ast

    tree = ast.parse((REPO_ROOT / "app.py").read_text())
    field_map_keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_FIELD_MAP"
            and isinstance(node.value, ast.Dict)
        ):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    field_map_keys.add(k.value)
    assert field_map_keys, "_FIELD_MAP not found in app.py via AST — has update_search_config been refactored?"

    missing = SEARCH_CONFIG_PUT_KEYS - field_map_keys
    assert not missing, (
        f"app.py's _FIELD_MAP does not accept frontend search-config key(s) "
        f"{sorted(missing)} — PUT /api/search-config will silently drop them "
        "(the request 200s, but the field is never saved)."
    )


def test_search_config_put_persists_every_frontend_field(client, app_module):
    """Runtime check: PUT the exact prefs payload Settings.jsx sends, and
    verify upsert_search_config is actually called with all of it — not
    just that _FIELD_MAP statically lists the key (test above)."""
    _authed(app_module)
    captured = {}
    fake_db = MagicMock()
    fake_db.get_user.return_value = {"id": "user-1"}
    fake_db.upsert_search_config.side_effect = lambda user_id, clean: captured.update(clean) or clean
    app_module._db = fake_db

    payload = {
        "queries": ["python developer"], "locations": ["dublin"],
        "experience_levels": ["mid"], "days_back": 7,
        "max_jobs_per_run": 15, "min_match_score": 60,
    }
    resp = client.put("/api/search-config", json=payload)
    assert resp.status_code == 200
    for key in payload:
        assert key in captured, f"PUT /api/search-config dropped '{key}' before reaching the DB"


def test_search_config_get_returns_keys_settings_page_reads():
    text = SETTINGS_JSX.read_text()
    keys = _keys_after_any(text, "apiGet('/api/search-config')", "data", window=600)
    expected = {"queries", "locations", "experience_levels", "days_back", "max_jobs_per_run", "min_match_score"}
    assert expected <= keys, (
        f"Settings.jsx's search-config GET handler(s) only read {sorted(keys)} — "
        "expected preference fields missing, verify by hand and update this test."
    )


# ---------------------------------------------------------------------------
# /api/score — ScoreCard.jsx
# ---------------------------------------------------------------------------

SCORE_CARD_JSX = REPO_ROOT / "web" / "src" / "components" / "ScoreCard.jsx"


def test_score_card_jsx_reads_these_fields_from_score_response():
    text = SCORE_CARD_JSX.read_text()
    keys = set(re.findall(r"\bdata\.([A-Za-z_][A-Za-z0-9_]*)", text))
    expected = {"ats_score", "hiring_manager_score", "tech_recruiter_score", "avg_score", "matched_resume"}
    assert expected <= keys, f"ScoreCard.jsx changed its field reads: has {sorted(keys)}"


def test_score_response_model_declares_every_field_scorecard_reads():
    """Static: Pydantic model vs frontend usage — cheapest possible check,
    fails immediately on a field rename in ScoreResponse."""
    import app as app_module_static

    fields = set(app_module_static.ScoreResponse.model_fields.keys())
    text = SCORE_CARD_JSX.read_text()
    expected = set(re.findall(r"\bdata\.([A-Za-z_][A-Za-z0-9_]*)", text))
    missing = expected - fields
    assert not missing, f"ScoreResponse is missing field(s) {sorted(missing)} that ScoreCard.jsx reads"


def test_score_endpoint_response_has_scorecard_fields_at_runtime(client, app_module):
    """Runtime check with a stubbed match_jobs (no LLM call): the real
    /api/score handler's JSON must contain every field ScoreCard.jsx reads."""
    _authed(app_module)
    app_module._resumes = {"sre_devops": r"\documentclass{article}\begin{document}x\end{document}"}

    fake_job = app_module._Job("Software Engineer", "Acme", "x" * 30)
    fake_job.ats_score = 80
    fake_job.hiring_manager_score = 82
    fake_job.tech_recruiter_score = 78
    fake_job.match_score = 80
    fake_job.match_reasoning = "Strong match."
    fake_job.matched_resume = "sre_devops"

    app_module.match_jobs = lambda jobs, resumes, ai_client, min_score=0, batch_size=1: [fake_job]

    resp = client.post("/api/score", json={
        "job_description": "x" * 30, "job_title": "Software Engineer",
        "company": "Acme", "location": "Dublin", "apply_url": "", "resume_type": "sre_devops",
    })
    assert resp.status_code == 200
    data = resp.json()
    for field in ("ats_score", "hiring_manager_score", "tech_recruiter_score", "avg_score", "matched_resume"):
        assert field in data
    assert data["avg_score"] == 80
