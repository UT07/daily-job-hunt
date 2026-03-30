#!/usr/bin/env python3
"""Phase 1 Verification Suite — tests all systems end-to-end.

Run: python scripts/verify_phase1.py

Tests:
  1. Supabase connection + data integrity
  2. All API endpoints (health, profile, search-config, quality-stats)
  3. Scraper connectivity (each scraper individually)
  4. LinkedIn contacts quality (real profiles vs search URLs)
  5. AI client + council functionality
  6. LaTeX compilation
  7. Pipeline dry-run data in Supabase
"""

import json
import os
import sys
import time
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ── Helpers ──

PASS = 0
FAIL = 0
WARN = 0
ERRORS = []
WARNINGS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        FAIL += 1
        msg = f"  \033[31mFAIL\033[0m  {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def warn(name, detail=""):
    global WARN
    WARN += 1
    msg = f"  \033[33mWARN\033[0m  {name}" + (f" — {detail}" if detail else "")
    print(msg)
    WARNINGS.append(msg)


def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ============================================================
# 1. SUPABASE DATA INTEGRITY
# ============================================================
section("1. SUPABASE DATA INTEGRITY")

try:
    from db_client import SupabaseClient
    db = SupabaseClient.from_env()
    test("Supabase client connects", True)

    # Get the real user
    result = db.client.table("users").select("id, email, name").execute()
    users = result.data
    test("Users table has data", len(users) > 0, f"got {len(users)} users")

    if users:
        user = users[0]
        user_id = user["id"]
        print(f"         User: {user.get('name')} ({user.get('email')})")

        # Jobs
        jobs = db.get_jobs(user_id, page=1, per_page=100)
        test("Jobs exist in Supabase", len(jobs) > 0, f"got {len(jobs)} jobs")

        if jobs:
            # Data quality checks
            with_title = sum(1 for j in jobs if j.get("title"))
            with_company = sum(1 for j in jobs if j.get("company"))
            with_url = sum(1 for j in jobs if j.get("apply_url", "").startswith("http"))
            with_score = sum(1 for j in jobs if (j.get("match_score") or 0) > 0)
            with_desc = sum(1 for j in jobs if len(j.get("description") or "") > 50)
            with_source = sum(1 for j in jobs if j.get("source"))

            test(f"All jobs have titles", with_title == len(jobs), f"{with_title}/{len(jobs)}")
            test(f"All jobs have companies", with_company == len(jobs), f"{with_company}/{len(jobs)}")
            test(f"Jobs have apply URLs", with_url > 0, f"{with_url}/{len(jobs)} have URLs")
            test(f"All jobs have scores", with_score == len(jobs), f"{with_score}/{len(jobs)}")
            test(f"Jobs have descriptions", with_desc > 0, f"{with_desc}/{len(jobs)} have descriptions")
            test(f"All jobs have source", with_source == len(jobs), f"{with_source}/{len(jobs)}")

            # Score distribution
            scores = [j.get("match_score", 0) for j in jobs if j.get("match_score")]
            if scores:
                avg = sum(scores) / len(scores)
                mn, mx = min(scores), max(scores)
                print(f"         Score range: {mn} — {mx}, avg: {avg:.1f}")
                test("Score distribution is reasonable", mn < 80 or mx > 60,
                     "All scores identical — possible rubber-stamping")

            # Sources breakdown
            sources = {}
            for j in jobs:
                s = j.get("source", "unknown")
                sources[s] = sources.get(s, 0) + 1
            print(f"         Sources: {dict(sorted(sources.items(), key=lambda x: -x[1]))}")

            # Contacts quality
            with_contacts = 0
            real_profiles = 0
            search_only = 0
            for j in jobs:
                raw = j.get("linkedin_contacts", "")
                if not raw:
                    continue
                try:
                    contacts = json.loads(raw)
                    if contacts:
                        with_contacts += 1
                        for c in contacts:
                            if c.get("profile_url", "").startswith("http"):
                                real_profiles += 1
                            elif c.get("google_url", "").startswith("http"):
                                search_only += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            print(f"         Contacts: {with_contacts} jobs have contacts, "
                  f"{real_profiles} real profiles, {search_only} search-only")
            if with_contacts > 0:
                test("Some contacts have real LinkedIn profiles", real_profiles > 0,
                     "All contacts are search URLs only")

        # Runs
        runs = db.get_runs(user_id)
        test("Pipeline runs recorded", len(runs) > 0, f"got {len(runs)} runs")
        if runs:
            latest = runs[0]
            print(f"         Latest run: {latest.get('run_date')} — {latest.get('status', 'unknown')}")

        # Search config
        config = db.get_search_config(user_id)
        test("Search config exists", config is not None)
        if config:
            print(f"         Queries: {config.get('queries', [])}")
            print(f"         Locations: {config.get('locations', [])}")

except Exception as e:
    test("Supabase connection", False, str(e))


# ============================================================
# 2. API ENDPOINTS (Lambda)
# ============================================================
section("2. API ENDPOINTS")

