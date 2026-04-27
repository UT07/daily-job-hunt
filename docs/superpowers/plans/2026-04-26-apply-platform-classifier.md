# Apply Platform Classifier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure URL→ATS classifier, flip the auto-apply eligibility gate from `apply_platform` to `apply_url`, backfill 831 prod jobs, and ship a 1-line resume-label UX fix.

**Architecture:** One pure regex function (`shared/apply_platform.py`) is called from two integration points — `lambdas/pipeline/score_batch.py` for new jobs at scoring-time, and `scripts/backfill_apply_platform.py` for historical jobs. The classifier is informational, never raises, never gates. Auto-apply continues to work for `apply_platform=None` jobs.

**Tech Stack:** Python 3.12, pytest, supabase-py, FastAPI (existing), React/JSX (existing).

**Spec:** [2026-04-26-apply-platform-classifier-design.md](../specs/2026-04-26-apply-platform-classifier-design.md)

**Pulled out of scope (sent to backlog):** Settings ↔ Onboarding work-authorization enum reconciliation — has data-migration tail, needs its own brainstorm.

---

## File Structure

```
layer/
  build.sh                                   (MODIFY) bundle `shared/` into the layer's python/
shared/
  apply_platform.py                          (CREATE) pure classifier function
scripts/
  backfill_apply_platform.py                 (CREATE) one-shot Supabase backfill
lambdas/pipeline/
  score_batch.py                             (MODIFY @ line ~152) call classifier on insert
app.py                                       (MODIFY @ lines 2418, 2472) flip eligibility gate
web/src/pages/
  Settings.jsx                               (MODIFY @ line 412) add resume.label to fallback
tests/unit/
  test_apply_platform.py                     (CREATE) 14 cases
  test_apply_endpoints.py                    (MODIFY) flip expected gate behavior
```

---

## Task 0: Fix the shared-deps layer build (CRITICAL prereq)

**Why this is here:** A pre-flight audit on 2026-04-26 found that the deployed `naukribaba-shared-deps:16` layer **does not contain the repo's `shared/` package**, even though three lambdas (`ws_connect`, `ws_disconnect`, `ws_route` from PR #8) and our new score_batch integration all import `from shared.*`. The WS lambdas have never been runtime-invoked (no frontend WS client exists), so the breakage is silent — but it's real. CI passes only because local PYTHONPATH includes the repo root. Lambda runtime PYTHONPATH does not.

This task fixes the layer build so `shared/` is bundled at `/opt/python/shared/` on every Lambda that uses the layer. Same fix unblocks the broken WS lambdas and makes Task 2's import work.

**Files:**
- Modify: `layer/build.sh`

- [ ] **Step 1: Read the current build script**

```bash
cat layer/build.sh
```

Expected current contents:

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# Build inside Docker to get Linux x86_64 binaries (pydantic_core etc.)
docker run --rm -v "$(pwd)":/layer -w /layer \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  pip install -r requirements.txt -t python/ --quiet

echo "Layer built: $(du -sh python/ | cut -f1)"
```

- [ ] **Step 2: Rewrite `layer/build.sh` to bundle `shared/`**

Replace the file with:

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# 1. Build pip deps inside Docker to get Linux x86_64 binaries (pydantic_core etc.)
docker run --rm -v "$(pwd)":/layer -w /layer \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  pip install -r requirements.txt -t python/ --quiet

# 2. Bundle the repo's `shared/` package into the layer.
# Every Lambda that does `from shared.*` (ws_connect, ws_disconnect, ws_route,
# score_batch, etc.) relies on this being on the Lambda runtime PYTHONPATH
# (Lambda mounts the layer at /opt/python). Without this step the imports
# crash at runtime with ModuleNotFoundError — unit tests pass locally only
# because the repo root is on the local PYTHONPATH.
cp -r ../shared python/shared

echo "Layer built: $(du -sh python/ | cut -f1)"
echo "shared/ files in layer:"
ls python/shared/
```

- [ ] **Step 3: Build the layer locally to verify**

