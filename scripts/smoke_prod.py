#!/usr/bin/env python3
"""Read-only production smoke test for NaukriBaba.

Exercises the deployed system exactly the way a human would glance at it
before a demo: health, the dashboard job list, one real artifact URL, and
the MCP transport's auth gate. Every request is a GET (or a HEAD-like
streamed GET closed before the body downloads); nothing here writes,
deletes, scrapes, or calls an LLM — safe to run against production
repeatedly.

Usage:
    source .venv/bin/activate
    python scripts/smoke_prod.py

Exit code is 0 iff every check passed, non-zero otherwise (so it's usable
as a CI/pre-demo gate, not just a human-readable report). Set API_URL to
point at a different stage; defaults to the deployed prod API Gateway stage
(same default scripts/verify_phase1.py already uses).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env the same minimal way app.py / scripts/verify_phase1.py do.
_env_path = REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

API = os.environ.get("API_URL", "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod")
TIMEOUT = 15

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    tag = "PASS" if ok else "FAIL"
    line = f"  [{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


def _user_jwt():
    """Self-sign a short-lived (5 min) read-only JWT for this project's own
    user, the same technique tests/security/conftest.py's `_make_hs256_jwt`
    and scripts/verify_phase1.py already use: HS256 with the secret already
    sitting in this machine's own .env, no password, no OAuth flow, and the
    token/secret are never printed. Returns (token, user_id, email) or None
    if credentials aren't available — callers must treat None as "skip the
    authenticated checks", not "fail them".
    """
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not secret:
        return None
    try:
        from db_client import SupabaseClient
        from jose import jwt as jose_jwt
    except Exception:
        return None
    try:
        db = SupabaseClient.from_env()
        result = (
            db.client.table("users").select("id, email")
            .eq("email", "254utkarsh@gmail.com").limit(1).execute()
        )
        rows = result.data or []
        if not rows:
            rows = db.client.table("users").select("id, email").limit(1).execute().data or []
        if not rows:
            return None
        user_id, email = rows[0]["id"], rows[0].get("email", "")
    except Exception:
        return None

    token = jose_jwt.encode(
        {
            "sub": user_id, "email": email, "aud": "authenticated", "role": "authenticated",
            "iat": int(time.time()), "exp": int(time.time()) + 300,
        },
        secret, algorithm="HS256",
    )
    return token, user_id, email


def main() -> int:
    print(f"Smoke-testing {API}\n(read-only — GETs only, no writes, no LLM calls)\n")

    # 1. Health — public, unauthenticated.
    try:
        r = requests.get(f"{API}/api/health", timeout=TIMEOUT)
        check("GET /api/health returns 200", r.status_code == 200, f"status={r.status_code}")
        data = r.json() if r.ok else {}
        check("  status == 'ok'", data.get("status") == "ok", str(data))
    except Exception as e:
        check("GET /api/health reachable", False, str(e))

    # 2 + 3. Dashboard job list returns rows, and one real artifact URL resolves.
    auth = _user_jwt()
    if auth is None:
        check(
            "Authenticated checks (job list + artifact)", False,
            "no SUPABASE_JWT_SECRET / no user row / db_client import failed — "
            "cannot mint a token, skipping job-list + artifact checks",
        )
    else:
        token, user_id, email = auth
        headers = {"Authorization": f"Bearer {token}"}
        print(f"  (authenticated read-only as {email or user_id})")

        try:
            r = requests.get(f"{API}/api/dashboard/jobs?per_page=5", headers=headers, timeout=TIMEOUT)
            check("GET /api/dashboard/jobs returns 200", r.status_code == 200, f"status={r.status_code}")
            jobs = r.json().get("jobs", []) if r.ok else []
            check("  dashboard job list returns >= 1 row", len(jobs) > 0, f"got {len(jobs)} row(s)")
        except Exception as e:
            check("GET /api/dashboard/jobs reachable", False, str(e))

        artifact_url = None
        try:
            r = requests.get(f"{API}/api/dashboard/jobs?tailored=true&per_page=5", headers=headers, timeout=TIMEOUT)
            for job in (r.json().get("jobs", []) if r.ok else []):
                if job.get("resume_s3_url"):
                    artifact_url = job["resume_s3_url"]
                    break
        except Exception as e:
            check("GET /api/dashboard/jobs?tailored=true reachable", False, str(e))

        if artifact_url:
            try:
                # stream=True + immediate close: verifies the presigned URL
                # resolves (status + headers) without downloading the PDF body.
                with requests.get(artifact_url, timeout=TIMEOUT, stream=True) as ar:
                    check("Artifact URL (resume_s3_url) resolves", ar.status_code == 200, f"status={ar.status_code}")
            except Exception as e:
                check("Artifact URL (resume_s3_url) resolves", False, str(e))
        else:
            check("Artifact URL (resume_s3_url) resolves", False, "no tailored job with resume_s3_url found to test")

    # 4. MCP transport must reject unauthenticated requests (RequireSupabaseJWT).
    try:
        r = requests.get(f"{API}/mcp/sse", timeout=TIMEOUT)
        check("GET /mcp/sse (unauthenticated) returns 401", r.status_code == 401, f"status={r.status_code}")
    except Exception as e:
        check("GET /mcp/sse reachable", False, str(e))

    failed = [name for name, ok, _ in _RESULTS if not ok]
    print(f"\n{len(_RESULTS) - len(failed)}/{len(_RESULTS)} checks passed.")
    if failed:
        print("FAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
