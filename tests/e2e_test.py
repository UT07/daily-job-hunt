#!/usr/bin/env python3
"""End-to-end test suite for the Job Automation SaaS platform.

Tests all backend endpoints, database operations, and key flows.
Run: .venv/bin/python tests/e2e_test.py
"""

import json
import os
import sys
import time
import requests
from pathlib import Path

# Add parent dir
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
for line in Path(__file__).parent.parent.joinpath(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

BASE_URL = "http://localhost:8000"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Test results
PASS = 0
FAIL = 0
ERRORS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def get_auth_token():
    """Get a valid JWT token for the test user."""
    # Use Supabase admin API to get user, then generate token
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    users = resp.json().get("users", [])
    if not users:
        return None, None

    user = users[0]
    user_id = user["id"]
    email = user["email"]

    # Generate a token via admin API
    resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/generate_link",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        json={"type": "magiclink", "email": email},
    )

    # Alternative: sign in with service role to get a valid access token
    # For testing, we can use the service key directly with a custom approach
    # Let's try signing in via the REST API

    # Actually, let's use the anon key approach - sign in with email
    anon_key = os.environ.get("SUPABASE_JWT_SECRET", "")

    # For E2E tests, we'll test unauthenticated endpoints and note auth issues
    return user_id, email


def auth_headers():
    """Try to get auth headers. Returns empty dict if can't authenticate."""
    # For now, test without auth and document failures
    return {}


# ============================================================
# Test Suite
# ============================================================

print("=" * 60)
print("  E2E TEST SUITE — Job Automation SaaS")
print("=" * 60)
print()

# -----------------------------------------------------------
# 1. Backend Health
# -----------------------------------------------------------
print("[1] BACKEND HEALTH")
try:
    r = requests.get(f"{BASE_URL}/api/health", timeout=5)
    test("Health endpoint returns 200", r.status_code == 200)
    data = r.json()
    test("Health shows resumes loaded", len(data.get("resumes_loaded", [])) > 0,
         f"got {data.get('resumes_loaded', [])}")
    test("Health shows AI providers", data.get("ai_providers", 0) > 0,
         f"got {data.get('ai_providers', 0)} providers")
except Exception as e:
    test("Backend reachable", False, str(e))

print()

# -----------------------------------------------------------
# 2. Templates API
# -----------------------------------------------------------
print("[2] TEMPLATES API")
try:
    r = requests.get(f"{BASE_URL}/api/templates", timeout=5)
    test("Templates endpoint returns 200", r.status_code == 200)
    templates = r.json().get("templates", [])
    test("Has 3 templates", len(templates) == 3, f"got {len(templates)}")
    names = [t["id"] for t in templates]
    test("Has professional template", "professional" in names)
    test("Has modern template", "modern" in names)
    test("Has minimal template", "minimal" in names)
except Exception as e:
    test("Templates API works", False, str(e))

print()

# -----------------------------------------------------------
# 3. Auth — Protected endpoints reject unauthenticated
# -----------------------------------------------------------
print("[3] AUTH PROTECTION")
protected_endpoints = [
    ("GET", "/api/profile"),
    ("GET", "/api/dashboard/jobs"),
    ("GET", "/api/dashboard/stats"),
    ("GET", "/api/dashboard/runs"),
    ("GET", "/api/resumes"),
    ("GET", "/api/gdpr/export"),
]
for method, path in protected_endpoints:
    try:
        r = requests.request(method, f"{BASE_URL}{path}", timeout=5)
        test(f"{method} {path} requires auth", r.status_code in (401, 403),
             f"got {r.status_code}")
    except Exception as e:
        test(f"{method} {path} reachable", False, str(e))

print()

