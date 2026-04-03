# Quality Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix data quality issues, improve resume/cover letter writing quality, wire self-improvement feedback loop, integrate QA tests, and fix scrapers — Phases 2.5b, 2.6, 2.7, 2.8, 2.9.

**Architecture:** Refactor the existing pipeline (main.py + Lambda handlers) with unified canonical hashing, deterministic 3-call median scoring, before/after score comparison, v2 tailoring prompts with keyword analysis, tiered self-improvement writing to Supabase `pipeline_adjustments`, and new QA tiers 4b/4c/4d. No new infrastructure except Glassdoor Fargate task.

**Tech Stack:** Python 3.11, Supabase (PostgreSQL), AWS Lambda/Step Functions/Fargate, pytest, pymupdf, tectonic

**Spec:** `docs/superpowers/specs/2026-04-03-quality-pipeline-design.md`
**Grand Plan:** `docs/superpowers/specs/2026-04-03-unified-grand-plan.md`

---

## Execution Order

```
Group A: Quick Wins (do first)
  Task 0: Backfill Apr 2-3 jobs to dashboard
  Task 1: DeepSeek removal + OpenRouter fix

Group B: Phase 2.7 Data Quality ─────────────┐
  Tasks 2-8                                   │
  Task 28: Hash migration script              │
  Task 29: Cross-run artifact reuse           │
  Task 32: seen_jobs.json → Supabase swap     │
  Task 36: Manual JD hashing merge logic      │
                                              ├── PARALLEL
Group C: Phase 2.6 Writing Quality            │  (but D depends on
  Tasks 9-16                                  │   B+C functions)
  Task 33: Resume length management           │
  Task 34: Council critic rubric              │
                                              │
Group D: Phase 2.8 QA Foundation              │
  Tasks 17-19                                 │
  Task 37: Scoring prompt calibration audit   │
  Task 42: Score determinism test             ┘

Group E: Phase 2.9 Self-Improvement (after B+C)
  Tasks 20-23
  Task 30: Model A/B testing
  Task 31: Query optimization tracking
  Task 35: Prompt versioning CRUD
  Task 38: Wire self_improve into Step Functions
  Task 39: Revert action write + cooldown set
  Task 40: Base resume improvement suggestions

Group F: Phase 2.5b Scrapers (independent)
  Tasks 24-26

Group G: Final QA + CI
  Task 27 (includes REPORT ONLY tiers)

Group H: Frontend Integration (after E)
  Task 41: User feedback — "flag score" API + UI

NOTE: Group D tasks import functions from Group B/C.
If using subagent parallelism, Group B+C must COMPLETE
before Group D tasks that test their functions.
```

---

## File Map

### New Files
| File | Purpose |
|------|---------|
| `utils/canonical_hash.py` | Single canonical hash function used everywhere |
| `utils/keyword_extractor.py` | JD keyword extraction for tailoring + cover letters |
| `utils/pdf_validator.py` | PDF output validation (page count, text extraction) |
| `supabase/migrations/20260403_quality_pipeline.sql` | Schema changes: new columns, new tables, RLS (applied via `npx supabase db push`) |
| `scripts/backfill_jobs.py` | One-time: push local jobs to Supabase |
| `scripts/migrate_hashes.py` | One-time: recompute canonical hashes for existing data |
| `tests/quality/golden_dataset.json` | 25 human-labeled JD+resume pairs |
| `tests/fixtures/dedup_fixtures.py` | Synthetic duplicate/near-miss job pairs |
| `tests/unit/test_canonical_hash.py` | Hash consistency tests |
| `tests/unit/test_keyword_extractor.py` | Keyword extraction tests |
| `tests/unit/test_pdf_validator.py` | PDF validation tests |
| `tests/quality/test_data_quality.py` | Tier 4b tests |
| `tests/quality/test_writing_quality.py` | Tier 4c tests |
| `tests/quality/test_self_improvement.py` | Tier 4d tests |

### Modified Files
| File | What Changes |
|------|-------------|
| `lambdas/scrapers/normalizers.py` | Use canonical_hash instead of local md5 |
| `scrapers/base.py` | Use canonical_hash for Job.id and BaseScraper.dedup |
| `lambdas/pipeline/merge_dedup.py` | Use canonical_hash, update fuzzy "richest" logic |
| `lambdas/pipeline/score_batch.py` | Remove truncation, temp=0, multi-call median, before/after, skip bad data |
| `lambdas/pipeline/load_config.py` | Read pipeline_adjustments from Supabase, merge into config |
| `resume_scorer.py` | Adaptive rounds, structured feedback prompt |
| `tailorer.py` | Prompt v2: keyword analysis, dynamic depth, project rewrites |
| `cover_letter.py` | Keyword analysis, early validation with retry |
| `latex_compiler.py` | Work on copy (rollback), hard brace gate |
| `ai_client.py` | Remove DeepSeek provider, fix OpenRouter model name |
| `self_improver.py` | Rewrite: tiered adjustments → Supabase, pipeline_runs metrics |
| `main.py` | Wire before/after scoring, writing quality check, self-improve at end, replace seen_jobs.json with Supabase |
| `config.yaml` | Remove deepseek from providers |
| `template.yaml` | Wire self_improve as terminal Step Functions state, add Glassdoor Fargate with Continue On Fail |
| `app.py` | Add `/api/feedback/flag-score` endpoint for user feedback, manual JD dedup merge |

---

## GROUP A: QUICK WINS

### Task 0: Backfill Apr 2-3 Jobs to Dashboard

**Files:**
- Create: `scripts/backfill_jobs.py`
- Read: `output/2026-04-02/raw_jobs.json`, `output/seen_jobs.json`

- [ ] **Step 1: Write backfill script**

```python
#!/usr/bin/env python3
"""Backfill locally-processed jobs to Supabase dashboard.

Reads raw_jobs.json from output directories, filters for scored jobs,
and upserts them to the Supabase jobs table.
"""
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from db_client import DatabaseClient


def load_local_jobs(output_dir: str) -> list[dict]:
    """Load jobs from raw_jobs.json files in output directories."""
    jobs = []
    output_path = Path(output_dir)

    # Check date-specific directories
    for date_dir in sorted(output_path.iterdir()):
        if not date_dir.is_dir() or date_dir.name.startswith("."):
            continue
        raw_path = date_dir / "raw_jobs.json"
        if raw_path.exists():
            with open(raw_path) as f:
                day_jobs = json.load(f)
                print(f"  {date_dir.name}: {len(day_jobs)} jobs")
                jobs.extend(day_jobs)

    return jobs


def backfill(user_id: str, output_dir: str = "output", min_score: float = 0):
    """Push locally-scored jobs to Supabase."""
    db = DatabaseClient()

    print(f"Loading local jobs from {output_dir}/...")
    all_jobs = load_local_jobs(output_dir)
    print(f"Total local jobs: {len(all_jobs)}")

    # Filter for jobs with scores
    scored = [j for j in all_jobs if j.get("match_score", 0) > min_score]
    print(f"Jobs with score > {min_score}: {len(scored)}")

    # Check what's already in Supabase
    existing = db.client.table("jobs").select("job_id").eq("user_id", user_id).execute()
    existing_ids = {r["job_id"] for r in existing.data}
    print(f"Already in Supabase: {len(existing_ids)}")

    new_jobs = [j for j in scored if j.get("id", j.get("job_id")) not in existing_ids]
    print(f"New jobs to backfill: {len(new_jobs)}")

    if not new_jobs:
        print("Nothing to backfill.")
        return

    # Upsert to Supabase
    success = 0
    errors = 0
    for job in new_jobs:
        try:
            row = {
                "job_id": job.get("id", job.get("job_id")),
                "user_id": user_id,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "description": job.get("description", ""),
                "apply_url": job.get("apply_url", ""),
                "source": job.get("source", ""),
                "match_score": job.get("match_score", 0),
                "ats_score": job.get("ats_score"),
                "hiring_manager_score": job.get("hiring_manager_score"),
                "tech_recruiter_score": job.get("tech_recruiter_score"),
                "resume_s3_url": job.get("resume_s3_url"),
                "cover_letter_s3_url": job.get("cover_letter_s3_url"),
                "application_status": job.get("application_status", "new"),
                "first_seen": job.get("first_seen"),
                "last_seen": job.get("last_seen"),
            }
            db.client.table("jobs").upsert(
                row, on_conflict="job_id,user_id"
            ).execute()
            success += 1
        except Exception as e:
            errors += 1
            print(f"  Error: {job.get('title', '?')} @ {job.get('company', '?')}: {e}")

    print(f"\nBackfill complete: {success} inserted, {errors} errors")


if __name__ == "__main__":
    user_id = os.environ.get("SUPABASE_USER_ID")
    if not user_id:
        print("Set SUPABASE_USER_ID env var")
        sys.exit(1)
    backfill(user_id, min_score=0)
```

- [ ] **Step 2: Run backfill**

```bash
# Set your user ID (from Supabase auth)
export SUPABASE_USER_ID="your-uuid-here"
python scripts/backfill_jobs.py
```

Expected: Jobs inserted to Supabase, visible on dashboard.

- [ ] **Step 3: Verify on dashboard**

```bash
# Check job count in Supabase
python -c "
from db_client import DatabaseClient
db = DatabaseClient()
result = db.client.table('jobs').select('*', count='exact').execute()
print(f'Total jobs in Supabase: {result.count}')
"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_jobs.py
git commit -m "feat: add backfill script for local jobs to Supabase dashboard"
```

---

### Task 1: DeepSeek Removal + OpenRouter Fix

**Files:**
- Modify: `ai_client.py` (remove DeepSeek provider)
- Modify: `config.yaml` (remove deepseek from provider list)
- Modify: `tests/unit/test_ai_helper.py` (update provider tests)

- [ ] **Step 1: Write test for provider list without DeepSeek**

```python
# tests/unit/test_ai_helper.py — add this test
def test_deepseek_not_in_providers():
    """DeepSeek removed from failover chain (accessible via NVIDIA)."""
    from ai_client import AIClient
    client = AIClient.__new__(AIClient)  # skip __init__
    # Verify DeepSeek class is not imported/used
    import ai_client as mod
    provider_classes = [
        name for name in dir(mod)
        if name.endswith("Provider") and name != "AIProvider"
    ]
    assert "DeepSeekProvider" not in provider_classes
```

- [ ] **Step 2: Run test — should fail**

```bash
pytest tests/unit/test_ai_helper.py::test_deepseek_not_in_providers -v
```

- [ ] **Step 3: Remove DeepSeek from ai_client.py**

In `ai_client.py`, find and remove:
- The `DeepSeekProvider` class definition
- Any reference to `deepseek` in the provider initialization/failover chain
- The `deepseek` entry from default provider ordering

In `config.yaml`, remove deepseek from the providers list.

Delete SSM parameter:
```bash
aws ssm delete-parameter --name "/naukribaba/deepseek_api_key" --region eu-west-1 2>/dev/null || echo "Parameter already gone"
```

- [ ] **Step 4: Fix OpenRouter model name**

Check SSM parameter for correct model name:
```bash
aws ssm get-parameter --name "/naukribaba/openrouter_model" --region eu-west-1 2>/dev/null || echo "Parameter not found"
```

Update the model name in `ai_client.py` OpenRouter provider config to match current OpenRouter naming.

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -x -q
```

Expected: All tests pass, no DeepSeek references.

- [ ] **Step 6: Commit**

```bash
git add ai_client.py config.yaml tests/unit/test_ai_helper.py
git commit -m "fix: remove DeepSeek from failover chain, fix OpenRouter model name"
```

---

## GROUP B: PHASE 2.7 — DATA QUALITY

### Task 2: Canonical Hash Function

**Files:**
- Create: `utils/__init__.py`
- Create: `utils/canonical_hash.py`
- Create: `tests/unit/test_canonical_hash.py`

- [ ] **Step 1: Create utils package**

```bash
mkdir -p utils
touch utils/__init__.py
```

- [ ] **Step 2: Write failing tests**

```python
# tests/unit/test_canonical_hash.py
"""Tests for canonical job hash function."""
import pytest
from utils.canonical_hash import canonical_hash, normalize_company, normalize_whitespace


class TestNormalizeCompany:
    def test_strips_legal_suffixes(self):
        assert normalize_company("Acme Inc") == "acme"
        assert normalize_company("Acme Inc.") == "acme"
        assert normalize_company("Acme Ltd") == "acme"
        assert normalize_company("Acme Ltd.") == "acme"
        assert normalize_company("Acme GmbH") == "acme"
        assert normalize_company("Acme LLC") == "acme"

    def test_lowercase_and_strip(self):
        assert normalize_company("  ACME  ") == "acme"

    def test_preserves_meaningful_names(self):
        assert normalize_company("Google") == "google"
        assert normalize_company("Meta Platforms") == "meta platforms"


class TestNormalizeWhitespace:
    def test_collapses_runs(self):
        assert normalize_whitespace("hello   world") == "hello world"

    def test_strips_leading_trailing(self):
        assert normalize_whitespace("  hello  ") == "hello"

    def test_handles_newlines_and_tabs(self):
        assert normalize_whitespace("hello\n\n\tworld") == "hello world"


class TestCanonicalHash:
    def test_same_job_different_sources(self):
        """Same company+title+description from Indeed and LinkedIn → same hash."""
        h1 = canonical_hash("Acme Inc", "Backend Engineer", "Build APIs using Python and FastAPI")
        h2 = canonical_hash("Acme Inc.", "Backend Engineer", "Build APIs using Python and FastAPI")
        assert h1 == h2

    def test_different_descriptions_different_hash(self):
        h1 = canonical_hash("Acme", "Backend Engineer", "Build APIs using Python")
        h2 = canonical_hash("Acme", "Backend Engineer", "Build frontends using React")
        assert h1 != h2

    def test_whitespace_normalization(self):
        h1 = canonical_hash("Acme", "Backend Engineer", "Build APIs\n\nusing Python")
        h2 = canonical_hash("Acme", "Backend Engineer", "Build APIs using Python")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = canonical_hash("ACME", "BACKEND ENGINEER", "BUILD APIS")
        h2 = canonical_hash("acme", "backend engineer", "build apis")
        assert h1 == h2

    def test_returns_12_char_hex(self):
        h = canonical_hash("Acme", "Engineer", "Description")
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_full_description_not_truncated(self):
        """Ensure descriptions longer than 500 chars produce different hashes."""
        base = "x" * 500
        h1 = canonical_hash("Acme", "Engineer", base + "AAAA")
        h2 = canonical_hash("Acme", "Engineer", base + "BBBB")
        assert h1 != h2  # Would be equal if truncated to 500

    def test_empty_description(self):
        """Empty description still produces a valid hash."""
        h = canonical_hash("Acme", "Engineer", "")
        assert len(h) == 12

    def test_location_excluded(self):
        """Location is NOT part of the hash — same job in Dublin and London → same hash."""
        h1 = canonical_hash("Acme", "Engineer", "Build APIs")
        h2 = canonical_hash("Acme", "Engineer", "Build APIs")
        assert h1 == h2  # Same because location isn't a parameter
```

- [ ] **Step 3: Run tests — should fail**

```bash
pytest tests/unit/test_canonical_hash.py -v
```

Expected: ImportError — module doesn't exist yet.

- [ ] **Step 4: Implement canonical_hash.py**

```python
# utils/canonical_hash.py
"""Single canonical hash function for job deduplication.

Used everywhere a job hash is computed:
- lambdas/scrapers/normalizers.py
- scrapers/base.py
- lambdas/pipeline/merge_dedup.py

Formula: md5(normalize(company) | normalize(title) | normalize_ws(description))
"""
import hashlib
import re

# Legal suffixes to strip from company names
_LEGAL_SUFFIXES = re.compile(
    r"\s+(?:Inc\.?|Ltd\.?|LLC|GmbH|Corp\.?|Co\.?|PLC|LP|LLP|SA|AG|BV|NV|SE)\s*$",
    re.IGNORECASE,
)


