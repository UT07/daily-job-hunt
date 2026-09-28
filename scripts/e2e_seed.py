#!/usr/bin/env python3
"""Seed / tear down the Playwright live-mode fixtures, and mint its JWT.

WHY A SYNTHETIC USER
--------------------
The live E2E project talks to the real Supabase project, because the bugs it
exists to catch (``hide_expired``'s engaged exemption, the ``not_archived``
default, the tier bands) live in ``db_client.get_jobs``'s SQL and cannot be
proved against a mock.

Every row this script writes carries ``user_id = E2E_USER_ID`` -- a fixed,
obviously-synthetic UUID that no human account will ever have. That single
predicate is what makes the teardown safe: it deletes by ``user_id`` and
therefore cannot reach the owner's data even if it is run at the wrong moment.

There is a hard guard below: the script refuses to delete anything unless the
user id it is about to delete is exactly ``E2E_USER_ID``.

NO AUTH USER IS CREATED
-----------------------
The backend verifies a Supabase JWT (``auth.py``), so the suite needs a token,
not a password. This script mints one itself with HS256 and the project's
``SUPABASE_JWT_SECRET`` -- the same signature the real Supabase issues -- and
inserts only a ``public.users`` row. Nothing is created in ``auth.users``, no
password exists anywhere, and the owner is never asked for theirs.

USAGE
-----
    python scripts/e2e_seed.py seed        # insert the fixture rows, print E2E_JWT
    python scripts/e2e_seed.py token       # re-print the token (no writes)
    python scripts/e2e_seed.py status      # what is currently seeded
    python scripts/e2e_seed.py teardown    # delete every E2E_USER_ID row

Requires SUPABASE_URL, SUPABASE_SERVICE_KEY and SUPABASE_JWT_SECRET, taken from
the environment or from a .env file (the repo root's by default; override with
NAUKRIBABA_ENV_FILE).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The synthetic tenant. Shared with web/e2e/fixtures/session.js -- change it in
# one place and the suite authenticates as a user with no data.
E2E_USER_ID = "00000000-0000-4000-8000-00000000e2e2"
E2E_USER_EMAIL = "playwright-e2e@naukribaba.test"
E2E_JOB_PREFIX = "e2e-live-"


def _load_env() -> None:
    env_file = os.environ.get("NAUKRIBABA_ENV_FILE") or str(REPO_ROOT / ".env")
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if Path(env_file).exists():
        load_dotenv(env_file)


def _client():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        sys.exit(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY are not set.\n"
            "Point NAUKRIBABA_ENV_FILE at a .env that has them, or export them."
        )
    return create_client(url, key)


def days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def score_to_tier(score):
    """The production bands, imported if possible so the fixtures cannot drift
    from the pipeline: S 90+, A 80-89, B 70-79, C 60-69, D <60."""
    try:
        from lambdas.pipeline.score_batch import score_to_tier as real

        return real(score)
    except Exception:
        if score is None:
            return "D"
        for threshold, tier in ((90, "S"), (80, "A"), (70, "B"), (60, "C")):
            if score >= threshold:
                return tier
        return "D"


def _job(job_id, title, company, score, **over):
    row = {
        "job_id": E2E_JOB_PREFIX + job_id,
        "user_id": E2E_USER_ID,
        "title": title,
        "company": company,
        "location": "Dublin, Ireland",
        "description": "Python, AWS and Kubernetes. Seeded by the E2E suite.",
        "apply_url": f"https://example.invalid/e2e/{job_id}",
        "source": "linkedin",
        "match_score": score,
        "ats_score": score,
        "hiring_manager_score": score,
        "tech_recruiter_score": score,
        "score_tier": score_to_tier(score),
        "score_status": "scored",
        "matched_resume": "sre_devops",
        "application_status": "New",
        "first_seen": days_ago(3),
        "last_seen": days_ago(1),
        "is_expired": False,
        "key_matches": ["Python", "AWS"],
        "gaps": [],
        "archetype": "backend",
        "seniority": "Mid-Level",
        "remote": "Hybrid",
        "level_fit": "exact_match",
        # job_hash is deliberately left NULL: jobs.job_hash is a foreign key
        # into jobs_raw, and seeding a value would mean writing to a second
        # table for no test benefit.
    }
    row.update(over)
    return row


def fixture_rows():
    """Nine rows, each earning its place in at least one assertion."""
    return [
        _job("s-tier", "Staff Platform Engineer", "Aurora Systems E2E", 93,
             first_seen=days_ago(1), archetype="platform_cloud", seniority="Staff/Lead",
             remote="Remote", resume_s3_url="https://example.invalid/e2e-resume.pdf"),
        _job("a-tier", "Senior Backend Engineer", "Borealis Labs E2E", 84,
             source="indeed", first_seen=days_ago(3), level_fit="stretch"),
        _job("b-tier", "Site Reliability Engineer", "Cygnus Cloud E2E", 72,
             source="adzuna", first_seen=days_ago(5), archetype="sre_devops", remote="On-site"),
        _job("low-score", "Junior Support Analyst", "Delta Retail E2E", 41,
             source="hn_hiring", first_seen=days_ago(4), seniority="Junior/Graduate"),
        # The bug-2 row: applied months ago, posting has since 404'd.
        _job("applied-expired", "Principal Engineer", "Echo Financial E2E", 88,
             application_status="Applied", is_expired=True, first_seen=days_ago(120),
             resume_s3_url="https://example.invalid/e2e-applied.pdf"),
        # Expired and never engaged: the row hide_expired SHOULD hide.
        _job("expired", "Cloud Engineer", "Foxtrot Media E2E", 76,
             is_expired=True, source="glassdoor", first_seen=days_ago(6)),
        # The 14-30 day band.
        _job("stale", "Infrastructure Engineer", "Gamma Freight E2E", 81,
             first_seen=days_ago(20), source="irishjobs"),
        # The bug-3 row: 30+ days and unengaged, so off the board entirely.
        _job("archived", "Ancient Posting", "Helios Legacy E2E", 91,
             first_seen=days_ago(140), source="jobs_ie"),
        # Engaged, so exempt from BOTH the age thresholds and hide_expired.
        # Deliberately expired as well: a Rejected row that is not expired
        # would survive a broken `.eq("is_expired", False)` by accident, and
        # the test asserting the exemption would pass for the wrong reason.
        _job("rejected", "Data Platform Engineer", "Iris Analytics E2E", 79,
             application_status="Rejected", is_expired=True, first_seen=days_ago(60),
             source="greenhouse", archetype="data"),
    ]


def mint_jwt(hours: int = 12) -> str:
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        sys.exit("SUPABASE_JWT_SECRET is not set — cannot mint a token the backend will accept.")
    from jose import jwt

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": E2E_USER_ID,
            "email": E2E_USER_EMAIL,
            "aud": "authenticated",
            "role": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=hours)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


def cmd_seed(db):
    db.table("users").upsert(
        {
            "id": E2E_USER_ID,
            "email": E2E_USER_EMAIL,
            "name": "Playwright E2E",
            "phone": "+353000000000",
            "location": "Dublin, Ireland",
            "visa_status": "Stamp 1G",
            "github": "https://github.invalid/e2e",
            "linkedin": "https://linkedin.invalid/in/e2e",
            "candidate_context": "Synthetic user owned by the Playwright E2E suite.",
            "gdpr_consent_at": datetime.now(timezone.utc).isoformat(),
            "onboarding_completed_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="id",
    ).execute()

    rows = fixture_rows()
    db.table("jobs").upsert(rows, on_conflict="job_id,user_id").execute()
    print(f"seeded {len(rows)} jobs for {E2E_USER_ID}")
    print()
    print("export E2E_LIVE=1")
    print(f"export E2E_JWT={mint_jwt()}")


def cmd_status(db):
    users = db.table("users").select("id,email").eq("id", E2E_USER_ID).execute().data
    jobs = db.table("jobs").select("job_id,title,match_score,score_tier,application_status,is_expired,first_seen") \
        .eq("user_id", E2E_USER_ID).order("first_seen", desc=True).execute().data
    print(f"user row: {users}")
    print(f"{len(jobs)} seeded jobs:")
    for j in jobs:
        print(f"  {j['job_id']:<28} {str(j['match_score']):>5} {j['score_tier']} "
              f"{j['application_status']:<10} expired={j['is_expired']} {j['first_seen'][:10]}")


def cmd_teardown(db):
    # The guard. Everything this function deletes is scoped to one synthetic
    # user id, and it refuses to run if that id has been edited into something
    # that could belong to a person.
    if E2E_USER_ID != "00000000-0000-4000-8000-00000000e2e2":
        sys.exit("refusing to delete: E2E_USER_ID is not the known synthetic id")

    jobs = db.table("jobs").delete().eq("user_id", E2E_USER_ID).execute()
    for table in ("application_timeline", "user_resumes", "user_search_configs"):
        try:
            db.table(table).delete().eq("user_id", E2E_USER_ID).execute()
        except Exception as e:  # pragma: no cover - table may not exist
            print(f"  (skipped {table}: {e})")
    users = db.table("users").delete().eq("id", E2E_USER_ID).execute()
    print(f"deleted {len(jobs.data or [])} jobs and {len(users.data or [])} user rows for {E2E_USER_ID}")


def main():
    _load_env()
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "token":
        print(mint_jwt())
        return
    db = _client()
    if command == "seed":
        cmd_seed(db)
    elif command == "teardown":
        cmd_teardown(db)
    elif command == "status":
        cmd_status(db)
    else:
        sys.exit(f"unknown command {command!r}; use seed | teardown | token | status")


if __name__ == "__main__":
    main()