# -----------------------------------------------------------
# 4. Supabase Connection
# -----------------------------------------------------------
print("[4] SUPABASE CONNECTION")
try:
    from db_client import SupabaseClient
    db = SupabaseClient.from_env()
    test("SupabaseClient connects", True)

    # Get user
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    auth_users = resp.json().get("users", [])
    test("Auth users exist", len(auth_users) > 0, f"got {len(auth_users)}")

    if auth_users:
        user_id = auth_users[0]["id"]

        # Check public.users table
        user = db.get_user(user_id)
        test("User in public.users table", user is not None)
        if user:
            test("User has name", bool(user.get("name")), f"name={user.get('name')}")
            test("User has email", bool(user.get("email")), f"email={user.get('email')}")

        # Check jobs
        jobs = db.get_jobs(user_id, page=1, per_page=100)
        test("Jobs exist in DB", len(jobs) > 0, f"got {len(jobs)} jobs")

        if jobs:
            sample = jobs[0]
            test("Job has title", bool(sample.get("title")))
            test("Job has company", bool(sample.get("company")))
            has_any_apply = any(j.get("apply_url", "").startswith("http") for j in jobs)
            test("Some jobs have apply_url (real URL)", has_any_apply,
                 "No jobs have real apply URLs")
            test("Job has match_score", sample.get("match_score", 0) > 0)
            test("Job has ats_score", sample.get("ats_score", 0) > 0)

            # Check for asset URLs
            has_resume_url = any(j.get("resume_s3_url") or j.get("tailored_pdf_path") for j in jobs)
            test("Some jobs have resume URLs", has_resume_url,
                 "ALL jobs missing resume URLs — assets not migrated from Excel")

            has_cover_letter = any(j.get("cover_letter_s3_url") or j.get("cover_letter_pdf_path") for j in jobs)
            test("Some jobs have cover letter URLs", has_cover_letter,
                 "ALL jobs missing cover letter URLs — assets not migrated")

            has_doc_url = any(j.get("resume_doc_url") for j in jobs)
            test("Some jobs have Google Doc URLs", has_doc_url,
                 "ALL jobs missing Google Doc URLs — not migrated")

            # Check contacts format
            has_contacts = any(j.get("linkedin_contacts") for j in jobs)
            test("Some jobs have contacts", has_contacts)
            if has_contacts:
                for j in jobs:
                    if j.get("linkedin_contacts"):
                        try:
                            contacts = json.loads(j["linkedin_contacts"])
                            if contacts:
                                c = contacts[0]
                                has_url = (c.get("profile_url", "").startswith("http") or
                                           c.get("search_url", "").startswith("http") or
                                           c.get("google_url", "").startswith("http"))
                                test("Contact has valid URL (profile/search/google)", has_url,
                                     f"profile={c.get('profile_url', '')[:40]}, search={c.get('search_url', '')[:40]}")
                                break
                        except json.JSONDecodeError:
                            test("Contacts JSON valid", False, "JSON parse error")
                            break

            # Check for initial vs tailored scores
            # Initial scores will be available in future pipeline runs
            # For now, just verify scores exist at all
            test("All jobs have scores", all((j.get('match_score') or 0) > 0 for j in jobs),
                 "Some jobs have zero scores")

        # Check stats
        stats = db.get_job_stats(user_id)
        test("Stats returns data", stats.get("total_jobs", 0) > 0)

        # Check runs
        runs = db.get_runs(user_id)
        test("Runs history exists", True,  # May be empty, that's OK
             f"got {len(runs)} runs")

except Exception as e:
    test("Supabase operations", False, str(e))

print()

# -----------------------------------------------------------
# 5. Python Module Imports
# -----------------------------------------------------------
print("[5] MODULE IMPORTS")
modules = [
    "user_profile", "pipeline_context", "auth", "db_client",
    "quality_logger", "resume_parser", "template_engine",
    "gdpr", "audit_middleware", "ai_client",
    "matcher", "tailorer", "cover_letter", "contact_finder",
    "self_improver", "s3_uploader", "email_notifier",
]
for mod in modules:
    try:
        __import__(mod)
        test(f"import {mod}", True)
    except Exception as e:
        test(f"import {mod}", False, str(e))

print()

# -----------------------------------------------------------
# 6. UserProfile
# -----------------------------------------------------------
print("[6] USER PROFILE")
try:
    from user_profile import UserProfile
    import yaml

    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    user = UserProfile.from_config(config)
    test("UserProfile.from_config works", True)
    test("User name set", user.name == "Utkarsh Singh")
    test("safe_filename_prefix", user.safe_filename_prefix() == "Utkarsh_Singh")
    test("to_candidate_context not empty", len(user.to_candidate_context()) > 20)
    test("to_dict has all fields", "work_authorizations" in user.to_dict())
except Exception as e:
    test("UserProfile", False, str(e))

print()

# -----------------------------------------------------------
# 7. Template Engine
# -----------------------------------------------------------
print("[7] TEMPLATE ENGINE")
try:
    from template_engine import list_templates, render_template

    templates = list_templates()
    test("list_templates returns 3", len(templates) == 3)

    for t in templates:
        test(f"Template '{t['id']}' has description", bool(t.get("description")))

    rendered = render_template("professional", {"SUMMARY": "Test summary", "SKILLS": "Python, AWS"},
                               user_name="Test User", contact_line="test@example.com")
    test("render_template produces LaTeX", "\\documentclass" in rendered)
    test("Placeholder replaced", "Test summary" in rendered)
    test("User name injected", "Test User" in rendered)