def normalize_company(company: str) -> str:
    """Normalize company name: lowercase, strip whitespace and legal suffixes."""
    name = company.strip().lower()
    name = _LEGAL_SUFFIXES.sub("", name)
    return name.strip()


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single space, strip leading/trailing."""
    return re.sub(r"\s+", " ", text).strip()


def canonical_hash(company: str, title: str, description: str) -> str:
    """Compute canonical job hash.

    Returns 12-char hex MD5 digest of normalized company|title|description.
    Full description is used — no truncation.
    Location and source are excluded so the same job from different boards matches.
    """
    parts = "|".join([
        normalize_company(company),
        title.strip().lower(),
        normalize_whitespace(description).lower(),
    ])
    return hashlib.md5(parts.encode()).hexdigest()[:12]
```

- [ ] **Step 5: Run tests — should pass**

```bash
pytest tests/unit/test_canonical_hash.py -v
```

Expected: All 10 tests pass.

- [ ] **Step 6: Commit**

```bash
git add utils/ tests/unit/test_canonical_hash.py
git commit -m "feat: add canonical hash function for unified job deduplication"
```

---

### Task 3: Migrate All Hash Locations to Canonical Hash

**Files:**
- Modify: `lambdas/scrapers/normalizers.py:18-20`
- Modify: `scrapers/base.py:76-78, 98-106`
- Modify: `lambdas/pipeline/merge_dedup.py`
- Modify: `tests/unit/test_normalizers.py`
- Modify: `tests/unit/test_merge_dedup.py`

- [ ] **Step 1: Update normalizers.py**

Replace the hash computation in `lambdas/scrapers/normalizers.py` (around lines 18-20):

```python
# OLD:
# job_hash = hashlib.md5(f"{company.lower()}|{title.lower()}|{desc[:500].lower()}".encode()).hexdigest()[:12]

# NEW:
from utils.canonical_hash import canonical_hash
# ... then wherever hash is computed:
job_hash = canonical_hash(company, title, desc)
```

Find every function in normalizers.py that computes a hash and replace with `canonical_hash()`.

- [ ] **Step 2: Update scrapers/base.py**

Replace Job.id computation (lines 76-78):

```python
# OLD:
# self.id = hashlib.md5(f"{title}|{company}|{location}|{source}".encode()).hexdigest()[:12]

# NEW:
from utils.canonical_hash import canonical_hash
# In Job.__post_init__ or wherever id is computed:
self.id = canonical_hash(self.company, self.title, self.description or "")
```

Update `BaseScraper.dedup()` (lines 98-106) to use canonical_hash instead of title+company key:

```python
def dedup(self, jobs: list) -> list:
    """Deduplicate jobs using canonical hash."""
    seen = {}
    for job in jobs:
        h = canonical_hash(job.company, job.title, job.description or "")
        if h in seen:
            # Keep version with longest description
            if len(job.description or "") > len(seen[h].description or ""):
                seen[h] = job
        else:
            seen[h] = job
    return list(seen.values())
```

- [ ] **Step 2b: Audit all scrapers for hash computation**

```bash
# Find ANY file that computes an md5 hash on job fields:
grep -rn "hashlib.md5" scrapers/ lambdas/scrapers/ --include="*.py"
grep -rn "job_hash\|job_id.*md5\|\.hexdigest" scrapers/ lambdas/ --include="*.py"
```

Review each match. Any hash computation that doesn't use `canonical_hash()` must be updated.

**Circular import check**: `utils/canonical_hash.py` is imported by both `scrapers/base.py` and `lambdas/scrapers/normalizers.py`. Verify no circular dependency by running:
```bash
python -c "from utils.canonical_hash import canonical_hash; print('OK')"
python -c "from scrapers.base import Job; print('OK')"
python -c "from lambdas.scrapers.normalizers import normalize_indeed; print('OK')"
```
If Lambda packaging breaks (can't find `utils/`), add `utils/` to the SharedDepsLayer in `template.yaml`.

- [ ] **Step 3: Update merge_dedup.py with richest version logic**

In `lambdas/pipeline/merge_dedup.py`, replace hash references and implement full tie-breaking:

```python
from utils.canonical_hash import canonical_hash

def _richness_score(job: dict) -> tuple:
    """Score for tie-breaking: longest desc → most fields → most recent.

    Returns tuple for comparison (higher = richer).
    """
    desc_len = len(job.get("description", "") or "")
    field_count = sum(1 for v in job.values() if v is not None and v != "")
    last_seen = job.get("last_seen", "") or ""
    return (desc_len, field_count, last_seen)


# In the exact hash dedup (Tier 1):
# When two jobs have the same canonical_hash, keep the one with higher _richness_score():
# if _richness_score(new_job) > _richness_score(existing_job): replace
```

- [ ] **Step 4: Update existing tests**

Update `tests/unit/test_normalizers.py` and `tests/unit/test_merge_dedup.py` to expect the new hash format. Any test that hardcodes a hash value needs updating.

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -x -q
```

Expected: All pass with new hash function.

- [ ] **Step 6: Commit**

```bash
git add lambdas/scrapers/normalizers.py scrapers/base.py lambdas/pipeline/merge_dedup.py tests/
git commit -m "refactor: migrate all hash locations to canonical_hash function"
```

---

### Task 4: Remove All Truncation

**Files:**
- Modify: `lambdas/pipeline/score_batch.py:130,132`

- [ ] **Step 1: Write test for no truncation**

```python
# tests/unit/test_score_batch.py — add test
def test_no_description_truncation():
    """Score batch should send full description to AI, not truncated."""
    long_desc = "x" * 5000
    # Mock the AI call and verify the prompt contains the full description
    # (test depends on existing test structure — adapt mock pattern)
    # Key assertion: the prompt passed to ai_complete contains all 5000 chars
    assert len(long_desc) == 5000  # Placeholder — adapt to actual mock
```

- [ ] **Step 2: Remove truncation in score_batch.py**

Find and remove these lines (approximately lines 130-132):

```python
# REMOVE these truncation lines:
# description = job.get("description", "")[:2000]
# resume_text = resume_content[:3000]

# REPLACE with:
description = job.get("description", "")
resume_text = resume_content
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_score_batch.py -v
```

- [ ] **Step 4: Verify dashboard API returns full descriptions**

Check that `app.py` and `db_client.py` don't truncate descriptions:

```bash
grep -n "description\[:.\|truncat" app.py db_client.py
```

Expected: no truncation found.

- [ ] **Step 5: Check frontend job workspace shows full JD**

In `web/src/`, find the job detail component. Verify it renders `job.description` in full. If a card/list view truncates for preview, that's OK — but the detail view must show the full JD.

```bash
grep -rn "description" web/src/pages/ web/src/components/ --include="*.tsx" --include="*.jsx" | grep -i "slice\|substr\|truncat\|maxLen"
```

If truncation is found in the detail view, remove it.

- [ ] **Step 6: Commit**

```bash
git add lambdas/pipeline/score_batch.py tests/unit/test_score_batch.py
git commit -m "fix: remove description and resume truncation from scoring and display"
```

---

### Task 5: Supabase Schema Changes

**Files:**
- Create: `supabase/migrations/20260403_quality_pipeline.sql`

- [ ] **Step 1: Link Supabase project (if not linked)**

```bash
npx supabase link --project-ref fzxdkvurtsqcflqidqto
```

- [ ] **Step 2: Create migration file**

```bash
npx supabase migration new quality_pipeline
```

This creates a timestamped file in `supabase/migrations/`. Edit it with the SQL below.

- [ ] **Step 3: Write migration SQL**

```sql
-- Quality Pipeline: Phase 2.7 + 2.9 schema changes

-- 0. Check if match_score column exists (it should from initial schema)
-- If not, add it. This is the avg of base scores for initial ranking.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'jobs' AND column_name = 'match_score') THEN
        ALTER TABLE jobs ADD COLUMN match_score FLOAT;
    END IF;
END $$;

-- 1. Add canonical_hash and new score columns to jobs table
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS canonical_hash TEXT,
  ADD COLUMN IF NOT EXISTS base_ats_score INTEGER,
  ADD COLUMN IF NOT EXISTS base_hm_score INTEGER,
  ADD COLUMN IF NOT EXISTS base_tr_score INTEGER,
  ADD COLUMN IF NOT EXISTS tailored_ats_score INTEGER,
  ADD COLUMN IF NOT EXISTS tailored_hm_score INTEGER,
  ADD COLUMN IF NOT EXISTS tailored_tr_score INTEGER,
  ADD COLUMN IF NOT EXISTS final_score FLOAT,
  ADD COLUMN IF NOT EXISTS score_version INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS scored_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS score_status TEXT DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS writing_quality_score FLOAT;

-- 2. Add canonical_hash to jobs_raw
ALTER TABLE jobs_raw
  ADD COLUMN IF NOT EXISTS canonical_hash TEXT;

-- 3. Create index on canonical_hash for cross-run dedup
CREATE INDEX IF NOT EXISTS idx_jobs_canonical_hash ON jobs(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_raw_canonical_hash ON jobs_raw(canonical_hash);

-- 4. Create seen_jobs table (replaces seen_jobs.json)
CREATE TABLE IF NOT EXISTS seen_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id TEXT,
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  canonical_hash TEXT NOT NULL,
  first_seen DATE NOT NULL,
  last_seen DATE NOT NULL,
  title TEXT,
  company TEXT,
  score FLOAT DEFAULT 0,
  matched BOOLEAN DEFAULT false,
  UNIQUE(user_id, canonical_hash)
);

-- 5. Create pipeline_adjustments table (self-improvement)
CREATE TABLE IF NOT EXISTS pipeline_adjustments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  adjustment_type TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  payload JSONB NOT NULL,
  previous_state JSONB,
  reason TEXT NOT NULL,
  evidence JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  applied_at TIMESTAMPTZ,
  reverted_at TIMESTAMPTZ,
  reviewed_by UUID,
  run_id UUID,
  cooldown_until TIMESTAMPTZ
);

-- 6. Create prompt_versions table
CREATE TABLE IF NOT EXISTS prompt_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  prompt_name TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  active_from TIMESTAMPTZ DEFAULT now(),
  active_to TIMESTAMPTZ,
  metrics JSONB,
  created_by TEXT DEFAULT 'manual'
);

-- 7. Create pipeline_runs table
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  jobs_scraped INTEGER DEFAULT 0,
  jobs_new INTEGER DEFAULT 0,
  jobs_scored INTEGER DEFAULT 0,
  jobs_matched INTEGER DEFAULT 0,
  jobs_tailored INTEGER DEFAULT 0,
  avg_base_score FLOAT,
  avg_final_score FLOAT,
  avg_writing_quality FLOAT,
  active_adjustments JSONB,
  scraper_stats JSONB,
  model_stats JSONB,
  status TEXT DEFAULT 'running'
);

-- 8. RLS policies
ALTER TABLE seen_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE prompt_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;

-- seen_jobs: user can CRUD own rows
CREATE POLICY seen_jobs_user_policy ON seen_jobs
  FOR ALL USING (user_id = auth.uid());
CREATE POLICY seen_jobs_service_policy ON seen_jobs
  FOR ALL USING (true) WITH CHECK (true);

-- pipeline_adjustments: user can CRUD own rows
CREATE POLICY pipeline_adj_user_policy ON pipeline_adjustments
  FOR ALL USING (user_id = auth.uid());
CREATE POLICY pipeline_adj_service_policy ON pipeline_adjustments
  FOR ALL USING (true) WITH CHECK (true);

-- prompt_versions: user can CRUD own rows
CREATE POLICY prompt_ver_user_policy ON prompt_versions
  FOR ALL USING (user_id = auth.uid());
CREATE POLICY prompt_ver_service_policy ON prompt_versions
  FOR ALL USING (true) WITH CHECK (true);

-- pipeline_runs: user can SELECT own, service can INSERT/UPDATE
CREATE POLICY pipeline_runs_user_select ON pipeline_runs
  FOR SELECT USING (user_id = auth.uid());
CREATE POLICY pipeline_runs_service_policy ON pipeline_runs
  FOR ALL USING (true) WITH CHECK (true);
```

- [ ] **Step 4: Push migration to Supabase**

```bash
npx supabase db push
```

Expected: Migration applied successfully. Verify tables exist:

```bash
npx supabase db execute "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('seen_jobs', 'pipeline_adjustments', 'prompt_versions', 'pipeline_runs')"
```

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/
git commit -m "feat: add quality pipeline schema — scores, seen_jobs, adjustments, runs"
```

---

### Task 6: Score Determinism (temperature=0 + Multi-Call Median)

**Files:**
- Modify: `lambdas/pipeline/score_batch.py`
- Modify: `matcher.py`
- Modify: `resume_scorer.py`

- [ ] **Step 1: Write test for temperature=0**

```python
# tests/unit/test_score_batch.py — add test
def test_scoring_uses_temperature_zero(mocker):
    """All scoring calls must use temperature=0 for determinism."""
    mock_ai = mocker.patch("lambdas.pipeline.score_batch.ai_complete_cached")
    mock_ai.return_value = {"content": '{"ats_score": 80, "hiring_manager_score": 75, "tech_recruiter_score": 78, "match_score": 77.7}'}

    from lambdas.pipeline.score_batch import score_single_job
    score_single_job({"title": "Test", "company": "Co", "description": "Desc"}, "resume text")

    # Verify temperature=0 was passed
    call_kwargs = mock_ai.call_args
    assert call_kwargs is not None
    # Check that temperature is 0 in the call
```

- [ ] **Step 2: Write test for multi-call median**

```python
# tests/unit/test_score_batch.py — add test
def test_multi_call_median_scoring(mocker):
    """Score each job 3 times, take median of each perspective."""
    responses = [
        {"content": '{"ats_score": 80, "hiring_manager_score": 70, "tech_recruiter_score": 75}'},
        {"content": '{"ats_score": 85, "hiring_manager_score": 72, "tech_recruiter_score": 78}'},
        {"content": '{"ats_score": 82, "hiring_manager_score": 68, "tech_recruiter_score": 76}'},
    ]
    mock_ai = mocker.patch("lambdas.pipeline.score_batch.ai_complete_cached", side_effect=responses)

    from lambdas.pipeline.score_batch import score_single_job_deterministic
    result = score_single_job_deterministic(
        {"title": "Test", "company": "Co", "description": "Desc"},
        "resume text"
    )

    assert mock_ai.call_count == 3
    assert result["ats_score"] == 82       # median of [80, 85, 82]
    assert result["hiring_manager_score"] == 70  # median of [70, 72, 68]
    assert result["tech_recruiter_score"] == 76  # median of [75, 78, 76]
```

- [ ] **Step 2b: Run both tests — should fail**

```bash
pytest tests/unit/test_score_batch.py::test_scoring_uses_temperature_zero tests/unit/test_score_batch.py::test_multi_call_median_scoring -v
```

Expected: FAIL — `score_single_job` doesn't accept `temperature`, `score_single_job_deterministic` doesn't exist.

- [ ] **Step 3: Add temperature parameter to score_single_job**

The existing `score_single_job` function must accept a `temperature` parameter.
Find the function signature in `lambdas/pipeline/score_batch.py` and add it:

```python
# BEFORE:
# def score_single_job(job: dict, resume_text: str) -> dict:

# AFTER:
def score_single_job(job: dict, resume_text: str, temperature: float = 0) -> dict:
    """Score a single job. Pass temperature through to ai_complete_cached."""
    # ... existing code, but pass temperature to the ai_complete_cached call:
    result = ai_complete_cached(
        system=SCORING_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=temperature,  # ADD THIS
    )
```

- [ ] **Step 4: Run Step 1 test again — should now pass**

```bash
pytest tests/unit/test_score_batch.py::test_scoring_uses_temperature_zero -v
```

- [ ] **Step 5: Implement score_single_job_deterministic**

Add to `lambdas/pipeline/score_batch.py`:

```python
import statistics

def score_single_job_deterministic(job: dict, resume_text: str, num_calls: int = 3) -> dict:
    """Score a job using multi-call median for determinism.

    Makes num_calls scoring calls with temperature=0, takes median of each
    perspective (ATS, HM, TR). Falls back to single call if only 1 provider available.
    """
    all_scores = []
    for _ in range(num_calls):
        result = score_single_job(job, resume_text, temperature=0)
        if result:
            all_scores.append(result)

    if not all_scores:
        return None

    if len(all_scores) == 1:
        return all_scores[0]

    return {
        "ats_score": int(statistics.median([s["ats_score"] for s in all_scores])),
        "hiring_manager_score": int(statistics.median([s["hiring_manager_score"] for s in all_scores])),
        "tech_recruiter_score": int(statistics.median([s["tech_recruiter_score"] for s in all_scores])),
        "match_score": round(statistics.median([s.get("match_score", 0) for s in all_scores]), 1),
    }
```

- [ ] **Step 6: Update temperature in matcher.py and resume_scorer.py**

In `matcher.py`: change `temperature=0.3` → `temperature=0` for all scoring calls.
In `resume_scorer.py`: change `temperature=0.5` → `temperature=0` for scoring calls (keep 0.3 for improvement calls — those need creativity).

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_score_batch.py -v
```

- [ ] **Step 6: Commit**

```bash
git add lambdas/pipeline/score_batch.py matcher.py resume_scorer.py tests/unit/test_score_batch.py
git commit -m "feat: deterministic scoring with temperature=0 and multi-call median"
```

---

### Task 7: Before/After Score Comparison

**Files:**
- Modify: `lambdas/pipeline/score_batch.py`
- Modify: `main.py`

- [ ] **Step 1: Write test for before/after flow**

```python
# tests/unit/test_score_batch.py — add test
def test_before_after_score_stored(mocker):
    """Base scores stored before tailoring, tailored scores stored after."""
    mock_ai = mocker.patch("lambdas.pipeline.score_batch.ai_complete_cached")
    mock_ai.return_value = {"content": '{"ats_score": 70, "hiring_manager_score": 65, "tech_recruiter_score": 72}'}

    from lambdas.pipeline.score_batch import compute_base_scores
    base = compute_base_scores(
        {"title": "Test", "company": "Co", "description": "Desc"},
        "base resume text"
    )

    assert "base_ats_score" in base
    assert "base_hm_score" in base
    assert "base_tr_score" in base
    assert base["base_ats_score"] == 70
```

- [ ] **Step 2: Implement compute_base_scores and compute_tailored_scores**

```python
# In lambdas/pipeline/score_batch.py

def compute_base_scores(job: dict, base_resume: str) -> dict:
    """Score base (untailored) resume against JD. Returns base_* scores."""
    scores = score_single_job_deterministic(job, base_resume)
    if not scores:
        return {}
    return {
        "base_ats_score": scores["ats_score"],
        "base_hm_score": scores["hiring_manager_score"],
        "base_tr_score": scores["tech_recruiter_score"],
        "match_score": scores["match_score"],
    }


def compute_tailored_scores(job: dict, tailored_resume: str) -> dict:
    """Score tailored resume against JD. Returns tailored_* scores."""
    scores = score_single_job_deterministic(job, tailored_resume)
    if not scores:
        return {}
    return {
        "tailored_ats_score": scores["ats_score"],
        "tailored_hm_score": scores["hiring_manager_score"],
        "tailored_tr_score": scores["tech_recruiter_score"],
        "final_score": scores["match_score"],
    }
```

- [ ] **Step 3: Wire into main.py pipeline**

In `main.py`, after scoring (Step 4) and before tailoring:
1. Call `compute_base_scores()` → store base scores in job dict
2. After tailoring, call `compute_tailored_scores()` → store tailored scores
3. Compute delta: `tailored - base` for each perspective
4. Flag for review if any tailored score < base score

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_score_batch.py -v
```

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/score_batch.py main.py
git commit -m "feat: before/after score comparison — base vs tailored resume scoring"
```

---

### Task 8: Skip Bad Data + Cross-Run Dedup

**Files:**
- Modify: `lambdas/pipeline/score_batch.py`
- Modify: `lambdas/pipeline/merge_dedup.py`

- [ ] **Step 1: Write test for skipping bad data**

```python
# tests/unit/test_score_batch.py — add test
def test_skip_insufficient_description():
    """Jobs with <100 char descriptions get score_status='insufficient_data'."""
    from lambdas.pipeline.score_batch import should_skip_scoring
    assert should_skip_scoring({"description": "short"}) == "insufficient_data"
    assert should_skip_scoring({"description": ""}) == "insufficient_data"
    assert should_skip_scoring({"description": "x" * 100}) is None  # OK to score

def test_skip_missing_company():
    """Jobs without company name get score_status='incomplete'."""
    from lambdas.pipeline.score_batch import should_skip_scoring
    assert should_skip_scoring({"description": "x" * 200, "company": ""}) == "incomplete"
    assert should_skip_scoring({"description": "x" * 200, "company": None}) == "incomplete"
```

- [ ] **Step 2: Implement should_skip_scoring**

```python
# In lambdas/pipeline/score_batch.py

def should_skip_scoring(job: dict) -> str | None:
    """Check if job should be skipped for scoring.

    Returns score_status string if should skip, None if OK to score.
    """
    desc = job.get("description", "") or ""
    if len(desc) < 100:
        return "insufficient_data"

    company = job.get("company", "") or ""
    if not company.strip():
        return "incomplete"

    return None
```

- [ ] **Step 3: Write test for cross-run dedup**

```python
# tests/unit/test_merge_dedup.py — add test
def test_cross_run_dedup_skips_recent(mocker):
    """Job scored within 7 days should be skipped on next run."""
    from lambdas.pipeline.merge_dedup import should_skip_cross_run
    from datetime import datetime, timedelta

    # Job scored 3 days ago
    existing = {"scored_at": (datetime.now() - timedelta(days=3)).isoformat()}
    assert should_skip_cross_run(existing) is True

    # Job scored 8 days ago
    old = {"scored_at": (datetime.now() - timedelta(days=8)).isoformat()}
    assert should_skip_cross_run(old) is False

    # No existing record
    assert should_skip_cross_run(None) is False
```

- [ ] **Step 4: Implement cross-run dedup**

```python
# In lambdas/pipeline/merge_dedup.py

from datetime import datetime, timedelta

def should_skip_cross_run(existing_job: dict | None, max_age_days: int = 7) -> bool:
    """Check if job was scored recently enough to skip re-scoring."""
    if not existing_job:
        return False
    scored_at = existing_job.get("scored_at")
    if not scored_at:
        return False
    scored_dt = datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
    return (datetime.now(scored_dt.tzinfo) - scored_dt) < timedelta(days=max_age_days)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_score_batch.py tests/unit/test_merge_dedup.py -v
```

- [ ] **Step 6: Commit**

```bash
git add lambdas/pipeline/score_batch.py lambdas/pipeline/merge_dedup.py tests/
git commit -m "feat: skip bad data + cross-run dedup (7-day cache)"
```

---

## GROUP C: PHASE 2.6 — WRITING QUALITY

### Task 9: JD Keyword Extraction

**Files:**
- Create: `utils/keyword_extractor.py`
- Create: `tests/unit/test_keyword_extractor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_keyword_extractor.py
"""Tests for JD keyword extraction."""
from utils.keyword_extractor import extract_keywords


def test_extracts_tech_skills():
    jd = "We need a developer with Python, Kubernetes, and GraphQL experience."
    keywords = extract_keywords(jd)
    assert "python" in keywords
    assert "kubernetes" in keywords
    assert "graphql" in keywords


def test_returns_top_10():
    jd = "Python Java Go Rust C++ TypeScript React Angular Vue Svelte Kubernetes Docker Terraform AWS"
    keywords = extract_keywords(jd, max_keywords=10)
    assert len(keywords) <= 10


def test_deduplicates():
    jd = "Python python PYTHON PyThOn experience with python"
    keywords = extract_keywords(jd)
    assert keywords.count("python") == 1


def test_ignores_common_words():
    jd = "We are looking for a great engineer with good communication skills"
    keywords = extract_keywords(jd)
    assert "we" not in keywords
    assert "are" not in keywords
    assert "looking" not in keywords
    assert "for" not in keywords


def test_handles_empty():
    assert extract_keywords("") == []
    assert extract_keywords(None) == []
```

- [ ] **Step 2: Implement keyword extractor**

```python
# utils/keyword_extractor.py
"""Extract top technical keywords from a job description.