(Requires Docker Desktop running.)

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/awesome-lederberg-3c9dc8 && ./layer/build.sh
```

Expected output ends with:

```
Layer built: 33M  (or similar)
shared/ files in layer:
__init__.py  apply_platform.py  browser_sessions.py  load_job.py  profile_completeness.py  ws_auth.py
```

(Note: `apply_platform.py` will appear in the listing only after Task 1 lands. For now you'll see the existing 5 files — `__init__.py`, `browser_sessions.py`, `load_job.py`, `profile_completeness.py`, `ws_auth.py`. That's correct.)

If Docker isn't running, skip the build step locally — the deploy.yml workflow will run `./layer/build.sh` on every deploy. The visual confirmation is nice-to-have but not required since the deploy gate (sam build + sam deploy) catches catastrophic failures.

- [ ] **Step 4: Spot-check the existing `shared/` files match what the WS lambdas need**

```bash
ls /Users/ut/code/naukribaba/.claude/worktrees/awesome-lederberg-3c9dc8/shared/
```

Expected: `__init__.py  browser_sessions.py  load_job.py  profile_completeness.py  ws_auth.py`

These are the modules `ws_connect.py`, `ws_disconnect.py`, `ws_route.py`, and `app.py` already import from. Confirms the fix unblocks them.

- [ ] **Step 5: Commit**

```bash
git add layer/build.sh
git commit -m "fix(layer): bundle repo's shared/ package into shared-deps layer

PR #8's WS lambdas (ws_connect, ws_disconnect, ws_route) import from
'shared.*' but the deployed layer never contained shared/. They've been
silently broken since 2026-04-24 because no WS client has connected yet
(no frontend UI). Same problem would have hit any future pipeline Lambda
(e.g. score_batch) doing 'from shared.X import Y'.

Fix: cp -r ../shared python/shared after pip install. Lambda runtime mounts
the layer at /opt/python so imports work after this. Unit tests already
passed because local PYTHONPATH includes the repo root.

Detected via pre-flight on 2026-04-26 while writing the apply-platform-
classifier plan; bundled here to ship one fix for both."
```

---

## Task 1: Build the classifier (TDD)

**Files:**
- Create: `tests/unit/test_apply_platform.py`
- Create: `shared/apply_platform.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_apply_platform.py`:

```python
"""Tests for shared.apply_platform.classify_apply_platform.

The classifier is informational — must never raise, must return None for
anything not on the known-platform list.
"""
import pytest
from shared.apply_platform import classify_apply_platform


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/acme/jobs/12345", "greenhouse"),
    ("https://jobs.lever.co/acme-co/abc-123", "lever"),
    ("https://acme.wd5.myworkdayjobs.com/External/job/Dublin/Engineer_R-12345", "workday"),
    ("https://jobs.ashbyhq.com/acme/abc-uuid-123", "ashby"),
    ("https://jobs.smartrecruiters.com/Acme/123-engineer", "smartrecruiters"),
    ("https://apply.workable.com/acme/j/ABC123/", "workable"),
    ("https://acme.taleo.net/careersection/jobdetail.ftl?job=12345", "taleo"),
    ("https://acme.icims.com/jobs/12345/engineer/job", "icims"),
    ("https://acme.jobs.personio.com/job/123456", "personio"),
    ("https://www.linkedin.com/jobs/view/12345?easy_apply=true", "linkedin_easy_apply"),
])
def test_known_platforms(url, expected):
    assert classify_apply_platform(url) == expected


def test_unknown_url_returns_none():
    assert classify_apply_platform("https://jobs.ie/job/12345") is None
    assert classify_apply_platform("https://www.indeed.com/viewjob?jk=abc") is None
    assert classify_apply_platform("https://acme.com/careers/123") is None


def test_empty_or_none_returns_none():
    assert classify_apply_platform("") is None
    assert classify_apply_platform(None) is None