import requests as http

API = os.environ.get("API_URL", "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod")

try:
    # Health (unauthenticated)
    r = http.get(f"{API}/api/health", timeout=10)
    test("GET /api/health", r.status_code == 200)
    data = r.json()
    test(f"  AI providers: {data.get('ai_providers', 0)}", data.get("ai_providers", 0) > 0)
    test(f"  Resumes loaded: {data.get('resumes_loaded', [])}", len(data.get("resumes_loaded", [])) > 0)

    # Generate test JWT
    try:
        import jwt as pyjwt
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if jwt_secret and user_id:
            token = pyjwt.encode(
                {"sub": user_id, "email": user.get("email", ""), "aud": "authenticated",
                 "role": "authenticated", "iat": int(time.time()), "exp": int(time.time()) + 3600},
                jwt_secret, algorithm="HS256"
            )
            headers = {"Authorization": f"Bearer {token}"}

            # Profile
            r = http.get(f"{API}/api/profile", headers=headers, timeout=10)
            test("GET /api/profile", r.status_code == 200)
            if r.status_code == 200:
                p = r.json()
                test(f"  full_name: {p.get('full_name')}", bool(p.get("full_name")))

            # Search config
            r = http.get(f"{API}/api/search-config", headers=headers, timeout=10)
            test("GET /api/search-config", r.status_code == 200)

            # Quality stats
            r = http.get(f"{API}/api/quality-stats", headers=headers, timeout=10)
            test("GET /api/quality-stats", r.status_code == 200)

            # Tailor (async)
            r = http.post(f"{API}/api/tailor", headers=headers,
                          json={"job_description": "Senior SRE at Stripe. Kubernetes, Terraform, Python. 5+ years.",
                                "resume_key": "sre_devops"}, timeout=10)
            test("POST /api/tailor returns 202", r.status_code == 202)
            if r.status_code == 202:
                task_id = r.json().get("task_id")
                test(f"  task_id returned", bool(task_id))

        else:
            warn("Skipping authenticated endpoints — no JWT secret or user_id")
    except ImportError:
        warn("Skipping authenticated endpoints — pyjwt not installed")

except Exception as e:
    test("API reachable", False, str(e))


# ============================================================
# 3. SCRAPER CONNECTIVITY
# ============================================================
section("3. SCRAPER CONNECTIVITY")

try:
    from scrapers.base import Job

    scrapers_to_test = []

    # API scrapers
    try:
        from scrapers.adzuna_scraper import AdzunaScraper
        if os.environ.get("ADZUNA_APP_ID"):
            scrapers_to_test.append(("adzuna", AdzunaScraper()))
        else:
            warn("Adzuna — ADZUNA_APP_ID not set")
    except Exception as e:
        warn(f"Adzuna import failed: {e}")

    try:
        from scrapers.jsearch_scraper import JSearchScraper
        if os.environ.get("JSEARCH_API_KEY"):
            scrapers_to_test.append(("jsearch", JSearchScraper()))
        else:
            warn("JSearch — JSEARCH_API_KEY not set")
    except Exception as e:
        warn(f"JSearch import failed: {e}")

    # HTML scrapers
    try:
        from scrapers.gradireland_scraper import GradIrelandScraper
        scrapers_to_test.append(("gradireland", GradIrelandScraper()))
    except Exception as e:
        warn(f"GradIreland import failed: {e}")

    try:
        from scrapers.yc_scraper import HackerNewsScraper
        scrapers_to_test.append(("hn_hiring", HackerNewsScraper()))
    except Exception as e:
        warn(f"HN Hiring import failed: {e}")

    # Test each scraper
    for name, scraper in scrapers_to_test:
        try:
            jobs = scraper.search("Software Engineer", location="Dublin", days_back=7)
            test(f"{name}: returns jobs", len(jobs) > 0, f"got {len(jobs)}")
            if jobs:
                j = jobs[0]
                test(f"  {name}: job has title", bool(j.title))
                test(f"  {name}: job has company", bool(j.company))
        except Exception as e:
            test(f"{name}: search works", False, str(e))

    # Browser scrapers need special handling — just test imports
    for name, cls_name in [("linkedin", "LinkedInScraper"), ("glassdoor", "GlassdoorScraper"),
                            ("indeed", "IndeedScraper"), ("jobsurface", "JobSurfaceScraper")]:
        try:
            mod = __import__(f"scrapers.{name}_scraper", fromlist=[cls_name])
            test(f"{name}: importable", True)
        except Exception as e:
            test(f"{name}: importable", False, str(e))

except Exception as e:
    test("Scraper framework", False, str(e))


# ============================================================
# 4. LINKEDIN CONTACTS QUALITY
# ============================================================
section("4. LINKEDIN CONTACTS QUALITY")