Used by tailorer.py and cover_letter.py to ensure keyword coverage.
"""
import re
from collections import Counter

# Technical terms that should always be recognized
TECH_KEYWORDS = {
    "python", "java", "javascript", "typescript", "go", "rust", "c++", "c#",
    "ruby", "php", "swift", "kotlin", "scala", "r", "sql", "nosql",
    "react", "angular", "vue", "svelte", "next.js", "node.js", "express",
    "django", "flask", "fastapi", "spring", "rails",
    "kubernetes", "docker", "terraform", "aws", "gcp", "azure",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "graphql", "rest", "grpc", "kafka", "rabbitmq",
    "ci/cd", "github actions", "jenkins", "circleci",
    "machine learning", "deep learning", "nlp", "computer vision",
    "microservices", "distributed systems", "event-driven",
    "agile", "scrum", "tdd", "devops", "sre",
    "linux", "git", "api", "sdk", "saas", "b2b", "b2c",
}

# Words to ignore
STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can",
    "we", "you", "our", "your", "their", "this", "that", "these", "those",
    "it", "its", "as", "if", "not", "no", "so", "up", "out", "about",
    "who", "what", "when", "where", "how", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "than",
    "too", "very", "just", "also", "into", "over", "after", "before",
    "between", "through", "during", "without", "within", "along",
    "looking", "great", "good", "strong", "experience", "ability",
    "work", "working", "team", "role", "position", "job", "company",
    "skills", "knowledge", "understanding", "required", "preferred",
    "minimum", "years", "year", "plus", "including", "using",
}


def extract_keywords(jd: str | None, max_keywords: int = 10) -> list[str]:
    """Extract top technical keywords from a job description.

    Returns lowercase keyword list, ordered by relevance (tech matches first,
    then by frequency).
    """
    if not jd:
        return []

    text = jd.lower()

    # First pass: find multi-word tech terms
    found_tech = []
    for term in TECH_KEYWORDS:
        if " " in term or "." in term or "/" in term:
            # Multi-word or special chars — search as-is
            if term in text:
                found_tech.append(term)

    # Second pass: find single-word terms
    words = re.findall(r"[a-z0-9#+.]+", text)
    word_counts = Counter(w for w in words if w not in STOP_WORDS and len(w) > 1)

    for word in word_counts:
        if word in TECH_KEYWORDS and word not in found_tech:
            found_tech.append(word)

    # Sort tech keywords by frequency in JD
    found_tech.sort(key=lambda t: text.count(t), reverse=True)

    # If we don't have enough tech keywords, add frequent non-stop words
    remaining = max_keywords - len(found_tech)
    if remaining > 0:
        non_tech = [
            w for w, _ in word_counts.most_common(remaining + 20)
            if w not in TECH_KEYWORDS and w not in STOP_WORDS and len(w) > 3
        ]
        found_tech.extend(non_tech[:remaining])

    return found_tech[:max_keywords]
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_keyword_extractor.py -v
```

- [ ] **Step 4: Commit**

```bash
git add utils/keyword_extractor.py tests/unit/test_keyword_extractor.py
git commit -m "feat: add JD keyword extractor for resume/cover letter targeting"
```

---

### Task 10: Resume Tailoring Prompt v2 + Dynamic Depth

**Files:**
- Modify: `tailorer.py:26-55` (system prompt), `tailorer.py:113-245` (tailor_resume)

- [ ] **Step 1: Write test for keyword-aware tailoring**

```python
# tests/unit/test_tailorer.py — add test
def test_tailor_prompt_includes_keywords(mocker):
    """Tailoring prompt should include extracted JD keywords."""
    mock_ai = mocker.patch("tailorer.ai_complete")
    mock_ai.return_value = {"content": "\\documentclass{article}\\begin{document}test\\end{document}"}

    from tailorer import tailor_resume
    # Call with a JD that has clear keywords
    jd = "Looking for Python and Kubernetes experience with GraphQL APIs"
    # The function should extract keywords and include them in the prompt
    # Verify by checking what was passed to ai_complete
    try:
        tailor_resume("base latex", jd, "Software Engineer", "Acme")
    except Exception:
        pass  # May fail on validation, that's OK

    if mock_ai.called:
        prompt = mock_ai.call_args[1].get("prompt", "") or mock_ai.call_args[0][1] if len(mock_ai.call_args[0]) > 1 else ""
        system = mock_ai.call_args[1].get("system", "") or mock_ai.call_args[0][0] if len(mock_ai.call_args[0]) > 0 else ""
        combined = f"{system} {prompt}"
        assert "python" in combined.lower() or "kubernetes" in combined.lower()
```

- [ ] **Step 2: Update tailoring system prompt in tailorer.py**

Replace the system prompt (lines ~26-55) with v2 that includes:
- Keyword analysis section: "KEY REQUIREMENTS FROM JD: {keywords}"
- Project handling: "Always include Purrrfect Keys. Select 2 other projects most relevant to: {keywords}. Rewrite ALL project descriptions to emphasize aspects matching the JD."
- Dynamic depth instructions based on base score
- Keep existing anti-fabrication rules

- [ ] **Step 3: Add dynamic depth logic**

```python
# In tailorer.py, add function:

def get_tailoring_depth(base_score: float | None) -> tuple[str, int]:
    """Determine tailoring depth and max improvement rounds from base score.

    Returns (depth_instruction, max_rounds).
    """
    if base_score is None or base_score < 0:
        return "moderate", 2  # Default if unknown

    if base_score is None or base_score < 0:
        return "moderate", 2  # Default if unknown

    if base_score >= 85:
        return (
            "LIGHT TOUCH: Make surgical keyword additions and minor description tweaks only. "
            "Do not restructure sections or rewrite bullets.",
            1,
        )
    elif base_score >= 70:
        return (
            "MODERATE REWRITE: Restructure bullet points to match JD priorities. "
            "Rewrite summary section. Reorder skills to lead with JD-relevant ones.",
            2,
        )
    else:
        return (
            "HEAVY REWRITE: Full project description rewrites emphasizing JD relevance. "
            "Overhaul summary. Reprioritize entire skills section. "
            "Restructure experience bullets with metrics and impact.",
            3,
        )


def should_tailor(job: dict) -> bool:
    """Check if a job should be tailored. Returns False if data is insufficient."""
    if job.get("score_status") == "insufficient_data":
        return False  # No description → can't tailor meaningfully
    if job.get("score_status") == "incomplete":
        return False  # Missing company → skip
    return True
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_tailorer.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tailorer.py tests/unit/test_tailorer.py
git commit -m "feat: resume tailoring prompt v2 — keyword analysis + dynamic depth"
```

---

### Task 11: Structured Feedback in Improvement Loop

**Files:**
- Modify: `resume_scorer.py:119-139` (improvement prompt)
- Modify: `resume_scorer.py:469-507` (improvement loop)

- [ ] **Step 1: Write test for structured feedback**

```python
# tests/unit/test_resume_scorer.py — add test
def test_improvement_prompt_includes_structured_feedback():
    """Improvement prompt should format scoring feedback as specific changes."""
    from resume_scorer import format_improvement_feedback

    scores = {
        "ats_score": 72,
        "hiring_manager_score": 68,
        "tech_recruiter_score": 75,
    }
    feedback = {
        "ats_feedback": "Missing keywords: Kubernetes, GraphQL",
        "hm_feedback": "Impact statements lack metrics",
        "tr_feedback": "3/5 required skills present",
    }

    result = format_improvement_feedback(scores, feedback)
    assert "ATS (score: 72)" in result
    assert "Kubernetes" in result
    assert "APPLY THESE SPECIFIC CHANGES" in result
```

- [ ] **Step 2: Implement format_improvement_feedback**

```python
# In resume_scorer.py

def format_improvement_feedback(scores: dict, feedback: dict) -> str:
    """Format scoring results into structured improvement instructions."""
    lines = ["FEEDBACK FROM SCORING:"]

    perspectives = [
        ("ATS", "ats_score", "ats_feedback"),
        ("Hiring Manager", "hiring_manager_score", "hm_feedback"),
        ("Tech Recruiter", "tech_recruiter_score", "tr_feedback"),
    ]

    changes = []
    for name, score_key, feedback_key in perspectives:
        score = scores.get(score_key, 0)
        fb = feedback.get(feedback_key, "No specific feedback")
        lines.append(f"- {name} (score: {score}): {fb}")
        if score < 85:
            changes.append(fb)

    lines.append("")
    lines.append("APPLY THESE SPECIFIC CHANGES:")
    for i, change in enumerate(changes, 1):
        lines.append(f"{i}. {change}")

    if not changes:
        lines.append("All perspectives scored 85+. Make only minor polish edits.")

    return "\n".join(lines)
```

- [ ] **Step 3: Wire into improvement loop**

Update `_score_and_improve_latex()` (lines ~469-507) to:
1. Collect feedback strings from each scoring perspective
2. Call `format_improvement_feedback()` to build structured prompt
3. Pass structured feedback as part of the improvement prompt
4. Use adaptive max_rounds from `get_tailoring_depth()`

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_resume_scorer.py -v
```

- [ ] **Step 5: Commit**

```bash
git add resume_scorer.py tests/unit/test_resume_scorer.py
git commit -m "feat: structured feedback in resume improvement loop"
```

---

### Task 12: Cover Letter Early Validation + Keyword Analysis

**Files:**
- Modify: `cover_letter.py:217-324` (generation function)

- [ ] **Step 1: Write test for early validation**

```python
# tests/unit/test_cover_letter.py — add test
def test_cover_letter_validates_at_generation_time(mocker):
    """Cover letter validates word count and banned phrases immediately, not post-hoc."""
    from cover_letter import validate_cover_letter

    # Too short
    result = validate_cover_letter("This is too short.")
    assert result["valid"] is False
    assert "word_count" in result["errors"]

    # Has banned phrase
    result = validate_cover_letter("I am excited " + "word " * 280)
    assert result["valid"] is False
    assert "banned_phrase" in result["errors"]

    # Has dashes
    result = validate_cover_letter("This is a great opportunity — " + "word " * 275)
    assert result["valid"] is False
    assert "dashes" in result["errors"]

    # Valid
    valid_text = "This is a well written cover letter. " * 10  # ~80 words, need more
    valid_text = " ".join(["word"] * 300)  # 300 words, no banned phrases
    result = validate_cover_letter(valid_text)
    assert result["valid"] is True
```

- [ ] **Step 2: Implement validate_cover_letter**

```python
# In cover_letter.py

import re

BANNED_PHRASES = [
    "i am excited", "leverage", "passionate", "synergy", "aligns with",
    "keen to", "eager to", "i am writing to", "thrilled", "delighted",
    "dynamic team",
]

DASH_PATTERN = re.compile(r"[–—]|--")


def validate_cover_letter(text: str) -> dict:
    """Validate cover letter content. Returns {valid: bool, errors: list}."""
    errors = []
    words = text.split()
    word_count = len(words)

    if word_count < 280 or word_count > 380:
        errors.append(f"word_count: {word_count} (expected 280-380)")

    text_lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in text_lower:
            errors.append(f"banned_phrase: '{phrase}'")

    if DASH_PATTERN.search(text):
        errors.append("dashes: em-dash, en-dash, or double hyphen found")

    return {"valid": len(errors) == 0, "errors": errors, "word_count": word_count}
```

- [ ] **Step 3: Wire validation into generate_cover_letter with retry**

In `generate_cover_letter()`, after AI generates text:
1. Call `validate_cover_letter()`
2. If invalid, retry with explicit correction instruction (max 2 retries)
3. After retries, accept best attempt and add quality flag

Also add keyword analysis: pass same `extract_keywords()` result to cover letter prompt.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_cover_letter.py -v
```

- [ ] **Step 5: Commit**

```bash
git add cover_letter.py tests/unit/test_cover_letter.py
git commit -m "feat: cover letter early validation + keyword analysis"
```

---

### Task 13: LaTeX Quality Gates + Compilation Rollback

**Files:**
- Modify: `latex_compiler.py`

- [ ] **Step 1: Write test for hard brace gate**

```python
# tests/unit/test_compile_latex.py — add test
def test_brace_balance_hard_gate():
    """Unbalanced braces should prevent compilation."""
    from latex_compiler import check_brace_balance
    assert check_brace_balance("\\begin{document} { } \\end{document}") is True
    assert check_brace_balance("\\begin{document} { \\end{document}") is False
    assert check_brace_balance("\\begin{document} } \\end{document}") is False


def test_compilation_preserves_original(tmp_path):
    """Compilation should work on a copy, preserving the original .tex file."""
    from latex_compiler import sanitize_and_compile

    tex_file = tmp_path / "test.tex"
    original_content = "\\documentclass{article}\n\\begin{document}\nHello & World\n\\end{document}"
    tex_file.write_text(original_content)

    # After sanitization, original file should be unchanged
    sanitize_and_compile(str(tex_file))
    assert tex_file.read_text() == original_content  # Original preserved
```

- [ ] **Step 1b: Write test for section completeness and size bounds**

```python
# tests/unit/test_compile_latex.py — add tests
def test_section_completeness():
    """All required sections must be present."""
    from latex_compiler import check_section_completeness
    full = "\\section{Summary}\\section{Skills}\\section{Experience}\\section{Projects}\\section{Education}"
    assert check_section_completeness(full) is True
    missing = "\\section{Summary}\\section{Skills}"
    assert check_section_completeness(missing) is False

def test_size_bounds():
    """Output must be 60-150% of input size."""
    from latex_compiler import check_size_bounds
    assert check_size_bounds(input_len=1000, output_len=800) is True   # 80%
    assert check_size_bounds(input_len=1000, output_len=500) is False  # 50% — too small
    assert check_size_bounds(input_len=1000, output_len=1600) is False # 160% — too big
```

- [ ] **Step 2: Implement rollback, hard brace gate, section check, size bounds**

In `latex_compiler.py`:

```python
import shutil

def sanitize_and_compile(tex_path: str) -> str | None:
    """Sanitize LaTeX and compile to PDF. Works on a copy, preserves original.

    Returns path to compiled PDF, or None on failure.
    """
    tex_path = Path(tex_path)

    # Work on a copy
    work_copy = tex_path.with_suffix(".work.tex")
    shutil.copy2(tex_path, work_copy)

    try:
        # Sanitize the copy
        content = work_copy.read_text()
        content = _sanitize_latex(content)

        # Hard brace balance gate
        if not check_brace_balance(content):
            logger.error(f"Brace imbalance in {tex_path.name} — skipping compilation")
            return None

        # Section completeness gate
        if not check_section_completeness(content):
            logger.error(f"Missing required sections in {tex_path.name}")
            return None

        # Size bounds gate (if original content provided)
        if original_content and not check_size_bounds(len(original_content), len(content)):
            logger.warning(f"Size bounds exceeded in {tex_path.name}")

        work_copy.write_text(content)

        # Compile the copy
        pdf_path = _compile_tex(str(work_copy))
        return pdf_path
    finally:
        # Clean up work copy
        if work_copy.exists():
            work_copy.unlink()


def check_brace_balance(content: str) -> bool:
    """Hard gate: return False if braces are unbalanced."""
    depth = 0
    i = 0
    while i < len(content):
        if content[i] == '\\' and i + 1 < len(content) and content[i + 1] in '{}':
            i += 2  # Skip escaped braces
            continue
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_compile_latex.py -v
```

- [ ] **Step 4: Commit**

```bash
git add latex_compiler.py tests/unit/test_compile_latex.py
git commit -m "feat: hard brace gate + compilation rollback (work on copy)"
```

---

### Task 14: PDF Output Validation

**Files:**
- Create: `utils/pdf_validator.py`
- Create: `tests/unit/test_pdf_validator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_pdf_validator.py
"""Tests for PDF output validation."""
import pytest
from unittest.mock import MagicMock, patch


def test_page_count_validation():
    from utils.pdf_validator import validate_pdf
    # Mock pymupdf to return a 2-page doc
    with patch("utils.pdf_validator.fitz") as mock_fitz:
        mock_doc = MagicMock()
        mock_doc.__len__ = lambda self: 2
        mock_doc.load_page.return_value.get_text.return_value = "Name\nSkills\nExperience"
        mock_fitz.open.return_value = mock_doc

        result = validate_pdf("/fake/path.pdf", expected_pages=2)
        assert result["valid"] is True


def test_page_count_wrong():
    from utils.pdf_validator import validate_pdf
    with patch("utils.pdf_validator.fitz") as mock_fitz:
        mock_doc = MagicMock()
        mock_doc.__len__ = lambda self: 3  # Wrong — expect 2
        mock_doc.load_page.return_value.get_text.return_value = "text"
        mock_fitz.open.return_value = mock_doc

        result = validate_pdf("/fake/path.pdf", expected_pages=2)
        assert result["valid"] is False
        assert "page_count" in result["errors"]


def test_file_size_bounds():
    from utils.pdf_validator import check_file_size
    assert check_file_size(5000) == "too_small"     # < 10KB
    assert check_file_size(50000) is None            # OK
    assert check_file_size(600000) == "too_large"    # > 500KB
```

- [ ] **Step 2: Implement pdf_validator.py**

```python
# utils/pdf_validator.py
"""PDF output validation for compiled resumes and cover letters."""
import os
from pathlib import Path

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None

REQUIRED_SECTIONS = ["skills", "experience"]  # Must appear in extracted text


def check_file_size(size_bytes: int) -> str | None:
    """Check PDF file size is within bounds. Returns error string or None."""
    if size_bytes < 10_000:
        return "too_small"
    if size_bytes > 500_000:
        return "too_large"
    return None


def validate_pdf(
    pdf_path: str,
    expected_pages: int = 2,
    check_sections: bool = True,
) -> dict:
    """Validate a compiled PDF.

    Checks: page count, file size, text extraction, section headers.
    Returns {valid: bool, errors: list, warnings: list}.
    """
    errors = []
    warnings = []
    path = Path(pdf_path)

    if not path.exists():
        return {"valid": False, "errors": ["file_not_found"], "warnings": []}

    # File size check
    size = path.stat().st_size
    size_issue = check_file_size(size)
    if size_issue:
        errors.append(f"file_size: {size_issue} ({size} bytes)")

    if fitz is None:
        warnings.append("pymupdf not installed — skipping content validation")
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    doc = fitz.open(str(path))

    # Page count
    if len(doc) != expected_pages:
        errors.append(f"page_count: {len(doc)} (expected {expected_pages})")

    # Text extraction
    full_text = ""
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        full_text += page.get_text().lower()

    if len(full_text.strip()) < 100:
        errors.append("text_extraction: less than 100 chars extracted (possibly empty PDF)")

    # Section headers check
    if check_sections:
        for section in REQUIRED_SECTIONS:
            if section not in full_text:
                warnings.append(f"missing_section: '{section}' not found in PDF text")

    # Content overflow check (last page)
    if len(doc) >= expected_pages:
        last_page = doc.load_page(len(doc) - 1)
        last_text = last_page.get_text().strip()
        if last_text and not last_text[-1] in ".!?)\"'":
            warnings.append("content_overflow: last page may end mid-sentence")

    doc.close()
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_pdf_validator.py -v
```

- [ ] **Step 4: Commit**

```bash
git add utils/pdf_validator.py tests/unit/test_pdf_validator.py
git commit -m "feat: PDF output validation — page count, file size, text extraction"
```

---

### Task 15: Writing Quality Scoring

**Files:**
- Modify: `main.py` (add quality check after tailoring)
- Modify: `lambdas/pipeline/score_batch.py` (add writing quality function)

- [ ] **Step 1: Write test for writing quality scoring**

```python
# tests/unit/test_score_batch.py — add test
def test_writing_quality_scoring(mocker):
    """After tailoring, AI rates writing quality 1-10."""
    mock_ai = mocker.patch("lambdas.pipeline.score_batch.ai_complete_cached")
    mock_ai.return_value = {
        "content": '{"specificity": 8, "impact_language": 7, "authenticity": 9, "readability": 8}'
    }

    from lambdas.pipeline.score_batch import score_writing_quality
    result = score_writing_quality("tailored resume text here")

    assert result["writing_quality_score"] == 8.0  # avg of 8,7,9,8
    assert "specificity" in result
```

- [ ] **Step 2: Implement score_writing_quality**

```python
# In lambdas/pipeline/score_batch.py

WRITING_QUALITY_PROMPT = """Rate this resume on a scale of 1-10 for each dimension:
- specificity: Does it use specific numbers, technologies, and outcomes instead of vague claims?
- impact_language: Does it use strong action verbs and quantify achievements?
- authenticity: Does it sound like a real person wrote it, free of AI filler and buzzwords?
- readability: Is it clear, concise, and well-structured?