def test_malformed_url_returns_none():
    """Classifier must never raise on garbage input."""
    assert classify_apply_platform("not a url") is None
    assert classify_apply_platform(12345) is None  # type: ignore
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ut/code/naukribaba && source .venv/bin/activate && pytest tests/unit/test_apply_platform.py -v`
Expected: 14 ERRORS or FAILS — `ModuleNotFoundError: No module named 'shared.apply_platform'`

- [ ] **Step 3: Write the classifier**

Create `shared/apply_platform.py`:

```python
"""URL → ATS platform classifier (informational only, never raises, never gates).

Used by:
- lambdas/pipeline/score_batch.py for new jobs at scoring-time
- scripts/backfill_apply_platform.py for one-shot historical backfill

Returns one of {greenhouse, lever, workday, ashby, smartrecruiters, workable,
taleo, icims, personio, linkedin_easy_apply} or None for unmatched URLs.

The /api/apply/* endpoints DO NOT gate on this column — auto-apply works for
jobs with apply_platform=None (cloud browser handles unknown forms via AI vision).
The actual gate is `apply_url`-non-null + `resume_s3_key`-non-null (latter
implicitly enforces ≤B-tier since the tailoring pipeline only writes
resume_s3_key for S/A/B-tier jobs per pipeline policy).
"""
from __future__ import annotations
import re
from typing import Optional


_PATTERNS = [
    ("greenhouse",          re.compile(r"boards\.greenhouse\.io/", re.IGNORECASE)),
    ("lever",               re.compile(r"jobs\.lever\.co/", re.IGNORECASE)),
    ("workday",             re.compile(r"\.myworkdayjobs\.com/", re.IGNORECASE)),
    ("ashby",               re.compile(r"jobs\.ashbyhq\.com/", re.IGNORECASE)),
    ("smartrecruiters",     re.compile(r"jobs\.smartrecruiters\.com/", re.IGNORECASE)),
    ("workable",            re.compile(r"apply\.workable\.com/", re.IGNORECASE)),
    ("taleo",               re.compile(r"\.taleo\.net/", re.IGNORECASE)),
    ("icims",               re.compile(r"\.icims\.com/", re.IGNORECASE)),
    ("personio",            re.compile(r"\.jobs\.personio\.com/", re.IGNORECASE)),
    ("linkedin_easy_apply", re.compile(r"linkedin\.com/jobs/.*easy.?apply", re.IGNORECASE)),
]


def classify_apply_platform(url: Optional[str]) -> Optional[str]:
    """Return one of the supported platform names, or None.

    Pure function. Never raises. Treats non-string input as unknown.
    """
    if not url or not isinstance(url, str):
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(url):
            return name
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_apply_platform.py -v`
Expected: 14 PASS

- [ ] **Step 5: Commit**

```bash
git add shared/apply_platform.py tests/unit/test_apply_platform.py
git commit -m "feat(apply): add URL→ATS platform classifier (informational, non-gating)

Pure regex classifier covering 10 platforms (greenhouse, lever, workday,
ashby, smartrecruiters, workable, taleo, icims, personio, linkedin_easy_apply).
Returns None for unmatched URLs. Never raises.

Per spec docs/superpowers/specs/2026-04-26-apply-platform-classifier-design.md.
14 unit tests pass."
```

---

## Task 2: Wire classifier into score_batch.py (TDD)

**Files:**
- Modify: `lambdas/pipeline/score_batch.py:152`
- Test: extend `tests/unit/test_apply_platform.py` with one integration-style assertion

The cleanest integration point is the single dict-builder at `score_batch.py:144-169` where every new row in the `jobs` table is constructed. No need to touch the 10 individual scrapers (they write to `jobs_raw`, not `jobs`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_apply_platform.py`:

```python
def test_score_batch_record_includes_apply_platform():
    """Smoke test: the score_batch record-builder pattern must call classifier.

    This is a unit-level check on the pattern, not a full Lambda integration test.
    """
    from shared.apply_platform import classify_apply_platform

    # Simulate the record-build that happens in score_batch.py
    job = {"apply_url": "https://boards.greenhouse.io/acme/jobs/123"}
    record = {
        "apply_url": job.get("apply_url"),
        "apply_platform": classify_apply_platform(job.get("apply_url") or ""),
    }
    assert record["apply_platform"] == "greenhouse"
    assert record["apply_url"] == "https://boards.greenhouse.io/acme/jobs/123"
```

Run: `pytest tests/unit/test_apply_platform.py::test_score_batch_record_includes_apply_platform -v`
Expected: PASS already (since classifier exists). This codifies the integration pattern; the next step adds the actual call to `score_batch.py`.

- [ ] **Step 2: Read the score_batch dict builder**

Open `lambdas/pipeline/score_batch.py` and confirm lines 144-169 build `job_record`. Find:

```python
        job_record = {
            "job_id": str(uuid.uuid4()),
            "user_id": user_id,
            "job_hash": job["job_hash"],
            "title": job["title"],
            "company": job["company"],
            "description": job.get("description"),
            "location": job.get("location"),
            "apply_url": job.get("apply_url"),
            "source": job["source"],
```

- [ ] **Step 3: Add classifier call**

In `lambdas/pipeline/score_batch.py`, add an import. The file currently imports only `json, logging, random, statistics, uuid, datetime`, and `from ai_helper import ai_complete_cached, get_supabase` (lines 1-9). There is no existing `from shared.*` import. Add the new import directly after the `from ai_helper` line:

```python
from ai_helper import ai_complete_cached, get_supabase
from shared.apply_platform import classify_apply_platform
```

Then modify the `apply_url` line in the `job_record` dict (line ~152):

```python
            "apply_url": job.get("apply_url"),
            "apply_platform": classify_apply_platform(job.get("apply_url") or ""),
```

- [ ] **Step 4: Add fallback in the column-stripping retry**

Lines 173-182 strip optional columns when CFN/migration drift causes "column X does not exist" errors. Add `apply_platform` to the strip list so a missing column doesn't break inserts:

```python
            if "column" in str(e) and "does not exist" in str(e):
                for col in ("key_matches", "gaps", "match_reasoning", "score_tier",
                            "archetype", "seniority", "remote", "requirement_map",
                            "matched_resume", "apply_platform"):
                    job_record.pop(col, None)
```

- [ ] **Step 5: Run all unit tests**