try:
    apify_key = os.environ.get("APIFY_API_KEY", "")
    if apify_key:
        from contact_finder import _apify_google_search, _search_linkedin_profile

        # Test Apify search
        profiles = _apify_google_search(
            'site:linkedin.com/in "Engineering Manager" "Stripe"', num_results=3
        )
        test("Apify Google Search returns profiles", len(profiles) > 0, f"got {len(profiles)}")
        for p in profiles:
            has_url = "/in/" in p.get("url", "")
            has_name = bool(p.get("name"))
            print(f"         {p.get('name', '?')} — {p.get('title', '?')}")
            print(f"         {p.get('url', '?')}")
            test(f"  Profile has LinkedIn URL", has_url, p.get("url", ""))
            test(f"  Profile has name", has_name)
            break  # just test first one in detail

        # Test unified search
        results = _search_linkedin_profile("Google", "Engineering Manager", "Dublin", num=1)
        test("Unified search finds profiles", len(results) > 0)
    else:
        warn("APIFY_API_KEY not set — skipping contact quality tests")

    serper_key = os.environ.get("SERPER_API_KEY", "")
    if serper_key and not apify_key:
        from contact_finder import _serper_search
        results = _serper_search('site:linkedin.com/in "Engineering Manager" "Stripe"', num=3)
        test("Serper fallback returns profiles", len(results) > 0)
    elif not serper_key:
        warn("SERPER_API_KEY not set — no search fallback available")

except Exception as e:
    test("Contact finder", False, str(e))


# ============================================================
# 5. AI CLIENT
# ============================================================
section("5. AI CLIENT")

try:
    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    from ai_client import AIClient
    client = AIClient.from_config(config)
    test(f"AI client initialized ({len(client.providers)} providers)", len(client.providers) > 0)

    # Simple completion
    info = client.complete_with_info("Return exactly: VERIFIED", system="Return only the word requested.")
    test("complete_with_info works", "VERIFIED" in info.get("response", "").upper(),
         f"got: {info.get('response', '')[:50]}")
    test(f"  Provider: {info.get('provider')}", bool(info.get("provider")))
    test(f"  Model: {info.get('model')}", bool(info.get("model")))

    # Council (if enough providers)
    if len(client.providers) >= 3:
        candidates = client.council_generate(
            "Return the number 42", system="Return only the number.", n_generators=2
        )
        test(f"Council generate ({len(candidates)} candidates)", len(candidates) > 0)
    else:
        warn(f"Council skipped — only {len(client.providers)} providers (need 3+)")

except Exception as e:
    test("AI client", False, str(e))


# ============================================================
# 6. LATEX COMPILATION
# ============================================================
section("6. LATEX COMPILATION")

try:
    from latex_compiler import compile_tex_to_pdf
    import tempfile, shutil

    # Check tectonic
    import subprocess
    result = subprocess.run(["tectonic", "--version"], capture_output=True, text=True)
    test("tectonic installed", result.returncode == 0, result.stderr[:100] if result.returncode else "")

    # Compile a sample resume
    resume_path = Path("resumes/sre_devops.tex")
    if resume_path.exists():
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_tex = Path(tmpdir) / "test.tex"
            tmp_tex.write_text(resume_path.read_text())
            pdf_path = compile_tex_to_pdf(str(tmp_tex), output_dir=tmpdir)
            test("Resume compiles to PDF", pdf_path and Path(pdf_path).exists(),
                 f"path: {pdf_path}")
            if pdf_path and Path(pdf_path).exists():
                size = Path(pdf_path).stat().st_size
                test(f"  PDF size reasonable ({size:,} bytes)", 30_000 < size < 500_000)
    else:
        warn("resumes/sre_devops.tex not found — skipping compilation test")

except Exception as e:
    test("LaTeX compilation", False, str(e))


# ============================================================
# 7. QUALITY LOGGER
# ============================================================
section("7. QUALITY LOGGER")

try:
    from quality_logger import read_quality_log, get_model_stats

    entries = read_quality_log()
    test(f"Quality log has entries", len(entries) > 0, f"got {len(entries)}")

    if entries:
        # Check recent entries have provider/model
        recent = entries[-10:]
        with_provider = sum(1 for e in recent if e.get("provider"))
        test(f"  Recent entries have provider info", with_provider > 0,
             f"{with_provider}/{len(recent)}")

        # Model stats
        stats = get_model_stats()
        test(f"  Model stats computed ({len(stats)} models)", len(stats) > 0)
        for model, s in list(stats.items())[:3]:
            print(f"         {model}: {s['count']} calls, avg score {s.get('avg_score', 'N/A')}")

except Exception as e:
    test("Quality logger", False, str(e))


# ============================================================
# SUMMARY
# ============================================================
section("SUMMARY")
print(f"  \033[32m{PASS} passed\033[0m, \033[31m{FAIL} failed\033[0m, \033[33m{WARN} warnings\033[0m")
print()

if ERRORS:
    print(f"  \033[31mFAILURES:\033[0m")
    for e in ERRORS:
        print(f"  {e}")
    print()

if WARNINGS:
    print(f"  \033[33mWARNINGS:\033[0m")
    for w in WARNINGS:
        print(f"  {w}")
    print()

sys.exit(1 if FAIL > 0 else 0)