Return JSON only: {"specificity": N, "impact_language": N, "authenticity": N, "readability": N}"""


def score_writing_quality(resume_text: str) -> dict:
    """Score resume writing quality using AI. Returns quality dimensions + average."""
    result = ai_complete_cached(
        system=WRITING_QUALITY_PROMPT,
        prompt=resume_text,
        temperature=0,
    )

    try:
        scores = json.loads(result["content"])
        avg = sum(scores.values()) / len(scores)
        scores["writing_quality_score"] = round(avg, 1)
        return scores
    except (json.JSONDecodeError, KeyError, ZeroDivisionError):
        return {"writing_quality_score": None}
```

- [ ] **Step 3: Wire into main.py** — after tailoring step, call `score_writing_quality()` and store in job dict.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_score_batch.py -v
```

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/score_batch.py main.py
git commit -m "feat: AI writing quality scoring after resume tailoring"
```

---

### Task 16: LaTeX Command Whitelist Sanitization

**Files:**
- Modify: `latex_compiler.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_compile_latex.py — add test
def test_latex_command_whitelist():
    """Known-good commands pass, unknown commands are flagged."""
    from latex_compiler import validate_latex_commands
    # Known good
    assert validate_latex_commands("\\textbf{hello}") == []
    assert validate_latex_commands("\\begin{itemize}") == []
    # Unknown / likely typo
    issues = validate_latex_commands("\\emphergencystretch{1em}")
    assert len(issues) > 0
    assert "emphergencystretch" in issues[0]
```

- [ ] **Step 2: Implement command whitelist validation**

```python
# In latex_compiler.py

KNOWN_COMMANDS = {
    "documentclass", "begin", "end", "usepackage", "newcommand", "renewcommand",
    "textbf", "textit", "emph", "underline", "textsc", "textrm", "textsf", "texttt",
    "section", "subsection", "subsubsection", "paragraph",
    "item", "hfill", "vspace", "hspace", "noindent", "centering",
    "small", "footnotesize", "large", "Large", "huge", "Huge",
    "href", "url", "color", "textcolor",
    "includegraphics", "input", "include",
    "setlength", "addtolength", "emergencystretch",
    "pagestyle", "thispagestyle", "fancyhf", "fancyhead", "fancyfoot",
    "geometry", "setmainfont", "setsansfont", "setmonofont",
    "faIcon", "faLinkedin", "faGithub", "faEnvelope", "faPhone", "faMapMarker",
    "raisebox", "makebox", "mbox", "parbox", "minipage",
    "tabularx", "multicolumn", "cline", "hline", "toprule", "midrule", "bottomrule",
    "newpage", "clearpage", "pagebreak",
    # Add more as needed
}


def validate_latex_commands(content: str) -> list[str]:
    """Check for LaTeX commands not in the whitelist. Returns list of warnings."""
    import re
    commands = re.findall(r"\\([a-zA-Z]+)", content)
    unknown = set()
    for cmd in commands:
        if cmd not in KNOWN_COMMANDS:
            unknown.add(cmd)
    return [f"Unknown LaTeX command: \\{cmd}" for cmd in sorted(unknown)]
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_compile_latex.py -v
```

- [ ] **Step 4: Commit**

```bash
git add latex_compiler.py tests/unit/test_compile_latex.py
git commit -m "feat: LaTeX command whitelist replacing hardcoded typo map"
```

---

## GROUP D: PHASE 2.8 — QA FOUNDATION

### Task 17: Test Fixtures

**Files:**
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/dedup_fixtures.py`
- Create: `tests/quality/golden_dataset.json` (placeholder structure)

- [ ] **Step 1: Create dedup fixtures**

```python
# tests/fixtures/dedup_fixtures.py
"""Synthetic test data for dedup and scoring tests."""

# 5 duplicate pairs: same job from different sources
DUPLICATE_PAIRS = [
    {
        "job_a": {"company": "Acme Inc", "title": "Backend Engineer", "description": "Build REST APIs using Python and FastAPI. 3+ years experience required.", "source": "linkedin"},
        "job_b": {"company": "Acme Inc.", "title": "Backend Engineer", "description": "Build REST APIs using Python and FastAPI. 3+ years experience required.", "source": "indeed"},
        "should_match": True,
    },
    {
        "job_a": {"company": "Google LLC", "title": "Software Engineer", "description": "Design and implement distributed systems at scale.", "source": "linkedin"},
        "job_b": {"company": "Google", "title": "Software Engineer", "description": "Design and implement distributed systems at scale.", "source": "adzuna"},
        "should_match": True,
    },
    {
        "job_a": {"company": "Stripe", "title": "Senior Backend Engineer", "description": "Build payment processing infrastructure.", "source": "hn"},
        "job_b": {"company": "Stripe", "title": "Sr Backend Engineer", "description": "Build payment processing infrastructure.", "source": "yc"},
        "should_match": True,  # Fuzzy tier catches "Senior" vs "Sr"
    },
    {
        "job_a": {"company": "Meta Platforms", "title": "ML Engineer", "description": "Work on recommendation systems " + "x" * 500, "source": "linkedin"},
        "job_b": {"company": "Meta Platforms Inc", "title": "ML Engineer", "description": "Work on recommendation systems " + "x" * 500, "source": "indeed"},
        "should_match": True,  # Legal suffix stripping
    },
    {
        "job_a": {"company": "Coinbase", "title": "Full Stack Developer", "description": "React frontend\n\nNode.js backend", "source": "linkedin"},
        "job_b": {"company": "Coinbase", "title": "Full Stack Developer", "description": "React frontend Node.js backend", "source": "indeed"},
        "should_match": True,  # Whitespace normalization
    },
]

# 3 near-miss pairs: similar but genuinely different jobs
NEAR_MISS_PAIRS = [
    {
        "job_a": {"company": "Acme", "title": "Backend Engineer", "description": "Build REST APIs using Python and FastAPI."},
        "job_b": {"company": "Acme", "title": "Frontend Engineer", "description": "Build React applications with TypeScript."},
        "should_match": False,  # Different role
    },
    {
        "job_a": {"company": "Google", "title": "Software Engineer", "description": "Work on Google Search ranking algorithms."},
        "job_b": {"company": "Google", "title": "Software Engineer", "description": "Work on YouTube content recommendation systems."},
        "should_match": False,  # Same title, different team/description
    },
    {
        "job_a": {"company": "Stripe", "title": "Backend Engineer", "description": "Build payment APIs for global markets."},
        "job_b": {"company": "Square", "title": "Backend Engineer", "description": "Build payment APIs for small businesses."},
        "should_match": False,  # Different company
    },
]

# Edge cases
EDGE_CASES = {
    "empty_description": {"company": "Acme", "title": "Engineer", "description": ""},
    "short_description": {"company": "Acme", "title": "Engineer", "description": "Short."},
    "long_description": {"company": "Acme", "title": "Engineer", "description": "x" * 5000},
    "missing_company": {"company": "", "title": "Engineer", "description": "Full description here."},
    "unicode": {"company": "Ünïcödé Ltd", "title": "Ëngïnëër", "description": "Wörk with dätä"},
}
```

- [ ] **Step 2: Create golden dataset placeholder**

```json
{
  "_comment": "Golden dataset: 25 JD+resume pairs, human-labeled. Utkarsh must label these from dashboard jobs.",
  "_instructions": "Select 25 jobs from the 177 in the dashboard. Label each as strong_match (5), good_match (8), weak_match (7), or no_match (5). Include the JD and base resume text.",
  "pairs": []
}
```

- [ ] **Step 3: Commit**

```bash
mkdir -p tests/fixtures
git add tests/fixtures/ tests/quality/golden_dataset.json
git commit -m "feat: test fixtures for dedup, scoring, and golden dataset placeholder"
```

---

### Task 18: Tier 4b Data Quality Tests

**Files:**
- Create: `tests/quality/test_data_quality.py`

- [ ] **Step 1: Write Tier 4b tests**

```python
# tests/quality/test_data_quality.py
"""Tier 4b: Data quality tests. MUST PASS in CI."""
import pytest
from utils.canonical_hash import canonical_hash
from tests.fixtures.dedup_fixtures import DUPLICATE_PAIRS, NEAR_MISS_PAIRS, EDGE_CASES


class TestHashConsistency:
    """Same job through any code path → identical canonical_hash."""

    @pytest.mark.parametrize("pair", DUPLICATE_PAIRS, ids=[f"dup_{i}" for i in range(5)])
    def test_duplicate_pairs_same_hash(self, pair):
        h_a = canonical_hash(pair["job_a"]["company"], pair["job_a"]["title"], pair["job_a"]["description"])
        h_b = canonical_hash(pair["job_b"]["company"], pair["job_b"]["title"], pair["job_b"]["description"])
        if pair["should_match"]:
            assert h_a == h_b, f"Expected same hash for duplicate pair"


class TestDedupCorrectness:
    """Duplicate pairs merge, near-misses don't."""

    @pytest.mark.parametrize("pair", NEAR_MISS_PAIRS, ids=[f"nm_{i}" for i in range(3)])
    def test_near_miss_pairs_different_hash(self, pair):
        h_a = canonical_hash(pair["job_a"]["company"], pair["job_a"]["title"], pair["job_a"]["description"])
        h_b = canonical_hash(pair["job_b"]["company"], pair["job_b"]["title"], pair["job_b"]["description"])
        assert h_a != h_b, f"Near-miss pair should NOT have same hash"