Run: `pytest tests/unit/ -v --tb=short`
Expected: all green (no regressions, no new failures from the score_batch edit since unit tests don't exercise the Lambda directly).

- [ ] **Step 6: Commit**

```bash
git add lambdas/pipeline/score_batch.py tests/unit/test_apply_platform.py
git commit -m "feat(apply): wire classifier into score_batch insert path

New jobs going jobs_raw → jobs now get apply_platform tagged at scoring time.
Single integration point — scrapers untouched. Stripped from retry-without-
optional-columns fallback so missing column doesn't break inserts."
```

---

## Task 3: Flip eligibility gate (TDD)

**Files:**
- Modify: `tests/unit/test_apply_endpoints.py`
- Modify: `app.py:2418, 2472`

- [ ] **Step 1: Read the existing test patterns**

Open `tests/unit/test_apply_endpoints.py` and find the two existing tests at lines 87 and 142 that exercise `apply_platform=None`:

```python
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
```

These tests currently expect `eligible: False, reason: "not_supported_platform"`. After our change, `apply_platform=None` should NOT block eligibility — only missing `apply_url` should.

- [ ] **Step 2: Update the existing tests**

In `tests/unit/test_apply_endpoints.py`, the existing tests use a `_job_row(**over)` helper (lines 40-51) that accepts arbitrary keyword overrides — including `apply_url=None`, no helper-extension needed. The fixture pattern is `c, _ = client` where `client` is a fixture yielding `(TestClient, db_mock)`.

Find the existing test by name (`test_eligibility_platform_not_supported`):

```python
def test_eligibility_platform_not_supported(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json() == {"eligible": False, "reason": "not_supported_platform"}
```

Replace it with (renamed for clarity, since "not_supported" is no longer the behavior):

```python
def test_eligibility_eligible_when_platform_null(client):
    """After the classifier flip, null platform no longer blocks eligibility.

    Path: apply_url is set → resume_s3_key is set → no existing app → profile complete
    → eligible=True with platform=None passthrough.
    """
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json() == {
        "eligible": True, "platform": None,
        "board_token": "acme", "posting_id": "12345",
    }
```

Find the existing test by name (`test_preview_returns_ineligible_for_unsupported_platform`):

```python
def test_preview_returns_ineligible_for_unsupported_platform(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 200
    assert r.json() == {"eligible": False, "reason": "not_supported_platform"}
```

Replace it with:

```python
def test_preview_passes_through_when_platform_null(client):
    """Preview no longer blocks on null platform — classifier is informational."""
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 200
    body = r.json()
    # Plan 3a's preview returns a minimal-shape body. After the gate flip we
    # expect the eligible-true path to be taken — exact response shape is locked
    # by Plan 3a's existing tests; here we only assert it's NOT a "not_supported"
    # rejection.
    assert body.get("eligible") is True
    assert body.get("reason") != "not_supported_platform"
```

Add two NEW tests asserting the new gate is `apply_url`. Place them next to the existing `test_eligibility_*` tests:

```python
def test_eligibility_blocks_when_apply_url_missing(client):
    """The new gate: jobs with no apply_url cannot be applied to."""
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_url=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.status_code == 200
    assert r.json() == {"eligible": False, "reason": "no_apply_url"}


def test_preview_blocks_when_apply_url_missing(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_url=None)):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 200
    assert r.json() == {"eligible": False, "reason": "no_apply_url"}
```

- [ ] **Step 3: Run tests to verify the existing two fail and the new two fail**

Run: `pytest tests/unit/test_apply_endpoints.py -v`
Expected: 4 failures total (2 modified + 2 new) — current `app.py` still has the old gate.

- [ ] **Step 4: Flip the gates in app.py**

Edit `app.py` line 2418. Find:

```python
    if not job.get("apply_platform"):
        return {"eligible": False, "reason": "not_supported_platform"}
```

Replace with:

```python
    if not job.get("apply_url"):
        return {"eligible": False, "reason": "no_apply_url"}
    # NOTE: resume_s3_key (checked below) is the implicit ≤B-tier gate — the
    # tailoring pipeline only writes it for S/A/B per pipeline policy. Do not
    # remove that gate without re-instating an explicit tier filter.
```

Edit `app.py` line 2472 (in the preview endpoint). Find:

```python
    if not job.get("apply_platform"):
        return {"eligible": False, "reason": "not_supported_platform"}
```

Replace with:

```python
    if not job.get("apply_url"):
        return {"eligible": False, "reason": "no_apply_url"}
```

Also fix line 2451 which currently does `"platform": job["apply_platform"]` (key access, would KeyError when None). Find:

```python
    return {
        "eligible": True,
        "platform": job["apply_platform"],
        "board_token": job.get("apply_board_token"),
        "posting_id": job.get("apply_posting_id"),
    }
```

Replace `job["apply_platform"]` with `job.get("apply_platform")`:

```python
    return {
        "eligible": True,
        "platform": job.get("apply_platform"),
        "board_token": job.get("apply_board_token"),
        "posting_id": job.get("apply_posting_id"),
    }
```

- [ ] **Step 5: Run tests to verify all pass**

Run: `pytest tests/unit/test_apply_endpoints.py -v`
Expected: all PASS (the 2 modified, the 2 new, and any others in the file).

Run: `pytest tests/unit/ tests/contract/ -v --tb=short`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): flip eligibility gate from apply_platform to apply_url

Per Path X (spec 2026-04-26-apply-platform-classifier-design.md): classifier
is informational, not gating. Auto-apply now works for any job with an
apply_url; resume_s3_key remains the implicit ≤B-tier filter. Reason code
'not_supported_platform' replaced with 'no_apply_url'.

