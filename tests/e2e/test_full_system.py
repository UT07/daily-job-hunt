"""Full system E2E test — tests every API endpoint and critical user flow.

Run with: python tests/e2e/test_full_system.py

Tests against the LIVE deployed API (not local). Reports all failures.
"""
import json
import sys
import time
from datetime import datetime, timedelta

import boto3
import httpx

# Config
API = "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod"
JWT_SECRET = "CYDI93+ZN8WFBDdTmtioU74bS92a6ynC0PHbQyZkDiexyoceSZeoe8cnbPeDu5fj6xZ+0W5fHgy5W3YaTDAStg=="
USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
REGION = "eu-west-1"

# Generate auth token
import jwt as pyjwt
TOKEN = pyjwt.encode({
    "sub": USER_ID, "role": "authenticated", "iss": "supabase",
    "iat": int(datetime.now().timestamp()),
    "exp": int((datetime.now() + timedelta(hours=24)).timestamp()),
}, JWT_SECRET, algorithm="HS256")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name: str):
        self.passed += 1
        print(f"  ✓ {name}")

    def fail(self, name: str, detail: str):
        self.failed += 1
        self.errors.append((name, detail))
        print(f"  ✗ {name} — {detail}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"RESULTS: {self.passed}/{total} passed, {self.failed} failed")
        if self.errors:
            print(f"\nFAILURES:")
            for name, detail in self.errors:
                print(f"  ✗ {name}: {detail}")
        print(f"{'='*60}")
        return self.failed == 0


results = TestResult()


def api_get(path: str, timeout: int = 30) -> tuple:
    """GET request, return (status, data)."""
    try:
        r = httpx.get(f"{API}{path}", headers=HEADERS, timeout=timeout)
        data = r.json() if r.status_code < 500 else r.text
        return r.status_code, data
    except Exception as e:
        return 0, str(e)


def api_post(path: str, body: dict = None, timeout: int = 60) -> tuple:
    """POST request, return (status, data)."""
    try:
        r = httpx.post(f"{API}{path}", headers=HEADERS, json=body or {}, timeout=timeout)
        data = r.json() if r.status_code < 500 else r.text
        return r.status_code, data
    except Exception as e:
        return 0, str(e)


# ============================================================================
# 1. AUTH & PROFILE
# ============================================================================
print("\n=== 1. AUTH & PROFILE ===")

status, data = api_get("/api/health")
if status == 200:
    results.ok("Health endpoint")
else:
    results.fail("Health endpoint", f"status={status}")

status, data = api_get("/api/profile")
if status == 200 and data.get("email"):
    results.ok(f"Profile — {data['email']}")
else:
    results.fail("Profile", f"status={status} data={str(data)[:80]}")

# Unauthenticated should fail
r = httpx.get(f"{API}/api/profile", timeout=10)
if r.status_code == 401:
    results.ok("Auth guard (no token → 401)")
else:
    results.fail("Auth guard", f"Expected 401, got {r.status_code}")


# ============================================================================
# 2. SEARCH CONFIG
# ============================================================================
print("\n=== 2. SEARCH CONFIG ===")

status, data = api_get("/api/search-config")
if status == 200 and data.get("queries"):
    results.ok(f"Search config — queries={data['queries']}")
else:
    results.fail("Search config", f"status={status}")


# ============================================================================
# 3. PIPELINE STATUS
# ============================================================================
print("\n=== 3. PIPELINE STATUS ===")

status, data = api_get("/api/pipeline/status")
if status == 200:
    latest = data.get("latest_run", {})
    run_date = latest.get("run_date", "none")
    results.ok(f"Pipeline status — last run: {run_date}")
    metrics = data.get("today_metrics", [])
    if metrics:
        results.ok(f"Today's metrics — {len(metrics)} scraper entries")
    else:
        results.fail("Today's metrics", "Empty — no scraper metrics for today")
else:
    results.fail("Pipeline status", f"status={status}")


# ============================================================================
# 4. DASHBOARD / JOBS
# ============================================================================
print("\n=== 4. DASHBOARD / JOBS ===")

# List all jobs
status, data = api_get("/api/dashboard/jobs?page=1&per_page=5")
if status == 200 and data.get("total", 0) > 0:
    results.ok(f"Job list — {data['total']} total jobs")
else:
    results.fail("Job list", f"status={status} total={data.get('total', 0) if isinstance(data, dict) else 0}")

# Tier filtering
for tier in ["S", "A", "B"]:
    status, data = api_get(f"/api/dashboard/jobs?tier={tier}&page=1&per_page=1")
    if status == 200:
        count = data.get("total", 0)
        results.ok(f"Tier {tier} filter — {count} jobs")
    else:
        results.fail(f"Tier {tier} filter", f"status={status}")

# Min score filter
status, data = api_get("/api/dashboard/jobs?min_score=80&page=1&per_page=1")
if status == 200:
    results.ok(f"Min score filter — {data.get('total', 0)} jobs with score≥80")
else:
    results.fail("Min score filter", f"status={status}")

# Hide expired
status, data = api_get("/api/dashboard/jobs?hide_expired=true&page=1&per_page=1")
if status == 200:
    results.ok(f"Hide expired — {data.get('total', 0)} non-expired")
else:
    results.fail("Hide expired", f"status={status}")

# 404 for nonexistent job
status, _ = api_get("/api/dashboard/jobs/nonexistent-uuid-here")
if status == 404:
    results.ok("Nonexistent job → 404")
else:
    results.fail("Nonexistent job", f"Expected 404, got {status}")


# ============================================================================
# 5. JOB-SPECIFIC ENDPOINTS (pick a real S-tier job)
# ============================================================================
print("\n=== 5. JOB-SPECIFIC ENDPOINTS ===")

status, data = api_get("/api/dashboard/jobs?tier=S&page=1&per_page=1")
if status != 200 or not data.get("jobs"):
    results.fail("Get S-tier job for testing", "No S-tier jobs found")
    job_id = None
else:
    job = data["jobs"][0]
    job_id = job["job_id"]
    print(f"  Test job: {job['title'][:40]} @ {job['company']}")

    # Single job detail
    status, detail = api_get(f"/api/dashboard/jobs/{job_id}")
    if status == 200 and detail.get("title"):
        results.ok("Single job detail")
    else:
        results.fail("Single job detail", f"status={status}")

    # Data completeness checks
    has_desc = bool(detail.get("description"))
    has_score = detail.get("match_score", 0) > 0
    has_resume = bool(detail.get("resume_s3_url"))
    has_cl = bool(detail.get("cover_letter_s3_url"))
    has_contacts = bool(detail.get("linkedin_contacts"))

    if has_desc: results.ok("Job has description")
    else: results.fail("Job description", "MISSING")

    if has_score: results.ok(f"Job has score: {detail['match_score']}")
    else: results.fail("Job score", "MISSING or 0")

    if has_resume: results.ok("Job has resume URL")
    else: results.fail("Job resume URL", "MISSING — tailoring may not have run")

    if has_contacts: results.ok("Job has contacts")
    else: results.fail("Job contacts", "MISSING")

    # Check resume URL is not expired
    if has_resume:
        resume_url = detail["resume_s3_url"]
        try:
            r = httpx.head(resume_url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                results.ok("Resume URL accessible (not expired)")
            else:
                results.fail("Resume URL", f"HTTP {r.status_code} — may be expired")
        except Exception as e:
            results.fail("Resume URL", f"Error: {str(e)[:50]}")

    # Timeline
    status, data = api_get(f"/api/dashboard/jobs/{job_id}/timeline")
    if status == 200:
        results.ok(f"Timeline — {len(data) if isinstance(data, list) else '?'} events")
    else:
        results.fail("Timeline", f"status={status} — {str(data)[:80]}")

    # Sections (may 404 if no tailored tex)
    status, data = api_get(f"/api/dashboard/jobs/{job_id}/sections")
    if status == 200:
        results.ok(f"Sections — {len(data.get('sections', [])) if isinstance(data, dict) else '?'} sections")
    elif status == 404:
        results.ok("Sections — 404 (no tailored tex, expected for untailored jobs)")
    else:
        results.fail("Sections", f"status={status}")


# ============================================================================
# 6. AI ENDPOINTS (these call real AI — test that they don't 500)
# ============================================================================
print("\n=== 6. AI ENDPOINTS ===")

if job_id:
    # Suggest
    status, data = api_post(f"/api/dashboard/jobs/{job_id}/suggest", {
        "section": "summary", "current_content": "Experienced engineer"
    })
    if status == 200 and data.get("suggestion"):
        results.ok(f"AI Suggest — {len(data['suggestion'])} chars")
    else:
        results.fail("AI Suggest", f"status={status} data={str(data)[:80]}")

    # Research
    status, data = api_post(f"/api/dashboard/jobs/{job_id}/research")
    if status == 200 and data.get("company_overview"):
        results.ok(f"AI Research — has company_overview")
    else:
        results.fail("AI Research", f"status={status} data={str(data)[:80]}")

    # Interview Prep
    status, data = api_post(f"/api/dashboard/jobs/{job_id}/interview-prep")
    if status == 200 and data.get("star_stories"):
        results.ok(f"AI Interview Prep — {len(data['star_stories'])} STAR stories")
    else:
        results.fail("AI Interview Prep", f"status={status} data={str(data)[:80]}")

    # Email generation
    status, data = api_post(f"/api/dashboard/jobs/{job_id}/generate-email", {
        "template": "cold_outreach"
    })
    if status == 200 and data.get("subject") and data.get("body"):
        results.ok(f"Email generation — subject: {data['subject'][:40]}")
    else:
        results.fail("Email generation", f"status={status} data={str(data)[:80]}")

    # Find contacts
    status, data = api_post(f"/api/dashboard/jobs/{job_id}/find-contacts")
    if status == 202:
        results.ok("Find contacts — 202 accepted (async)")
    else:
        results.fail("Find contacts", f"status={status}")


# ============================================================================
# 7. CONTACT QUALITY AUDIT
# ============================================================================
print("\n=== 7. CONTACT QUALITY AUDIT ===")

from supabase import create_client
ssm = boto3.client("ssm", region_name=REGION)
db = create_client(
    ssm.get_parameter(Name="/naukribaba/SUPABASE_URL", WithDecryption=True)["Parameter"]["Value"],
    ssm.get_parameter(Name="/naukribaba/SUPABASE_SERVICE_KEY", WithDecryption=True)["Parameter"]["Value"],
)

sa_contacts = db.table("jobs").select("linkedin_contacts").in_("score_tier", ["S", "A"]).not_.is_("linkedin_contacts", "null").limit(20).execute()

total_contacts = 0
names_missing = 0
urls_missing = 0
garbage_names = 0
placeholder_msgs = 0

for j in sa_contacts.data:
    try:
        contacts = json.loads(j["linkedin_contacts"]) if isinstance(j["linkedin_contacts"], str) else j["linkedin_contacts"]
        for c in contacts:
            total_contacts += 1
            if not c.get("name"): names_missing += 1
            if not c.get("profile_url") or len(c.get("profile_url", "")) < 20: urls_missing += 1
            name = c.get("name", "")
            if "<" in name or "substring" in name or len(name) > 50: garbage_names += 1
            msg = c.get("message", "")
            if "[First Name]" in msg or "[first name]" in msg.lower(): placeholder_msgs += 1
    except Exception:
        pass

if total_contacts > 0:
    results.ok(f"Contact audit — {total_contacts} contacts across {len(sa_contacts.data)} jobs")
    if names_missing / total_contacts < 0.3:
        results.ok(f"Names — {names_missing}/{total_contacts} missing ({100*names_missing//total_contacts}%)")
    else:
        results.fail("Contact names", f"{names_missing}/{total_contacts} missing ({100*names_missing//total_contacts}%)")
    if garbage_names == 0:
        results.ok("No garbage names")
    else:
        results.fail("Garbage names", f"{garbage_names} contacts have HTML/JS in name field")
    if placeholder_msgs == 0:
        results.ok("No [First Name] placeholders")
    else:
        results.fail("Placeholder messages", f"{placeholder_msgs} still have [First Name]")
    if urls_missing / total_contacts < 0.1:
        results.ok(f"URLs — {urls_missing}/{total_contacts} missing")
    else:
        results.fail("Contact URLs", f"{urls_missing}/{total_contacts} missing")
else:
    results.fail("Contact audit", "No contacts found in S+A jobs")


# ============================================================================
# 8. DATA INTEGRITY
# ============================================================================
print("\n=== 8. DATA INTEGRITY ===")

# Check S+A artifact completeness
sa_total = db.table("jobs").select("job_hash", count="exact").in_("score_tier", ["S", "A"]).execute()
sa_resume = db.table("jobs").select("job_hash", count="exact").in_("score_tier", ["S", "A"]).not_.is_("resume_s3_url", "null").execute()
sa_cl = db.table("jobs").select("job_hash", count="exact").in_("score_tier", ["S", "A"]).not_.is_("cover_letter_s3_url", "null").execute()
sa_contacts_count = db.table("jobs").select("job_hash", count="exact").in_("score_tier", ["S", "A"]).not_.is_("linkedin_contacts", "null").execute()
sa_no_desc = db.table("jobs").select("job_hash", count="exact").in_("score_tier", ["S", "A"]).is_("description", "null").execute()

results.ok(f"S+A total: {sa_total.count}")

pct_resume = 100 * sa_resume.count // max(1, sa_total.count)
if pct_resume >= 70:
    results.ok(f"S+A with resume: {sa_resume.count}/{sa_total.count} ({pct_resume}%)")
else:
    results.fail(f"S+A resume coverage", f"{sa_resume.count}/{sa_total.count} ({pct_resume}%) — target ≥70%")

pct_cl = 100 * sa_cl.count // max(1, sa_total.count)
if pct_cl >= 50:
    results.ok(f"S+A with cover letter: {sa_cl.count}/{sa_total.count} ({pct_cl}%)")
else:
    results.fail(f"S+A CL coverage", f"{sa_cl.count}/{sa_total.count} ({pct_cl}%) — target ≥50%")

pct_contacts = 100 * sa_contacts_count.count // max(1, sa_total.count)
results.ok(f"S+A with contacts: {sa_contacts_count.count}/{sa_total.count} ({pct_contacts}%)")

if sa_no_desc.count == 0:
    results.ok("No S+A jobs missing description")
else:
    results.fail("Missing descriptions", f"{sa_no_desc.count} S+A jobs have no description")

# Check for expired presigned URLs
import urllib.parse
expired = 0
sample = db.table("jobs").select("resume_s3_url").not_.is_("resume_s3_url", "null").limit(50).execute()
for j in sample.data:
    url = j["resume_s3_url"]
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    expires = params.get("Expires", [None])[0]
    if expires and datetime.fromtimestamp(int(expires)) < datetime.now():
        expired += 1

if expired == 0:
    results.ok("No expired URLs in sample (50 checked)")
else:
    results.fail("Expired URLs", f"{expired}/50 sampled URLs are expired")


# ============================================================================
# 9. LAMBDA HEALTH CHECK
# ============================================================================
print("\n=== 9. LAMBDA HEALTH CHECK ===")

lambda_client = boto3.client("lambda", region_name=REGION)
lambdas_to_check = [
    "naukribaba-tailor-resume",
    "naukribaba-compile-latex",
    "naukribaba-generate-cover-letter",
    "naukribaba-save-job",
    "naukribaba-score-batch",
    "naukribaba-merge-dedup",
    "naukribaba-scrape-contacts",
    "naukribaba-post-score",
    "naukribaba-self-improve",
    "naukribaba-send-email",
]

for fn in lambdas_to_check:
    try:
        config = lambda_client.get_function_configuration(FunctionName=fn)
        timeout = config.get("Timeout", 0)
        mem = config.get("MemorySize", 0)
        results.ok(f"Lambda {fn.replace('naukribaba-', ''):25s} timeout={timeout}s mem={mem}MB")
    except Exception as e:
        results.fail(f"Lambda {fn}", str(e)[:60])


# ============================================================================
# SUMMARY
# ============================================================================
success = results.summary()
sys.exit(0 if success else 1)