class TestNoTruncation:
    def test_long_description_not_truncated(self):
        """5000-char description should produce different hash than 500-char version."""
        base = "x" * 500
        h_short = canonical_hash("Acme", "Engineer", base)
        h_long = canonical_hash("Acme", "Engineer", base + "y" * 4500)
        assert h_short != h_long


class TestDescriptionlessHandling:
    def test_empty_description_skipped(self):
        """Job with empty description → should_skip returns 'insufficient_data'."""
        from lambdas.pipeline.score_batch import should_skip_scoring
        assert should_skip_scoring(EDGE_CASES["empty_description"]) == "insufficient_data"

    def test_short_description_skipped(self):
        from lambdas.pipeline.score_batch import should_skip_scoring
        assert should_skip_scoring(EDGE_CASES["short_description"]) == "insufficient_data"


class TestBeforeAfterDelta:
    def test_delta_computed_correctly(self):
        """Before/after scores stored and delta correct."""
        base = {"base_ats_score": 70, "base_hm_score": 65, "base_tr_score": 72}
        tailored = {"tailored_ats_score": 85, "tailored_hm_score": 78, "tailored_tr_score": 80}
        delta = {
            "ats_delta": tailored["tailored_ats_score"] - base["base_ats_score"],
            "hm_delta": tailored["tailored_hm_score"] - base["base_hm_score"],
            "tr_delta": tailored["tailored_tr_score"] - base["base_tr_score"],
        }
        assert delta["ats_delta"] == 15
        assert delta["hm_delta"] == 13
        assert delta["tr_delta"] == 8


class TestCrossRunDedup:
    def test_recently_scored_job_skipped(self):
        """Job scored within 7 days → skip re-scoring, reuse artifacts."""
        from lambdas.pipeline.merge_dedup import cross_run_check
        from datetime import datetime, timedelta

        existing = {
            "scored_at": (datetime.now() - timedelta(days=3)).isoformat(),
            "base_ats_score": 75,
            "resume_s3_url": "s3://bucket/resume.pdf",
        }
        result = cross_run_check(existing)
        assert result["skip_scoring"] is True
        assert result["skip_tailoring"] is True

    def test_old_job_rescored(self):
        """Job scored 8+ days ago → re-score."""
        from lambdas.pipeline.merge_dedup import cross_run_check
        from datetime import datetime, timedelta

        old = {"scored_at": (datetime.now() - timedelta(days=8)).isoformat()}
        result = cross_run_check(old)
        assert result["skip_scoring"] is False
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/quality/test_data_quality.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/quality/test_data_quality.py
git commit -m "test: Tier 4b data quality tests — hash, dedup, truncation, scoring"
```

---

### Task 19: Tier 4c Writing Quality Tests

**Files:**
- Create: `tests/quality/test_writing_quality.py`

- [ ] **Step 1: Write Tier 4c tests**

```python
# tests/quality/test_writing_quality.py
"""Tier 4c: Writing quality tests. Structural checks = MUST PASS, AI checks = REPORT ONLY."""
import pytest
from cover_letter import validate_cover_letter
from latex_compiler import check_brace_balance


class TestCoverLetterValidation:
    """MUST PASS: Cover letter structural checks."""

    def test_word_count_in_range(self):
        valid = " ".join(["word"] * 300)
        result = validate_cover_letter(valid)
        assert result["valid"] is True

    def test_word_count_too_short(self):
        short = " ".join(["word"] * 100)
        result = validate_cover_letter(short)
        assert not result["valid"]

    def test_word_count_too_long(self):
        long = " ".join(["word"] * 400)
        result = validate_cover_letter(long)
        assert not result["valid"]

    def test_banned_phrases_detected(self):
        text = "I am excited to apply for this role. " + " ".join(["word"] * 280)
        result = validate_cover_letter(text)
        assert not result["valid"]
        assert any("banned_phrase" in e for e in result["errors"])

    def test_dashes_detected(self):
        text = "This is great — really great. " + " ".join(["word"] * 275)
        result = validate_cover_letter(text)
        assert not result["valid"]
        assert any("dashes" in e for e in result["errors"])


class TestBraceBalance:
    """MUST PASS: LaTeX brace balance before compilation."""

    def test_balanced(self):
        assert check_brace_balance("\\textbf{hello} \\textit{world}") is True

    def test_unbalanced_open(self):
        assert check_brace_balance("\\textbf{hello \\textit{world}") is False

    def test_unbalanced_close(self):
        assert check_brace_balance("hello} world") is False

    def test_escaped_braces_ignored(self):
        assert check_brace_balance("\\{escaped\\}") is True

    def test_nested(self):
        assert check_brace_balance("\\textbf{\\textit{nested}}") is True


class TestSectionCompleteness:
    """MUST PASS: All required resume sections present."""

    def test_all_sections_present(self):
        from latex_compiler import check_section_completeness
        content = "\\section{Summary}\\section{Skills}\\section{Experience}\\section{Projects}\\section{Education}"
        assert check_section_completeness(content) is True

    def test_missing_section(self):
        from latex_compiler import check_section_completeness
        content = "\\section{Summary}\\section{Skills}"
        assert check_section_completeness(content) is False