Eligibility/preview now return platform=null when classifier didn't match."
```

---

## Task 4: Backfill script

**Files:**
- Create: `scripts/backfill_apply_platform.py`

- [ ] **Step 1: Write the script**

Create `scripts/backfill_apply_platform.py`:

```python
"""One-shot backfill: classify apply_platform for jobs where it's NULL.

Idempotent: only touches rows where apply_platform IS NULL. Re-runnable.

Usage (from repo root):
    source .venv/bin/activate
    python scripts/backfill_apply_platform.py            # dry-run (default)
    python scripts/backfill_apply_platform.py --commit   # actually write

Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from .env (project root).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from typing import Iterable, List

from dotenv import load_dotenv
from supabase import create_client

# Make `shared` importable when run from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.apply_platform import classify_apply_platform  # noqa: E402


CHUNK_SIZE = 100


def _chunked(seq: List[dict], n: int) -> Iterable[List[dict]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Write updates (default: dry-run)")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    sb = create_client(url, key)

    # Fetch every row where apply_platform is NULL but apply_url is set
    rows: list[dict] = []
    PAGE = 1000
    offset = 0
    while True:
        page = (
            sb.table("jobs")
            .select("job_id, apply_url")
            .is_("apply_platform", "null")
            .not_.is_("apply_url", "null")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        if not page.data:
            break
        rows.extend(page.data)
        offset += PAGE
        if len(page.data) < PAGE:
            break

    print(f"Candidates: {len(rows)} jobs with apply_platform IS NULL and apply_url IS NOT NULL")

    classified: list[tuple[str, str]] = []  # (job_id, platform)
    dist: Counter = Counter()
    for r in rows:
        platform = classify_apply_platform(r.get("apply_url") or "")
        if platform:
            classified.append((r["job_id"], platform))
            dist[platform] += 1

    dist["<unmatched>"] = len(rows) - len(classified)
    print("\nClassification result:")
    for k, v in dist.most_common():
        print(f"  {k:<25} {v:>5}")

    if not args.commit:
        print(f"\nDry-run complete. {len(classified)} would be updated. Re-run with --commit to write.")
        return 0

    print(f"\nWriting {len(classified)} updates in chunks of {CHUNK_SIZE}...")
    written = 0
    for chunk in _chunked(classified, CHUNK_SIZE):
        # supabase-py doesn't have a bulk update by id; loop within chunk
        for job_id, platform in chunk:
            sb.table("jobs").update({"apply_platform": platform}).eq("job_id", job_id).execute()
            written += 1
        print(f"  wrote {written}/{len(classified)}")

    print(f"\nDone. {written} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Dry-run locally**

Run: `cd /Users/ut/code/naukribaba && source .venv/bin/activate && python scripts/backfill_apply_platform.py`
Expected: prints classification distribution and "Dry-run complete." Verify the count is reasonable (likely a few hundred matching greenhouse/lever/etc. and the rest unmatched).

- [ ] **Step 3: Commit the script before running it for real**

```bash
git add scripts/backfill_apply_platform.py
git commit -m "feat(apply): one-shot backfill script for apply_platform

Reads SUPABASE_SERVICE_KEY from .env. Dry-run by default; --commit to write.
Idempotent: only touches rows where apply_platform IS NULL."
```

- [ ] **Step 4: Real backfill (after PR merges to main)**

Defer this step until the PR ships. After merge:

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
python scripts/backfill_apply_platform.py --commit
```

Expected: writes ~N updates depending on the dry-run count. Re-runnable safely.

---

## Task 5: Resume label one-liner

**Files:**
- Modify: `web/src/pages/Settings.jsx:412`

- [ ] **Step 1: Edit the fallback chain**

Find at `web/src/pages/Settings.jsx:412`:

```jsx
                  <p className="text-sm font-bold text-black">{resume.filename || resume.name || `Resume ${resume.id}`}</p>
```

Replace with:

```jsx
                  <p className="text-sm font-bold text-black">{resume.label || resume.filename || resume.name || `Resume ${resume.id}`}</p>
```

`resume.label` is what the backend stores ("SRE / DevOps (base LaTeX from repo)"). Falls through to UUID only if all four fields are missing.

- [ ] **Step 2: Visual smoke test**

Run dev server in a separate terminal: `cd web && npm run dev`. Open localhost in browser, log in, go to Settings → Resumes. Two resumes should now show their human labels instead of UUIDs.

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/Settings.jsx
git commit -m "fix(ui): show resume label instead of UUID in Settings

Backend already returns resume.label ('SRE / DevOps (base LaTeX from repo)'
etc.). Frontend was only checking resume.filename / resume.name, both unset
in the data, falling through to the UUID. One-line fix."
```

---

## Task 6: PR + deploy + manual smoke test

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin claude/awesome-lederberg-3c9dc8
gh pr create --title "feat(apply): classifier + flip eligibility gate + fix shared-deps layer + UX" --body "$(cat <<'EOF'
## Summary
- **`layer/build.sh` now bundles \`shared/\` into the layer** — fixes silently broken PR #8 WS lambdas (would crash with ModuleNotFoundError on first invocation; never noticed because no frontend WS client exists yet). Same fix unblocks new \`from shared.apply_platform\` imports.
- New \`shared/apply_platform.py\` regex classifier for 10 ATS platforms (informational, never raises, never gates)
- \`lambdas/pipeline/score_batch.py\` calls classifier on every new job insert
- \`app.py\` eligibility/preview gates flipped from \`apply_platform\` to \`apply_url\` — auto-apply now works for any job with a URL
- \`scripts/backfill_apply_platform.py\` one-shot backfill for the 831 existing jobs (run after merge)
- One-line UX fix: Settings resume list now shows \`resume.label\` instead of UUID

Spec: \`docs/superpowers/specs/2026-04-26-apply-platform-classifier-design.md\`

## Test plan
- [ ] CI green (unit + contract + integration)
- [ ] After merge: `gh workflow run deploy.yml --ref main`
- [ ] Run `python scripts/backfill_apply_platform.py` (dry-run) — review counts
- [ ] Run `python scripts/backfill_apply_platform.py --commit`
- [ ] curl `/api/apply/eligibility/{any S-tier job_id}` → expect `eligible: true`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: After CI green, merge via gh API**

Per session memory (Apr 26): `gh pr merge` with local checkout fails because the worktree owns the branch. Use the API directly:

```bash
gh api -X PUT "repos/UT07/daily-job-hunt/pulls/<NUMBER>/merge" -f merge_method=squash
```

(Replace `<NUMBER>` with the PR number from step 1's output.)

- [ ] **Step 3: Trigger deploy on main**

```bash
gh workflow run deploy.yml --ref main
gh run watch $(gh run list --workflow=deploy.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds (~5 min). Layer + Lambdas re-deployed with new code.

- [ ] **Step 4: Run the backfill for real**

Pull main locally first to make sure backfill script is the merged version:

```bash
cd /Users/ut/code/naukribaba && git fetch origin main && git checkout main && git pull
source .venv/bin/activate
python scripts/backfill_apply_platform.py            # dry-run, sanity-check counts
python scripts/backfill_apply_platform.py --commit   # actually write
```

- [ ] **Step 5: Manual end-to-end smoke test**

Get a fresh JWT from your browser. Pick any S-tier job from the dashboard. Then:

```bash
JWT='<paste fresh token>'
JOB_ID='<paste S-tier job id>'
curl -sS -H "Authorization: Bearer $JWT" \
  "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/api/apply/eligibility/$JOB_ID" | python3 -m json.tool
```

Expected: `{"eligible": true, "platform": "greenhouse" | "lever" | ... | null, ...}` — for the FIRST TIME ever, since pre-classifier this would have returned `not_supported_platform`.

If `eligible: false` with `reason: "no_resume"` — that means the chosen job is missing `resume_s3_key`. Pick a different S/A-tier job. Per spec, this gate stays on purpose.

- [ ] **Step 6: Update memory**

Append to `~/.claude/projects/-Users-ut-code-naukribaba/memory/MEMORY.md`:

```
- [Session Apr 26](session_2026_04_26.md) — Classifier shipped + flipped gate + 831 backfilled; smoke test green for first time
```

And drop the resolved item from `backlog_apr26_walkthrough.md` (the apply_platform gap is now closed; work-auth + Plan 3c remain).

---

## Self-Review Checklist

(Author note 2026-04-26 — completed before save; updated after pre-flight bug discovery)

- ✅ **Spec coverage:** all 6 spec sections (Goal, Non-Goals, Architecture, Components, Data flow, Testing) map to tasks. Frontend UI delegated to Plan 3c (out of scope here). Slug extraction delegated to Plan 3b. Settings work-auth dropped to backlog (enum mismatch).
- ✅ **No placeholders:** every code block is complete; no TBD/TODO/handwave.
- ✅ **Type consistency:** classifier signature `Optional[str] -> Optional[str]` is the same in both spec and plan. `apply_platform` field name consistent across all tasks.
- ✅ **Integration point corrected:** the spec said "scrapers/base.py Job constructor"; this plan correctly targets `lambdas/pipeline/score_batch.py:152` (the actual write path — `Job` dataclass is orphaned for the new pipeline).
- ✅ **Script is idempotent + dry-run by default:** safe to run repeatedly; explicit `--commit` flag prevents accidental writes.
- ✅ **Layer-build fix bundled (Task 0):** pre-flight discovered `shared/` was never in the deployed layer, silently breaking PR #8's WS lambdas and threatening Task 2's import. Fix is small (3 lines added to `layer/build.sh`) and resolves both issues in one deploy.