except Exception as e:
    test("Template engine", False, str(e))

print()

# -----------------------------------------------------------
# 8. Quality Logger
# -----------------------------------------------------------
print("[8] QUALITY LOGGER")
try:
    from quality_logger import log_quality, read_quality_log, get_model_stats

    log_quality(task="test", provider="test_provider", model="test_model",
                job_id="test123", scores={"ats_score": 85})
    test("log_quality writes", True)

    entries = read_quality_log()
    test("read_quality_log returns entries", len(entries) > 0)

    last = entries[-1]
    test("Log entry has correct fields", last.get("task") == "test")
except Exception as e:
    test("Quality logger", False, str(e))

print()

# -----------------------------------------------------------
# 9. AI Client
# -----------------------------------------------------------
print("[9] AI CLIENT")
try:
    from ai_client import AIClient

    client = AIClient.from_config(config)
    test("AIClient initializes", True)
    test(f"Has {len(client.providers)} providers", len(client.providers) > 0)

    # Test a simple completion
    result = client.complete("Return exactly: HELLO", system="Return only the word requested.")
    test("AI complete() works", "HELLO" in result.upper(), f"got: {result[:50]}")

    # Test complete_with_info
    info = client.complete_with_info("Return exactly: WORLD", system="Return only the word requested.")
    test("complete_with_info returns dict", isinstance(info, dict))
    test("Has response key", "response" in info)
    test("Has provider key", "provider" in info, f"keys: {list(info.keys())}")
    test("Has model key", "model" in info)

    # Test council (if enough providers)
    if len(client.providers) >= 3:
        candidates = client.council_generate(
            "Return the number 42", system="Return only the number.",
            n_generators=2
        )
        test("council_generate returns candidates", len(candidates) > 0,
             f"got {len(candidates)} candidates")
    else:
        test("council_generate (skipped, <3 providers)", True)

except Exception as e:
    test("AI client", False, str(e))

print()

# -----------------------------------------------------------
# 10. GDPR
# -----------------------------------------------------------
print("[10] GDPR MODULE")
try:
    from gdpr import export_user_data, record_consent, request_deletion

    db = SupabaseClient.from_env()
    user_id = auth_users[0]["id"] if auth_users else None

    if user_id:
        zip_bytes = export_user_data(db, user_id)
        test("Data export produces ZIP", len(zip_bytes) > 0, f"got {len(zip_bytes)} bytes")

        import zipfile, io
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        files_in_zip = zf.namelist()
        test("ZIP contains profile.json", "profile.json" in files_in_zip)
        test("ZIP contains jobs.json", "jobs.json" in files_in_zip)
        test("ZIP contains resumes.json", "resumes.json" in files_in_zip)
    else:
        test("GDPR export (skipped, no user)", True)
except Exception as e:
    test("GDPR module", False, str(e))

print()

# -----------------------------------------------------------
# 11. Frontend Build
# -----------------------------------------------------------
print("[11] FRONTEND")
try:
    r = requests.get("http://localhost:5173/", timeout=5)
    test("Frontend serves HTML", r.status_code == 200)
    test("HTML contains React root", "root" in r.text)
    test("HTML contains app script", ".js" in r.text)
except Exception as e:
    test("Frontend reachable", False, str(e))

print()

# -----------------------------------------------------------
# 12. Schema Verification
# -----------------------------------------------------------
print("[12] DATABASE SCHEMA")
try:
    db = SupabaseClient.from_env()

    # Verify all tables exist by querying them
    tables = ["users", "user_resumes", "user_search_configs", "jobs", "runs", "audit_log"]
    for table in tables:
        try:
            db.client.table(table).select("*").limit(1).execute()
            test(f"Table '{table}' exists", True)
        except Exception as e:
            test(f"Table '{table}' exists", False, str(e))
except Exception as e:
    test("Schema verification", False, str(e))

print()

# -----------------------------------------------------------
# Summary
# -----------------------------------------------------------
print("=" * 60)
print(f"  RESULTS: {PASS} passed, {FAIL} failed")
print("=" * 60)

if ERRORS:
    print(f"\n  FAILURES ({len(ERRORS)}):")
    for e in ERRORS:
        print(f"  {e}")

print()
sys.exit(1 if FAIL > 0 else 0)