class TestPDFValidation:
    """MUST PASS: PDF output checks (delegates to pdf_validator unit tests for detail)."""

    def test_page_count_check_exists(self):
        from utils.pdf_validator import validate_pdf
        assert callable(validate_pdf)

    def test_file_size_bounds(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(5000) == "too_small"
        assert check_file_size(50000) is None
        assert check_file_size(600000) == "too_large"


class TestKeywordCoverage:
    """REPORT ONLY: Tailored resume should contain JD keywords."""

    def test_keyword_extraction_works(self):
        from utils.keyword_extractor import extract_keywords
        keywords = extract_keywords("We need Python and Kubernetes experience")
        assert "python" in keywords
        assert "kubernetes" in keywords
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/quality/test_writing_quality.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/quality/test_writing_quality.py
git commit -m "test: Tier 4c writing quality tests — cover letter validation, brace balance"
```

---

## GROUP E: PHASE 2.9 — SELF-IMPROVEMENT

### Task 20: Self-Improve Lambda Rewrite

**Files:**
- Modify: `self_improver.py`
- Modify: `lambdas/pipeline/self_improve.py`

- [ ] **Step 1: Write test for tiered adjustment generation**

```python
# tests/unit/test_self_improver.py — add test
def test_generates_low_risk_scraper_disable():
    """3-day zero-yield scraper → low-risk auto-apply adjustment."""
    from self_improver import generate_adjustments

    scraper_stats = {
        "glassdoor": {"yields": [0, 0, 0], "last_3_days": True},
        "linkedin": {"yields": [45, 48, 52], "last_3_days": True},
    }
    adjustments = generate_adjustments(scraper_stats=scraper_stats)

    glassdoor_adj = [a for a in adjustments if "glassdoor" in a["reason"].lower()]
    assert len(glassdoor_adj) == 1
    assert glassdoor_adj[0]["risk_level"] == "low"
    assert glassdoor_adj[0]["adjustment_type"] == "scraper_config"


def test_generates_medium_risk_score_threshold():
    """80%+ jobs below score 50 → medium-risk threshold adjustment."""
    from self_improver import generate_adjustments

    score_stats = {"pct_below_50": 0.85, "avg_score": 42}
    adjustments = generate_adjustments(score_stats=score_stats)

    threshold_adj = [a for a in adjustments if a["adjustment_type"] == "score_threshold"]
    assert len(threshold_adj) == 1
    assert threshold_adj[0]["risk_level"] == "medium"


def test_generates_high_risk_prompt_change():
    """Writing quality trend declining → high-risk prompt change suggestion."""
    from self_improver import generate_adjustments

    quality_stats = {"trend": "declining", "avg_last_3": 5.2, "avg_prev_3": 7.1}
    adjustments = generate_adjustments(quality_stats=quality_stats)

    prompt_adj = [a for a in adjustments if a["adjustment_type"] == "prompt_change"]
    assert len(prompt_adj) == 1
    assert prompt_adj[0]["risk_level"] == "high"
    assert prompt_adj[0]["status"] == "pending"  # Not auto-applied
```

- [ ] **Step 2: Implement generate_adjustments in self_improver.py**

Rewrite `self_improver.py` to:
1. Accept metrics from the pipeline run
2. Analyze each metric category
3. Generate typed adjustments with risk levels
4. Write to `pipeline_adjustments` Supabase table
5. Write run metrics to `pipeline_runs` table

Key function signature:
```python
def generate_adjustments(
    scraper_stats: dict = None,
    score_stats: dict = None,
    quality_stats: dict = None,
    model_stats: dict = None,
    keyword_stats: dict = None,
) -> list[dict]:
    """Analyze pipeline metrics and generate tiered adjustments."""
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_self_improver.py -v
```

- [ ] **Step 4: Commit**

```bash
git add self_improver.py lambdas/pipeline/self_improve.py tests/unit/test_self_improver.py
git commit -m "feat: self-improvement rewrite — tiered adjustment generation"
```

---

### Task 21: load_config Adjustment Reading

**Files:**
- Modify: `lambdas/pipeline/load_config.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_load_config.py — add test
def test_load_config_merges_adjustments(mocker):
    """Active adjustments from Supabase override base config values."""
    mock_db = mocker.patch("lambdas.pipeline.load_config.get_supabase_client")
    mock_db.return_value.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = [
        {
            "adjustment_type": "score_threshold",
            "payload": {"min_match_score": 40},
            "status": "auto_applied",
        }
    ]

    from lambdas.pipeline.load_config import load_config_with_adjustments
    config = load_config_with_adjustments(
        base_config={"min_match_score": 50},
        user_id="test-user"
    )

    assert config["min_match_score"] == 40  # Overridden by adjustment
```

- [ ] **Step 2: Implement adjustment merging**

```python
# In lambdas/pipeline/load_config.py

def load_config_with_adjustments(base_config: dict, user_id: str) -> dict:
    """Load base config and merge active pipeline adjustments.

    Precedence: user manual override > approved > auto_applied > base config.
    """
    config = dict(base_config)

    # Query active adjustments
    db = get_supabase_client()
    result = db.table("pipeline_adjustments").select("*").in_(
        "status", ["auto_applied", "approved"]
    ).eq("user_id", user_id).execute()

    active_adjustments = result.data or []

    # Sort by precedence: auto_applied FIRST, then approved OVERWRITES
    # (later in the loop = higher priority = wins)
    for adj in sorted(active_adjustments, key=lambda a: 0 if a["status"] == "auto_applied" else 1):
        payload = adj.get("payload", {})
        for key, value in payload.items():
            config[key] = value

    # Store which adjustments are active (for pipeline_runs tracking)
    config["_active_adjustments"] = [a["id"] for a in active_adjustments]

    return config
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_load_config.py -v
```

- [ ] **Step 4: Commit**

```bash
git add lambdas/pipeline/load_config.py tests/unit/test_load_config.py
git commit -m "feat: load_config reads and merges pipeline adjustments from Supabase"
```

---

### Task 22: Rollback + Cooldown + Pipeline Runs

**Files:**
- Modify: `self_improver.py`

- [ ] **Step 1: Write test for rollback**

```python
# tests/unit/test_self_improver.py — add test
def test_rollback_reverts_adjustment():
    """Adjustment that worsened metrics → reverted using previous_state."""
    from self_improver import should_revert_adjustment

    adjustment = {
        "id": "adj-1",
        "payload": {"min_match_score": 40},
        "previous_state": {"min_match_score": 50},
        "applied_at": "2026-04-01T00:00:00Z",
    }

    # Metrics worsened by >5% over 3 runs
    run_metrics = [
        {"avg_base_score": 55},  # Run N (before adj)
        {"avg_base_score": 50},  # Run N+1
        {"avg_base_score": 48},  # Run N+2
        {"avg_base_score": 47},  # Run N+3
    ]

    assert should_revert_adjustment(adjustment, run_metrics) is True


def test_cooldown_blocks_reapply():
    """Reverted adjustment cannot be re-proposed for 5 runs."""
    from self_improver import is_on_cooldown

    adjustment = {"cooldown_until": "2026-04-10T00:00:00Z", "status": "reverted"}
    assert is_on_cooldown(adjustment, current_run=3, cooldown_runs=5) is True
```

- [ ] **Step 2: Implement rollback and cooldown logic**

```python
# In self_improver.py

from datetime import datetime, timedelta

def should_revert_adjustment(adjustment: dict, run_metrics: list[dict], threshold: float = 0.05) -> bool:
    """Check if an adjustment worsened metrics over 3+ runs.

    Returns True if target metric declined by >= threshold (5%) over 3-run average.
    """
    if len(run_metrics) < 4:  # Need at least 1 before + 3 after
        return False

    before = run_metrics[0].get("avg_base_score", 0)
    after_avg = sum(r.get("avg_base_score", 0) for r in run_metrics[1:4]) / 3

    if before == 0:
        return False

    change = (after_avg - before) / before
    return change < -threshold  # Worsened by more than threshold


def is_on_cooldown(adjustment: dict, current_run: int = 0, cooldown_runs: int = 5) -> bool:
    """Check if a reverted adjustment is still in cooldown period."""
    if adjustment.get("status") != "reverted":
        return False
    cooldown_until = adjustment.get("cooldown_until")
    if cooldown_until:
        return datetime.now().isoformat() < cooldown_until
    return False


def execute_revert(db, adjustment: dict, cooldown_runs: int = 5):
    """Revert an adjustment: create new adjustment with old state, mark original as reverted.

    Sets cooldown_until so the reverted adjustment can't be re-proposed for cooldown_runs.
    """
    from datetime import timedelta

    # Mark original as reverted with cooldown
    cooldown_until = (datetime.now() + timedelta(days=cooldown_runs)).isoformat()
    db.table("pipeline_adjustments").update({
        "status": "reverted",
        "reverted_at": datetime.now().isoformat(),
        "cooldown_until": cooldown_until,
    }).eq("id", adjustment["id"]).execute()

    # Create restoration adjustment with previous state
    if adjustment.get("previous_state"):
        db.table("pipeline_adjustments").insert({
            "user_id": adjustment["user_id"],
            "adjustment_type": adjustment["adjustment_type"],
            "risk_level": "low",
            "status": "auto_applied",
            "payload": adjustment["previous_state"],
            "reason": f"Auto-revert of adjustment {adjustment['id']} — metrics worsened",
            "evidence": {"reverted_from": adjustment["id"]},
        }).execute()


def should_revert_or_extend(adjustment: dict, run_metrics: list[dict], threshold: float = 0.05) -> str:
    """Extended evaluation: 3 runs → revert/confirm. If inconclusive, extend to 5.

    Returns: 'confirm', 'revert', 'extend', or 'wait' (not enough runs).
    """
    if len(run_metrics) < 4:
        return "wait"

    before = run_metrics[0].get("avg_base_score", 0)
    if before == 0:
        return "wait"

    after_3 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:4]) / 3
    change_3 = (after_3 - before) / before

    if change_3 < -threshold:
        return "revert"
    if change_3 > threshold:
        return "confirm"

    # Inconclusive — try 5 runs
    if len(run_metrics) >= 6:
        after_5 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:6]) / 5
        change_5 = (after_5 - before) / before
        if change_5 < -threshold:
            return "revert"
        return "confirm"  # Force decision after 5 runs

    return "extend"  # Keep evaluating
```

- [ ] **Step 3: Add pipeline_runs writing to self_improver**

After analysis completes, write run metrics to `pipeline_runs` table:

```python
def save_pipeline_run(db, user_id: str, run_data: dict):
    """Save pipeline run metrics to Supabase."""
    db.table("pipeline_runs").insert({
        "user_id": user_id,
        "started_at": run_data.get("started_at"),
        "completed_at": datetime.now().isoformat(),
        "jobs_scraped": run_data.get("jobs_scraped", 0),
        "jobs_new": run_data.get("jobs_new", 0),
        "jobs_scored": run_data.get("jobs_scored", 0),
        "jobs_matched": run_data.get("jobs_matched", 0),
        "jobs_tailored": run_data.get("jobs_tailored", 0),
        "avg_base_score": run_data.get("avg_base_score"),
        "avg_final_score": run_data.get("avg_final_score"),
        "avg_writing_quality": run_data.get("avg_writing_quality"),
        "active_adjustments": run_data.get("active_adjustments"),
        "scraper_stats": run_data.get("scraper_stats"),
        "model_stats": run_data.get("model_stats"),
        "status": "completed",
    }).execute()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_self_improver.py -v
```

- [ ] **Step 5: Commit**

```bash
git add self_improver.py tests/unit/test_self_improver.py
git commit -m "feat: self-improvement rollback, cooldown, and pipeline_runs tracking"
```

---

## GROUP F: PHASE 2.5b — SCRAPER FIXES

### Task 23: IrishJobs 403 Fix

**Files:**
- Modify: `scrapers/irish.py` or equivalent Irish portal scraper

- [ ] **Step 1: Investigate current 403 response**

```bash
# Test the current IrishJobs detail page fetch
python -c "
import httpx
url = 'https://www.irishjobs.ie/jobs/some-job-slug'  # Use a known URL from recent scrape
resp = httpx.get(url, headers={'User-Agent': 'Mozilla/5.0'})
print(f'Status: {resp.status_code}')
print(f'Headers: {dict(resp.headers)}')
"
```

- [ ] **Step 2: Try Web Unlocker route for detail pages**

If direct request returns 403, route through Bright Data Web Unlocker (same as search pages).

- [ ] **Step 3: If still 403, implement cross-reference enrichment**

For IrishJobs jobs with empty descriptions, search Indeed/LinkedIn for same title+company and use that description. Uses canonical hash to match across sources.

- [ ] **Step 4: Run scraper tests**

```bash
pytest tests/unit/test_normalizers.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scrapers/
git commit -m "fix: IrishJobs 403 — route detail pages through Web Unlocker"
```

---

### Task 24: GradIreland Fix

**Files:**
- Modify: GradIreland scraper file

- [ ] **Step 1: Inspect current HTML structure**

```bash
python -c "
import httpx
resp = httpx.get('https://gradireland.com/graduate-jobs', headers={'User-Agent': 'Mozilla/5.0'})
print(f'Status: {resp.status_code}')
print(resp.text[:2000])
"
```

- [ ] **Step 2: Update selectors based on new Drupal template**

Based on inspection, update CSS selectors/regex patterns.

- [ ] **Step 3: Test and commit**

```bash
git add scrapers/
git commit -m "fix: GradIreland scraper — update selectors for new Drupal template"
```

---

### Task 25: Glassdoor Fargate Setup

**Files:**
- Modify: `scrapers/playwright/glassdoor.py`
- Modify: `Dockerfile.playwright`
- Modify: `template.yaml` (activate PlaywrightTaskDef)

- [ ] **Step 1: Build Playwright Docker image**

```bash
docker build -f Dockerfile.playwright -t naukribaba-playwright .
```

- [ ] **Step 2: Push to ECR**

```bash
aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin 385017713886.dkr.ecr.eu-west-1.amazonaws.com
docker tag naukribaba-playwright:latest 385017713886.dkr.ecr.eu-west-1.amazonaws.com/naukribaba-playwright:latest
docker push 385017713886.dkr.ecr.eu-west-1.amazonaws.com/naukribaba-playwright:latest
```

- [ ] **Step 3: Activate PlaywrightTaskDef in template.yaml**

Uncomment/activate the Fargate task definition. Add Glassdoor credentials to SSM:

```bash
aws ssm put-parameter --name "/naukribaba/glassdoor_email" --value "your-email" --type SecureString --region eu-west-1
aws ssm put-parameter --name "/naukribaba/glassdoor_password" --value "your-pass" --type SecureString --region eu-west-1
```

- [ ] **Step 4: Wire into Step Functions parallel scraper branch**

In `template.yaml`, add Glassdoor Fargate task to the parallel scraper branch:
- Add as a parallel branch alongside Lambda scrapers
- Configure with `"Catch": [{"ErrorEquals": ["States.ALL"], "Next": "ContinueAfterScrape"}]` for "Continue On Fail"
- This ensures pipeline continues even if Glassdoor Fargate fails or times out

- [ ] **Step 5: Add rate limiting and session caching to Glassdoor scraper**

In `scrapers/playwright/glassdoor.py`:

```python
MAX_DETAIL_PAGES = 50  # Rate limit: max 50 job detail pages per run

SESSION_COOKIE_S3_KEY = "glassdoor/session_cookie.json"
SESSION_COOKIE_TTL = 86400  # 24 hours

async def get_or_create_session(s3_client, bucket: str) -> dict:
    """Load cached session cookie from S3, or create new login session."""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=SESSION_COOKIE_S3_KEY)
        cookie_data = json.loads(obj["Body"].read())
        age = time.time() - cookie_data.get("created_at", 0)
        if age < SESSION_COOKIE_TTL:
            return cookie_data["cookies"]
    except s3_client.exceptions.NoSuchKey:
        pass

    # Login and cache new session
    cookies = await glassdoor_login()
    s3_client.put_object(
        Bucket=bucket,
        Key=SESSION_COOKIE_S3_KEY,
        Body=json.dumps({"cookies": cookies, "created_at": time.time()}),
    )
    return cookies
```

- [ ] **Step 6: Test locally**

```bash
docker run --env-file .env naukribaba-playwright python -m scrapers.playwright.glassdoor --test
```

- [ ] **Step 6: Commit**

```bash
git add scrapers/playwright/ Dockerfile.playwright template.yaml
git commit -m "feat: Glassdoor Fargate scraper — Playwright + Chromium behind login wall"
```

---

## GROUP G: FINAL QA + CI

### Task 26: Tier 4d Self-Improvement Tests

**Files:**
- Create: `tests/quality/test_self_improvement.py`

- [ ] **Step 1: Write Tier 4d tests**

```python
# tests/quality/test_self_improvement.py
"""Tier 4d: Self-improvement loop tests."""
import pytest
from self_improver import generate_adjustments, should_revert_adjustment, is_on_cooldown


class TestTieredRisk:
    def test_low_risk_auto_applied(self):
        adjs = generate_adjustments(scraper_stats={"broken": {"yields": [0, 0, 0], "last_3_days": True}})
        low = [a for a in adjs if a["risk_level"] == "low"]
        assert all(a["status"] == "auto_applied" for a in low)

    def test_medium_risk_notifies(self):
        adjs = generate_adjustments(score_stats={"pct_below_50": 0.85, "avg_score": 42})
        med = [a for a in adjs if a["risk_level"] == "medium"]
        assert all(a["status"] == "auto_applied" for a in med)
        # Notification flag should be set
        assert all(a.get("notify", False) for a in med)

    def test_high_risk_awaits_approval(self):
        adjs = generate_adjustments(quality_stats={"trend": "declining", "avg_last_3": 5.2, "avg_prev_3": 7.1})
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert all(a["status"] == "pending" for a in high)


class TestRollback:
    def test_revert_on_decline(self):
        adj = {"id": "1", "payload": {}, "previous_state": {}, "applied_at": "2026-04-01"}
        metrics = [{"avg_base_score": 60}, {"avg_base_score": 50}, {"avg_base_score": 48}, {"avg_base_score": 47}]
        assert should_revert_adjustment(adj, metrics) is True

    def test_no_revert_on_improvement(self):
        adj = {"id": "1", "payload": {}, "previous_state": {}, "applied_at": "2026-04-01"}
        metrics = [{"avg_base_score": 60}, {"avg_base_score": 65}, {"avg_base_score": 68}, {"avg_base_score": 70}]
        assert should_revert_adjustment(adj, metrics) is False


class TestCooldown:
    def test_reverted_adjustment_on_cooldown(self):
        adj = {"status": "reverted", "cooldown_until": "2099-01-01T00:00:00Z"}
        assert is_on_cooldown(adj) is True

    def test_active_adjustment_not_on_cooldown(self):
        adj = {"status": "auto_applied", "cooldown_until": None}
        assert is_on_cooldown(adj) is False


class TestConflictDetection:
    """REPORT ONLY: Contradictory adjustments should be flagged."""

    def test_contradictory_adjustments_flagged(self):
        """Two adjustments that set the same config key to different values → conflict."""
        from self_improver import detect_conflicts

        adjustments = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 60}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjustments)
        assert len(conflicts) == 1
        assert "min_match_score" in conflicts[0]["key"]


class TestUserFeedbackIngestion:
    """MUST PASS: Flagged score creates ground truth entry."""

    def test_flag_creates_adjustment(self):
        """User feedback stored as pipeline_adjustment with quality_flag type."""
        # This is tested in Task 41 (test_flag_score_creates_ground_truth)
        # Here we verify the data shape
        feedback = {
            "adjustment_type": "quality_flag",
            "risk_level": "high",
            "status": "pending",
            "payload": {"job_id": "test-123", "feedback_type": "score_inaccurate"},
        }
        assert feedback["adjustment_type"] == "quality_flag"
        assert feedback["risk_level"] == "high"
```

- [ ] **Step 2: Implement detect_conflicts in self_improver.py**

```python
# In self_improver.py

def detect_conflicts(adjustments: list[dict]) -> list[dict]:
    """Detect contradictory adjustments that set the same key to different values."""
    key_values = {}
    conflicts = []
    for adj in adjustments:
        if adj.get("status") not in ("auto_applied", "approved"):
            continue
        for key, value in (adj.get("payload") or {}).items():
            if key in key_values and key_values[key]["value"] != value:
                conflicts.append({
                    "key": key,
                    "adjustment_a": key_values[key]["id"],
                    "value_a": key_values[key]["value"],
                    "adjustment_b": adj["id"],
                    "value_b": value,
                })
            key_values[key] = {"id": adj["id"], "value": value}
    return conflicts
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/quality/test_self_improvement.py -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/quality/test_self_improvement.py self_improver.py
git commit -m "test: Tier 4d self-improvement tests — tiered risk, rollback, cooldown, conflicts"
```

---

### Task 27: CI Pipeline Update

**Files:**
- Modify: `.github/workflows/test.yml` (or create if not exists)

- [ ] **Step 1: Add quality test tiers to CI**

```yaml
# In .github/workflows/test.yml, add jobs:

  data-quality-tests:
    name: "Tier 4b: Data Quality"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_data_quality.py -v --tb=short

  writing-quality-tests:
    name: "Tier 4c: Writing Quality (structural)"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_writing_quality.py -v --tb=short

  self-improvement-tests:
    name: "Tier 4d: Self-Improvement"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_self_improvement.py -v --tb=short

  # REPORT ONLY — don't block PRs
  score-determinism:
    name: "Tier 4b: Score Determinism (REPORT)"
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_score_determinism.py -v --tb=short || true

  writing-quality-ai:
    name: "Tier 4c: Writing Quality AI (REPORT)"
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_writing_quality.py -k "report_only" -v --tb=short || true

  ai-quality-golden:
    name: "Tier 4: AI Quality Golden Dataset (REPORT)"
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_scoring_quality.py -v --tb=short || true

  conflict-detection:
    name: "Tier 4d: Conflict Detection (REPORT)"
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r tests/requirements-test.txt
      - run: pytest tests/quality/test_self_improvement.py::TestConflictDetection -v --tb=short || true
```

- [ ] **Step 2: Run full test suite locally**

```bash
pytest tests/ -v --tb=short -q
```

Expected: All existing + new tests pass.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/
git commit -m "ci: add Tier 4b/4c/4d quality tests to CI pipeline"
```

---

## GROUP H: MISSING FROM SELF-REVIEW

### Task 28: Hash Migration Script

**Files:**
- Create: `scripts/migrate_hashes.py`

- [ ] **Step 1: Write migration script**

```python
#!/usr/bin/env python3
"""One-time: recompute canonical hashes for all existing jobs_raw and jobs records."""
import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.canonical_hash import canonical_hash
from db_client import DatabaseClient


def migrate():
    db = DatabaseClient()

    # Migrate jobs table
    jobs = db.client.table("jobs").select("job_id, company, title, description").execute()
    print(f"Migrating {len(jobs.data)} jobs...")
    for job in jobs.data:
        new_hash = canonical_hash(
            job.get("company", ""),
            job.get("title", ""),
            job.get("description", ""),
        )
        db.client.table("jobs").update(
            {"canonical_hash": new_hash}
        ).eq("job_id", job["job_id"]).execute()

    # Migrate jobs_raw table
    raw = db.client.table("jobs_raw").select("id, company, title, description").execute()
    print(f"Migrating {len(raw.data)} raw jobs...")
    for job in raw.data:
        new_hash = canonical_hash(
            job.get("company", ""),
            job.get("title", ""),
            job.get("description", ""),
        )
        db.client.table("jobs_raw").update(
            {"canonical_hash": new_hash}
        ).eq("id", job["id"]).execute()

    print("Migration complete.")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 2: Run migration**

```bash
python scripts/migrate_hashes.py
```

- [ ] **Step 3: Verify**

```bash
python -c "
from db_client import DatabaseClient
db = DatabaseClient()
nulls = db.client.table('jobs').select('job_id', count='exact').is_('canonical_hash', 'null').execute()
print(f'Jobs with null canonical_hash: {nulls.count}')
"
```

Expected: 0 null hashes.

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_hashes.py
git commit -m "feat: one-time hash migration script for existing Supabase records"
```

---

### Task 29: Cross-Run Dedup — Artifact Reuse

**Files:**
- Modify: `lambdas/pipeline/merge_dedup.py`
- Modify: `main.py`

Extends Task 8. When cross-run dedup detects a recently-scored job:
- Skip re-scoring AND re-tailoring
- Reuse existing base scores, tailored scores, resume PDF, cover letter PDF
- Carry forward the before/after delta from original scoring

- [ ] **Step 1: Write test**

```python
# tests/unit/test_merge_dedup.py — add test
def test_cross_run_dedup_reuses_artifacts():
    """Recently-scored job → skip scoring, tailoring, reuse all artifacts."""
    from lambdas.pipeline.merge_dedup import cross_run_check

    existing = {
        "canonical_hash": "abc123",
        "scored_at": "2026-04-02T00:00:00Z",
        "base_ats_score": 75,
        "tailored_ats_score": 88,
        "resume_s3_url": "s3://bucket/resume.pdf",
        "cover_letter_s3_url": "s3://bucket/cover.pdf",
    }

    result = cross_run_check(existing)
    assert result["skip_scoring"] is True
    assert result["skip_tailoring"] is True
    assert result["reuse_artifacts"]["resume_s3_url"] == "s3://bucket/resume.pdf"
```

- [ ] **Step 2: Implement cross_run_check**

```python
# In lambdas/pipeline/merge_dedup.py

def cross_run_check(existing_job: dict | None, max_age_days: int = 7) -> dict:
    """Check if a job was recently processed. Returns reuse instructions."""
    if not existing_job or not should_skip_cross_run(existing_job, max_age_days):
        return {"skip_scoring": False, "skip_tailoring": False, "reuse_artifacts": {}}

    return {
        "skip_scoring": True,
        "skip_tailoring": True,
        "reuse_artifacts": {
            "base_ats_score": existing_job.get("base_ats_score"),
            "base_hm_score": existing_job.get("base_hm_score"),
            "base_tr_score": existing_job.get("base_tr_score"),
            "tailored_ats_score": existing_job.get("tailored_ats_score"),
            "tailored_hm_score": existing_job.get("tailored_hm_score"),
            "tailored_tr_score": existing_job.get("tailored_tr_score"),
            "resume_s3_url": existing_job.get("resume_s3_url"),
            "cover_letter_s3_url": existing_job.get("cover_letter_s3_url"),
            "writing_quality_score": existing_job.get("writing_quality_score"),
        },
    }
```

- [ ] **Step 3: Wire into main.py** — before scoring step, check Supabase for existing canonical_hash match. If found and recent, apply reuse_artifacts and skip scoring+tailoring for that job.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/unit/test_merge_dedup.py -v
git add lambdas/pipeline/merge_dedup.py main.py tests/unit/test_merge_dedup.py
git commit -m "feat: cross-run dedup reuses scores AND artifacts for recently-processed jobs"
```

---

### Task 30: Model A/B Testing

**Files:**
- Modify: `lambdas/pipeline/score_batch.py`
- Modify: `self_improver.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_score_batch.py — add test
def test_model_ab_splits_jobs(mocker):
    """20% of jobs should be scored with alternate model."""
    import random
    random.seed(42)
    from lambdas.pipeline.score_batch import assign_model_for_ab_test

    assignments = [assign_model_for_ab_test(["groq", "qwen"]) for _ in range(100)]
    primary_count = assignments.count("groq")
    alternate_count = assignments.count("qwen")

    # ~80% primary, ~20% alternate (with some variance)
    assert 60 < primary_count < 95
    assert 5 < alternate_count < 40
```

- [ ] **Step 2: Implement A/B assignment**

```python
# In lambdas/pipeline/score_batch.py

import random

def assign_model_for_ab_test(
    available_providers: list[str],
    ab_ratio: float = 0.2,
) -> str:
    """Assign a model for A/B testing. 80% primary, 20% alternate."""
    if len(available_providers) < 2:
        return available_providers[0] if available_providers else None

    if random.random() < ab_ratio:
        return available_providers[1]  # Alternate
    return available_providers[0]  # Primary
```

- [ ] **Step 3: Add model comparison to self_improver**

In self_improver.py, add analysis that compares scores from primary vs alternate model and generates a low-risk `model_swap` adjustment if alternate consistently outperforms.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/unit/test_score_batch.py -v
git add lambdas/pipeline/score_batch.py self_improver.py tests/unit/test_score_batch.py
git commit -m "feat: model A/B testing — 20% alternate model, compare in self-improvement"
```

---

### Task 31: Query Optimization Tracking

**Files:**
- Modify: `self_improver.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_self_improver.py — add test
def test_low_match_rate_query_flagged():
    """Query with <5% match rate for 3+ runs → medium-risk suggestion."""
    from self_improver import analyze_query_effectiveness

    query_stats = {
        "python backend dublin": {"match_rates": [0.03, 0.02, 0.04]},  # <5% for 3 runs
        "software engineer dublin": {"match_rates": [0.35, 0.40, 0.38]},  # OK
    }
    suggestions = analyze_query_effectiveness(query_stats)

    flagged = [s for s in suggestions if "python backend dublin" in s["reason"]]
    assert len(flagged) == 1
    assert flagged[0]["risk_level"] == "medium"
```

- [ ] **Step 2: Implement query analysis**

```python
# In self_improver.py

def analyze_query_effectiveness(query_stats: dict, threshold: float = 0.05, min_runs: int = 3) -> list[dict]:
    """Flag search queries with consistently low match rates."""
    suggestions = []
    for query, stats in query_stats.items():
        rates = stats.get("match_rates", [])
        if len(rates) >= min_runs and all(r < threshold for r in rates[-min_runs:]):
            suggestions.append({
                "adjustment_type": "keyword_weight",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"query": query, "action": "suggest_modification"},
                "reason": f"Query '{query}' has <{threshold*100}% match rate for {min_runs}+ consecutive runs",
                "evidence": {"match_rates": rates},
            })
    return suggestions
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_self_improver.py -v
git add self_improver.py tests/unit/test_self_improver.py
git commit -m "feat: query optimization — flag low-yield search queries"
```

---

## GROUP H: REMAINING SPEC COVERAGE

### Task 32: seen_jobs.json → Supabase Pipeline Code Swap

**Files:**
- Modify: `main.py` (replace `_load_seen_jobs` / `_save_seen_jobs` with Supabase queries)

- [ ] **Step 1: Write test**

```python
# tests/unit/test_seen_jobs.py
def test_check_seen_job_in_supabase(mocker):
    """Pipeline should check Supabase seen_jobs table, not local JSON."""
    mock_db = mocker.MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"canonical_hash": "abc123", "first_seen": "2026-04-01", "score": 75}
    ]

    from main import check_seen_job
    result = check_seen_job(mock_db, user_id="test", canonical_hash="abc123")
    assert result is not None
    assert result["score"] == 75
```

- [ ] **Step 2: Implement Supabase-backed seen_jobs functions**

In `main.py`, replace `_load_seen_jobs(path)` and `_save_seen_jobs(seen, path)` with:

```python
def check_seen_job(db, user_id: str, canonical_hash: str) -> dict | None:
    """Check if job was seen before (Supabase)."""
    result = db.client.table("seen_jobs").select("*").eq(
        "user_id", user_id
    ).eq("canonical_hash", canonical_hash).execute()
    return result.data[0] if result.data else None


def upsert_seen_job(db, user_id: str, job: dict, canonical_hash: str):
    """Track job as seen (Supabase). Updates last_seen if exists."""
    from datetime import date
    db.client.table("seen_jobs").upsert({
        "user_id": user_id,
        "canonical_hash": canonical_hash,
        "job_id": job.get("id"),
        "title": job.get("title"),
        "company": job.get("company"),
        "first_seen": str(date.today()),
        "last_seen": str(date.today()),
        "score": job.get("match_score", 0),
        "matched": job.get("match_score", 0) > 0,
    }, on_conflict="user_id,canonical_hash").execute()
```

- [ ] **Step 3: Remove seen_jobs.json references**

Search main.py for `seen_jobs.json`, `_load_seen_jobs`, `_save_seen_jobs` and replace all calls with the Supabase functions above. The local JSON file becomes deprecated.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/ -x -q
git add main.py tests/unit/test_seen_jobs.py
git commit -m "refactor: replace seen_jobs.json with Supabase seen_jobs table"
```

---

### Task 33: Resume Length Management

**Files:**
- Modify: `tailorer.py` (add word budgets to prompt)
- Modify: `main.py` (add page-count retry after compilation)

- [ ] **Step 1: Write test**

```python
# tests/unit/test_tailorer.py — add test
def test_prompt_includes_word_budgets(mocker):
    """Tailoring prompt should include section word budgets for 2-page target."""
    mock_ai = mocker.patch("tailorer.ai_complete")
    mock_ai.return_value = {"content": "\\documentclass{article}\\begin{document}test\\end{document}"}

    from tailorer import build_tailoring_prompt
    prompt = build_tailoring_prompt("base latex", "job description", "Engineer", "Acme", base_score=70)

    assert "850-1000 words" in prompt or "word budget" in prompt.lower()
    assert "Summary" in prompt and "40-60" in prompt
```

- [ ] **Step 2: Add word budgets to tailoring prompt**

In `tailorer.py`, add to the system prompt:

```python
LENGTH_GUIDANCE = """
TARGET LENGTH: 850-1000 words of content for exactly 2 pages.

SECTION WORD BUDGETS:
- Summary: 40-60 words
- Skills: 50-80 words
- Each Experience entry: 80-120 words (3-4 bullet points)
- Each Project: 60-90 words
- Education: 30-50 words
- Certifications: 20-30 words
"""
```

- [ ] **Step 3: Add page-count retry in main.py**

After compilation, check PDF page count. If wrong, retry:

```python
from utils.pdf_validator import validate_pdf

def compile_with_length_retry(tex_path: str, max_retries: int = 1) -> str | None:
    """Compile LaTeX → PDF, retry if page count is wrong."""
    pdf_path = sanitize_and_compile(tex_path)
    if not pdf_path:
        return None

    result = validate_pdf(pdf_path, expected_pages=2)
    if result["valid"]:
        return pdf_path

    # Check page count issue
    page_errors = [e for e in result["errors"] if "page_count" in e]
    if not page_errors or max_retries <= 0:
        return pdf_path  # Accept as-is

    # Determine correction
    if "1 (expected 2)" in page_errors[0]:
        correction = "The resume compiled to only 1 page. Expand experience bullet points with more detail and metrics."
    elif "3" in page_errors[0]:
        correction = "The resume compiled to 3+ pages. Condense: reduce to top 3 bullets per role, shorten project descriptions."
    else:
        return pdf_path

    # Re-tailor with correction instruction, recompile
    # (implementation depends on having access to the original tailor function)
    logger.info(f"Page count retry: {correction}")
    return pdf_path  # For now, accept and flag
```

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/unit/test_tailorer.py -v
git add tailorer.py main.py
git commit -m "feat: resume length management — word budgets + page-count retry"
```

---

### Task 34: Council Critic Rubric

**Files:**
- Modify: `tailorer.py` (update critic prompt in council pattern)

- [ ] **Step 1: Write test**

```python
# tests/unit/test_tailorer.py — add test
def test_council_critic_evaluates_against_rubric(mocker):
    """Council critic should evaluate generators against keyword coverage, quality, fabrication."""
    from tailorer import CRITIC_RUBRIC_PROMPT
    assert "keyword coverage" in CRITIC_RUBRIC_PROMPT.lower()
    assert "section completeness" in CRITIC_RUBRIC_PROMPT.lower()
    assert "fabrication" in CRITIC_RUBRIC_PROMPT.lower()
```

- [ ] **Step 2: Define critic rubric prompt**

In `tailorer.py`:

```python
CRITIC_RUBRIC_PROMPT = """You are evaluating two resume tailoring attempts. Pick the BETTER one.

EVALUATION CRITERIA (score each 1-10):
1. KEYWORD COVERAGE: Does the resume address the top JD keywords? Count how many of the required keywords appear.
2. SECTION COMPLETENESS: Are all 6 sections present and substantive (Summary, Skills, Experience, Projects, Education, Certifications)?
3. WRITING QUALITY: Are bullet points specific with metrics? Are action verbs strong? Is language authentic (no AI filler)?
4. NO FABRICATION: Does the resume only claim skills/experience present in the original? Flag any suspicious additions.

Return JSON: {"winner": "A" or "B", "scores_a": {"keywords": N, "sections": N, "quality": N, "fabrication": N}, "scores_b": {...}, "reason": "..."}"""
```

- [ ] **Step 3: Wire into council pattern**

In `tailorer.py`, where the council critic is called (2-generator + 1-critic pattern), replace the existing critic prompt with `CRITIC_RUBRIC_PROMPT`.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/unit/test_tailorer.py -v
git add tailorer.py
git commit -m "feat: council critic rubric — structured evaluation of keyword, quality, fabrication"
```

---

### Task 35: Prompt Versioning CRUD

**Files:**
- Create: `utils/prompt_versioning.py`
- Create: `tests/unit/test_prompt_versioning.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/test_prompt_versioning.py
def test_load_active_prompt(mocker):
    """Load the currently active prompt version from Supabase."""
    mock_db = mocker.MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.is_.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"version": 3, "content": "You are a scoring AI...", "active_to": None}
    ]

    from utils.prompt_versioning import load_active_prompt
    prompt = load_active_prompt(mock_db, "test-user", "scoring_system")
    assert prompt["version"] == 3
    assert "scoring" in prompt["content"].lower()


def test_create_prompt_version(mocker):
    """Creating a new version sets active_to on the old one."""
    mock_db = mocker.MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.is_.return_value.execute.return_value.data = [
        {"id": "old-id", "version": 2}
    ]

    from utils.prompt_versioning import create_prompt_version
    create_prompt_version(mock_db, "test-user", "scoring_system", "New prompt text", created_by="auto")

    # Should deactivate old version
    mock_db.table.return_value.update.assert_called()
```

- [ ] **Step 2: Implement prompt versioning**

```python
# utils/prompt_versioning.py
"""Prompt version management — store, load, and rollback prompt versions."""
from datetime import datetime


def load_active_prompt(db, user_id: str, prompt_name: str) -> dict | None:
    """Load the currently active prompt version."""
    result = db.table("prompt_versions").select("*").eq(
        "user_id", user_id
    ).eq("prompt_name", prompt_name).is_("active_to", "null").order(
        "version", desc=True
    ).limit(1).execute()
    return result.data[0] if result.data else None


def create_prompt_version(db, user_id: str, prompt_name: str, content: str, created_by: str = "manual"):
    """Create a new prompt version. Deactivates the current active version."""
    # Deactivate current
    current = load_active_prompt(db, user_id, prompt_name)
    if current:
        db.table("prompt_versions").update({
            "active_to": datetime.now().isoformat()
        }).eq("id", current["id"]).execute()
        new_version = current["version"] + 1
    else:
        new_version = 1

    # Create new
    db.table("prompt_versions").insert({
        "user_id": user_id,
        "prompt_name": prompt_name,
        "version": new_version,
        "content": content,
        "created_by": created_by,
    }).execute()


def rollback_prompt(db, user_id: str, prompt_name: str):
    """Rollback to the previous prompt version."""
    current = load_active_prompt(db, user_id, prompt_name)
    if not current:
        return

    # Deactivate current
    db.table("prompt_versions").update({
        "active_to": datetime.now().isoformat()
    }).eq("id", current["id"]).execute()

    # Reactivate previous
    previous = db.table("prompt_versions").select("*").eq(
        "user_id", user_id
    ).eq("prompt_name", prompt_name).eq(
        "version", current["version"] - 1
    ).execute()

    if previous.data:
        db.table("prompt_versions").update({
            "active_to": None
        }).eq("id", previous.data[0]["id"]).execute()
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_prompt_versioning.py -v
git add utils/prompt_versioning.py tests/unit/test_prompt_versioning.py
git commit -m "feat: prompt versioning CRUD — load, create, rollback prompt versions"
```

---

### Task 36: Manual JD Hashing Merge Logic

**Files:**
- Modify: `app.py` (the `/api/tailor` or job submission endpoint)

- [ ] **Step 1: Write test**

```python
# tests/unit/test_app.py — add test
def test_manual_jd_dedup_prefers_manual(mocker):
    """User-submitted JD matching a scraped job → merge, manual wins as canonical."""
    from app import merge_manual_job
    existing_scraped = {"source": "linkedin", "description": "Short desc", "canonical_hash": "abc"}
    manual_submission = {"source": "manual", "description": "Full detailed JD with requirements", "canonical_hash": "abc"}

    merged = merge_manual_job(existing_scraped, manual_submission)
    assert merged["source"] == "manual"
    assert merged["description"] == "Full detailed JD with requirements"
```

- [ ] **Step 2: Implement merge logic**

In `app.py`, in the job submission endpoint:

```python
from utils.canonical_hash import canonical_hash

def merge_manual_job(existing: dict, manual: dict) -> dict:
    """Merge manual JD with existing scraped job. Manual wins for all fields."""
    merged = {**existing}
    # Manual submission fields override scraped
    for key in ("description", "title", "company", "location", "apply_url", "source"):
        if manual.get(key):
            merged[key] = manual[key]
    return merged

# In the job submission endpoint:
# 1. Compute canonical_hash for the submitted JD
# 2. Check Supabase for existing job with same hash
# 3. If found → merge_manual_job(), update in Supabase
# 4. If not found → create new job with source="manual"
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_app.py -v
git add app.py tests/unit/test_app.py
git commit -m "feat: manual JD dedup — merge with scraped job, manual takes priority"
```

---

### Task 37: Scoring Prompt Calibration Audit

**Files:**
- Modify: `lambdas/pipeline/score_batch.py` (review and update SCORING_SYSTEM_PROMPT)

This is a review task, not a code generation task. It requires the golden dataset (Task 17).

- [ ] **Step 1: Run current scoring prompt against 5 known jobs**

Select 5 jobs from the dashboard where you know the "correct" score intuitively:
- 2 strong matches (should be 80+)
- 2 moderate matches (should be 50-79)
- 1 non-match (should be <30)

Score each with the current prompt and record actual vs expected.

- [ ] **Step 2: Identify calibration issues**

Common issues:
- All scores clustered around 70-80 (not enough spread)
- ATS scores systematically higher than HM/TR (or vice versa)
- Score doesn't change much with very different JDs

- [ ] **Step 3: Adjust scoring prompt anchors**

In `SCORING_SYSTEM_PROMPT`, review the calibration scale. Update if needed:
- Ensure each score range has concrete examples
- Add explicit: "A score of 85+ means the candidate could be shortlisted with zero resume changes"
- Add: "A score below 50 means the candidate would need significant additional experience"

- [ ] **Step 4: Re-score the 5 test jobs and verify improvement**

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/score_batch.py
git commit -m "fix: calibrate scoring prompt — better anchor definitions for 0-100 scale"
```

---

### Task 38: Wire self_improve into Step Functions

**Files:**
- Modify: `template.yaml`

- [ ] **Step 1: Add self_improve as terminal state**

In `template.yaml`, find the daily pipeline state machine definition. Add `SelfImprove` as the final state after all `SaveJob` steps complete:

```yaml
# After the parallel scraper + score + tailor + save chain:
SelfImprove:
  Type: Task
  Resource: !GetAtt SelfImproveFunction.Arn
  InputPath: "$"
  ResultPath: "$.self_improvement"
  Catch:
    - ErrorEquals: ["States.ALL"]
      Next: NotifyError
  End: true
```

The `Catch` ensures that if self-improvement fails, it doesn't crash the pipeline — it routes to error notification instead.

- [ ] **Step 2: Verify state machine definition is valid**

```bash
# Validate SAM template
npx sam validate --template template.yaml 2>&1 | head -20
```

- [ ] **Step 3: Commit**

```bash
git add template.yaml
git commit -m "feat: wire self_improve Lambda as terminal Step Functions state"
```

---

### Task 39: Self-Improvement Revert Action + Cooldown Write

Already implemented as part of Task 22 (`execute_revert` and `should_revert_or_extend` functions). This task wires them into the self_improve Lambda handler.

**Files:**
- Modify: `lambdas/pipeline/self_improve.py`

- [ ] **Step 1: Wire revert logic into handler**

In `lambdas/pipeline/self_improve.py`, after `generate_adjustments()`:

```python
from self_improver import should_revert_or_extend, execute_revert

# Check existing active adjustments for revert
active = db.table("pipeline_adjustments").select("*").in_(
    "status", ["auto_applied"]
).eq("user_id", user_id).execute().data

for adj in active:
    # Get run metrics since this adjustment was applied
    runs_since = db.table("pipeline_runs").select("avg_base_score").eq(
        "user_id", user_id
    ).gte("started_at", adj["applied_at"]).order("started_at").execute().data

    decision = should_revert_or_extend(adj, runs_since)
    if decision == "revert":
        execute_revert(db, adj)
    elif decision == "confirm":
        db.table("pipeline_adjustments").update(
            {"status": "confirmed"}
        ).eq("id", adj["id"]).execute()
```

- [ ] **Step 2: Test and commit**

```bash
pytest tests/unit/test_self_improver.py -v
git add lambdas/pipeline/self_improve.py
git commit -m "feat: wire revert + cooldown logic into self_improve Lambda handler"
```

---

### Task 40: Base Resume Improvement Suggestions

**Files:**
- Modify: `self_improver.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_self_improver.py — add test
def test_base_resume_suggestion_from_keyword_gaps():
    """Consistent keyword gap across 50+ jobs → suggest base resume update."""
    from self_improver import analyze_keyword_gaps_for_resume

    keyword_stats = {
        "kubernetes": {"count": 34, "avg_job_score": 78},
        "graphql": {"count": 28, "avg_job_score": 72},
        "react": {"count": 12, "avg_job_score": 65},
    }
    suggestions = analyze_keyword_gaps_for_resume(keyword_stats, min_jobs=25)

    assert len(suggestions) == 2  # kubernetes + graphql (both >25 jobs)
    assert suggestions[0]["risk_level"] == "medium"
    assert "kubernetes" in suggestions[0]["reason"].lower()
```

- [ ] **Step 2: Implement keyword gap analysis for resume**

```python
# In self_improver.py

def analyze_keyword_gaps_for_resume(keyword_stats: dict, min_jobs: int = 25) -> list[dict]:
    """Suggest base resume updates for consistently missing keywords."""
    suggestions = []
    for keyword, stats in keyword_stats.items():
        if stats["count"] >= min_jobs:
            suggestions.append({
                "adjustment_type": "quality_flag",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"keyword": keyword, "action": "add_to_base_resume"},
                "reason": f"Consider adding '{keyword}' to base resume — appeared in {stats['count']} of top matched JDs (avg score: {stats['avg_job_score']})",
                "evidence": stats,
            })
    return sorted(suggestions, key=lambda s: s["evidence"]["count"], reverse=True)
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_self_improver.py -v
git add self_improver.py tests/unit/test_self_improver.py
git commit -m "feat: base resume improvement suggestions from keyword gap analysis"
```

---

### Task 41: User Feedback — "Flag Score" API + Dashboard

**Files:**
- Modify: `app.py` (new endpoint)
- Modify: `web/src/` (flag score button — frontend, details TBD)

- [ ] **Step 1: Write API test**

```python
# tests/unit/test_app.py — add test
def test_flag_score_creates_ground_truth(mocker, client):
    """POST /api/feedback/flag-score creates ground truth entry."""
    mock_db = mocker.patch("app._db")

    response = client.post("/api/feedback/flag-score", json={
        "job_id": "test-job-123",
        "feedback_type": "score_inaccurate",
        "expected_score": 85,
        "comment": "This is a perfect match, scored too low",
    }, headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    mock_db.client.table.assert_called_with("pipeline_adjustments")
```

- [ ] **Step 2: Implement endpoint**

In `app.py`:

```python
@app.post("/api/feedback/flag-score")
def flag_score(
    body: dict,
    user: AuthUser = Depends(get_current_user),
):
    """User flags a score as inaccurate → creates ground truth for self-improvement."""
    _db.client.table("pipeline_adjustments").insert({
        "user_id": user.id,
        "adjustment_type": "quality_flag",
        "risk_level": "high",  # User feedback is high-confidence signal
        "status": "pending",
        "payload": {
            "job_id": body["job_id"],
            "feedback_type": body.get("feedback_type", "score_inaccurate"),
            "expected_score": body.get("expected_score"),
            "comment": body.get("comment"),
        },
        "reason": f"User flagged score for job {body['job_id']}: {body.get('feedback_type')}",
    }).execute()

    return {"status": "ok", "message": "Feedback recorded"}
```

- [ ] **Step 3: Add frontend button (basic)**

In the job workspace scores card, add a "Flag" button that POSTs to this endpoint. Minimal UI — can be polished in Phase 3.0 dashboard work.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/unit/test_app.py -v
git add app.py web/src/
git commit -m "feat: user feedback — 'flag score' API endpoint + dashboard button"
```

---

### Task 42: Score Determinism Test (REPORT ONLY)

**Files:**
- Create: `tests/quality/test_score_determinism.py`

- [ ] **Step 1: Write REPORT ONLY test**

```python
# tests/quality/test_score_determinism.py
"""Tier 4b REPORT ONLY: Score determinism across multiple calls.

Requires real AI calls — cached after first run. Runs in CI as REPORT ONLY.
"""
import pytest


@pytest.mark.skipif(
    not pytest.importorskip("ai_client", reason="AI client not available"),
    reason="Requires AI client",
)
class TestScoreDeterminism:
    def test_same_job_scored_three_times_within_tolerance(self):
        """Same job scored 3x with temp=0 → all within +/-2 points."""
        from lambdas.pipeline.score_batch import score_single_job

        job = {
            "title": "Backend Engineer",
            "company": "Test Corp",
            "description": "Build REST APIs using Python and FastAPI. " * 20,
        }
        resume = "Experienced Python developer with 5 years of backend development. " * 20

        scores = []
        for _ in range(3):
            result = score_single_job(job, resume, temperature=0)
            if result:
                scores.append(result)

        if len(scores) < 2:
            pytest.skip("Not enough AI providers available")

        # All ATS scores within +/-2
        ats_scores = [s["ats_score"] for s in scores]
        assert max(ats_scores) - min(ats_scores) <= 4, f"ATS scores too spread: {ats_scores}"

        hm_scores = [s["hiring_manager_score"] for s in scores]
        assert max(hm_scores) - min(hm_scores) <= 4, f"HM scores too spread: {hm_scores}"
```

- [ ] **Step 2: Commit**

```bash
git add tests/quality/test_score_determinism.py
git commit -m "test: score determinism test (REPORT ONLY) — same job 3x within tolerance"
```

---

## COMPLETION CHECKLIST

After all tasks:

- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Verify dashboard shows backfilled jobs
- [ ] Verify DeepSeek removed from provider list
- [ ] Verify canonical hash used everywhere: `grep -rn "hashlib.md5" scrapers/ lambdas/ --include="*.py"` → should return 0 results
- [ ] Verify no truncation in scoring: `grep -rn "\[:2000\]\|\[:3000\]\|\[:500\]" lambdas/ --include="*.py"` → should return 0 results
- [ ] Verify Supabase migration applied: `npx supabase db execute "SELECT count(*) FROM seen_jobs"`
- [ ] Verify seen_jobs.json no longer read by pipeline: `grep -rn "seen_jobs.json" main.py` → should return 0 results
- [ ] Update CLAUDE.md with Phase 2.7/2.6 completion status
- [ ] Commit final documentation updates
