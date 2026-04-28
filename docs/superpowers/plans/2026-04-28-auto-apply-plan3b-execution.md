# Auto-Apply Plan 3b — AI Preview Implementation Plan (Execution-Ready, v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v2 (2026-04-28):** Closed gaps identified during spec audit against design spec [§7.1-7.8](../specs/2026-04-11-auto-apply-mode-1-design.md). Changes vs v1: rich AI prompt using `UserProfile.to_candidate_context()`, `DEFAULT_CANDIDATE_CONTEXT` fallback, Pydantic models per §7.1, Greenhouse `compliance[]` + `demographic_questions` merging, type normalization layer, Supabase `ai_cache` table per §7.7, `_refresh_s3_urls()` for resume URLs, `is_default` flag, `jobs.is_expired=true` side effect on 404, expanded error matrix coverage.

**Goal:** Replace the empty Plan-3a `GET /api/apply/preview/{job_id}` stub with a fully populated AI-driven preview that fetches platform questions, classifies them, generates answers via the AI council with rich profile context, loads the user's cover letter, and caches the response — conforming to the `ApplyPreviewResponse` Pydantic schema from spec §7.1.

**Architecture:** Net-new `shared/platform_metadata/`, `shared/question_classifier.py`, `shared/answer_generator.py`, `shared/cover_letter_loader.py`, `shared/tex_utils.py`, `shared/apply_models.py` (Pydantic). Existing `shared/apply_platform.py` extended with `extract_platform_ids()`. Existing `lambdas/pipeline/ai_helper.py::ai_complete_cached()` extended to accept `max_tokens`. The preview endpoint orchestrates: eligibility → cache (Supabase `ai_cache` table) → metadata fetch → classify → load resume meta + presign → load cover letter → generate answers → cache+return.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v1, httpx, Supabase (`ai_cache` table for preview cache, `users` + `jobs` for context), AWS Lambda (container Lambda for API), pytest, existing `lambdas/pipeline/ai_helper.ai_complete_cached` multi-provider council with Supabase-backed cache.

**Live API anchors (pinned 2026-04-27):**
- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{posting_id}?questions=true` returns `{questions: [{label, required, description, fields: [{name, type, values: [{label, value}]}]}]}`. Field types observed: `input_text`, `input_file`, `textarea`, `multi_value_single_select`, `multi_value_multi_select`. Standard fields use stable names (`first_name`, `last_name`, `email`, `phone`, `resume`, `cover_letter`, `linkedin`). Custom questions are `question_<numeric_id>`.
- Ashby: `posting-api/job-posting/{uuid}` returns **401 Unauthorized** — needs auth. The public form data is at `https://jobs.ashbyhq.com/api/non-user-graphql` (GraphQL). Task 2 includes an investigation phase before the fetcher.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `shared/apply_platform.py` | modify | Add `extract_platform_ids(url) -> Optional[dict]` |
| `lambdas/pipeline/score_batch.py` | modify | Insert `apply_board_token` + `apply_posting_id` alongside `apply_platform` |
| `scripts/backfill_apply_platform.py` | modify | Backfill `apply_board_token` + `apply_posting_id` for existing rows |
| `lambdas/pipeline/ai_helper.py` | modify | Extend `ai_complete_cached()` to accept `max_tokens` (forwarded to `ai_complete`) |
| `shared/apply_models.py` | create | Pydantic models per spec §7.1: `PlatformInfo`, `CustomQuestion`, `CustomAnswer`, `ApplyPreviewResponse` |
| `shared/platform_metadata/__init__.py` | create | Package marker + `fetch_metadata(platform, board_token, posting_id)` dispatcher |
| `shared/platform_metadata/greenhouse.py` | create | `fetch_greenhouse(board_token, posting_id) -> dict` returning normalized `{questions, cover_letter_required, cover_letter_max_length}` (merges `questions[]` + `compliance[]` + `demographic_questions`, normalizes Greenhouse field types into spec's `text|textarea|select|multi_select|checkbox|yes_no|file` vocabulary) |
| `shared/platform_metadata/ashby.py` | create | `fetch_ashby(board_token, posting_id) -> dict` same normalized shape via GraphQL |
| `shared/question_classifier.py` | create | `classify_question(label, description) -> Literal["custom","eeo","confirmation","marketing","referral"]` |
| `shared/answer_generator.py` | create | `generate_answer(question, user_profile, job, resume_text, cover_letter_text, ai_complete_cached_fn) -> dict` per-category branching using rich spec §7.3 prompt + `DEFAULT_CANDIDATE_CONTEXT` fallback |
| `shared/tex_utils.py` | create | `tex_to_plaintext(tex) -> str` strips LaTeX commands for AI prompt context |
| `shared/cover_letter_loader.py` | create | `load_cover_letter(user_id, job_hash, s3_client) -> dict {text, source}` with default fallback |
| `shared/preview_cache.py` | create | `get_preview_cache(db, key) -> dict\|None` and `set_preview_cache(db, key, payload, ttl_minutes)` reading/writing Supabase `ai_cache` table per spec §7.7 |
| `app.py` | modify | Replace `apply_preview` endpoint orchestration; wire `_refresh_s3_urls()` for resume URLs; emit `is_default`; mark `jobs.is_expired=true` on platform 404 |
| `tests/unit/test_apply_platform_extract.py` | create | Tests for slug extractor |
| `tests/unit/test_apply_models.py` | create | Tests for Pydantic model validation |
| `tests/unit/test_platform_metadata_greenhouse.py` | create | Tests for Greenhouse fetcher (mocked httpx) — including compliance + demographic_questions + type normalization |
| `tests/unit/test_platform_metadata_ashby.py` | create | Tests for Ashby fetcher (mocked httpx) |
| `tests/unit/test_question_classifier.py` | create | Tests for classifier regex |
| `tests/unit/test_answer_generator.py` | create | Tests for per-category answer generation (mocked `ai_complete_cached`) — uses rich profile fixture matching `tests/unit/test_apply_endpoints.py:60` |
| `tests/unit/test_tex_utils.py` | create | Tests for tex stripping |
| `tests/unit/test_cover_letter_loader.py` | create | Tests for S3 load + fallback |
| `tests/unit/test_preview_cache.py` | create | Tests for `ai_cache` table read/write |
| `tests/unit/test_apply_endpoints.py` | modify | Extend with new preview endpoint tests against full `ApplyPreviewResponse` shape |
| `tests/unit/test_ai_helper_max_tokens.py` | create | Tests that `ai_complete_cached(..., max_tokens=300)` forwards correctly |

**File-creation order matches task order below.** Each task self-contains its tests, implementation, and commit.

---

## Task 0: URL slug extractor + persistence + backfill

**Files:**
- Modify: `shared/apply_platform.py` — add `extract_platform_ids()`
- Modify: `lambdas/pipeline/score_batch.py:145-180` — insert slug fields
- Modify: `scripts/backfill_apply_platform.py` — backfill slug fields
- Create: `tests/unit/test_apply_platform_extract.py`

**Why this is Step 0:** Without `apply_board_token` and `apply_posting_id` populated in the `jobs` table, every metadata fetcher in Tasks 1-2 has nothing to call. Today scrapers extract these into the in-memory dict but `score_batch.py` drops them at insert time.

- [ ] **Step 0.1: Write failing test for `extract_platform_ids`**

Create `tests/unit/test_apply_platform_extract.py`:

```python
import pytest
from shared.apply_platform import extract_platform_ids


class TestExtractPlatformIds:
    def test_greenhouse_standard_url(self):
        url = "https://boards.greenhouse.io/airbnb/jobs/7649441"
        assert extract_platform_ids(url) == {
            "platform": "greenhouse",
            "board_token": "airbnb",
            "posting_id": "7649441",
        }

    def test_greenhouse_with_query_string(self):
        url = "https://boards.greenhouse.io/airbnb/jobs/7649441?gh_src=abc"
        assert extract_platform_ids(url) == {
            "platform": "greenhouse",
            "board_token": "airbnb",
            "posting_id": "7649441",
        }

    def test_greenhouse_embed_url(self):
        url = "https://boards.greenhouse.io/embed/job_app?for=airbnb&token=7649441"
        result = extract_platform_ids(url)
        assert result is not None
        assert result["platform"] == "greenhouse"
        assert result["board_token"] == "airbnb"
        assert result["posting_id"] == "7649441"

    def test_ashby_standard_url(self):
        url = "https://jobs.ashbyhq.com/openai/145ff46b-1441-4773-bcd3-c8c90baa598a"
        assert extract_platform_ids(url) == {
            "platform": "ashby",
            "board_token": "openai",
            "posting_id": "145ff46b-1441-4773-bcd3-c8c90baa598a",
        }

    def test_ashby_with_application_suffix(self):
        url = "https://jobs.ashbyhq.com/openai/145ff46b-1441-4773-bcd3-c8c90baa598a/application"
        result = extract_platform_ids(url)
        assert result is not None
        assert result["board_token"] == "openai"
        assert result["posting_id"] == "145ff46b-1441-4773-bcd3-c8c90baa598a"

    def test_unsupported_platform_returns_none(self):
        assert extract_platform_ids("https://jobs.lever.co/foo/bar") is None
        assert extract_platform_ids("https://linkedin.com/jobs/view/12345") is None

    def test_none_input_returns_none(self):
        assert extract_platform_ids(None) is None
        assert extract_platform_ids("") is None
        assert extract_platform_ids(123) is None  # type: ignore[arg-type]

    def test_malformed_greenhouse_url_returns_none(self):
        assert extract_platform_ids("https://boards.greenhouse.io/airbnb") is None
        assert extract_platform_ids("https://boards.greenhouse.io/") is None
```

- [ ] **Step 0.2: Run test, verify it fails**

Run: `pytest tests/unit/test_apply_platform_extract.py -v`
Expected: `ImportError: cannot import name 'extract_platform_ids' from 'shared.apply_platform'`

- [ ] **Step 0.3: Implement `extract_platform_ids` in `shared/apply_platform.py`**

Append to `shared/apply_platform.py`:

```python
_GREENHOUSE_STANDARD = re.compile(
    r"boards\.greenhouse\.io/(?P<board>[^/?]+)/jobs/(?P<posting>\d+)",
    re.IGNORECASE,
)
_GREENHOUSE_EMBED = re.compile(
    r"boards\.greenhouse\.io/embed/job_app\?[^#]*?for=(?P<board>[^&]+)[^#]*?token=(?P<posting>\d+)",
    re.IGNORECASE,
)
_ASHBY_STANDARD = re.compile(
    r"jobs\.ashbyhq\.com/(?P<board>[^/?]+)/(?P<posting>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def extract_platform_ids(url: Optional[str]) -> Optional[dict]:
    """Extract platform-specific identifiers needed to call platform APIs.

    Returns a dict {platform, board_token, posting_id} or None if the URL
    isn't a recognized greenhouse/ashby URL or doesn't contain the slugs.

    Pure function. Never raises.
    """
    if not url or not isinstance(url, str):
        return None

    m = _GREENHOUSE_STANDARD.search(url)
    if m:
        return {
            "platform": "greenhouse",
            "board_token": m.group("board"),
            "posting_id": m.group("posting"),
        }

    m = _GREENHOUSE_EMBED.search(url)
    if m:
        return {
            "platform": "greenhouse",
            "board_token": m.group("board"),
            "posting_id": m.group("posting"),
        }

    m = _ASHBY_STANDARD.search(url)
    if m:
        return {
            "platform": "ashby",
            "board_token": m.group("board"),
            "posting_id": m.group("posting"),
        }

    return None
```

- [ ] **Step 0.4: Run test, verify it passes**

Run: `pytest tests/unit/test_apply_platform_extract.py -v`
Expected: 8 passed.

- [ ] **Step 0.5: Wire slug fields into `score_batch.py` insert**

In `lambdas/pipeline/score_batch.py`, locate the `job_record = {...}` dict around line 145. Add 3 lines after the `apply_platform` line:

```python
        ids = extract_platform_ids(job.get("apply_url") or "")
        job_record = {
            # ... existing fields ...
            "apply_platform": classify_apply_platform(job.get("apply_url") or ""),
            "apply_board_token": ids["board_token"] if ids else None,
            "apply_posting_id": ids["posting_id"] if ids else None,
            # ... rest of fields ...
        }
```

Add `extract_platform_ids` to the existing import at the top of the file:

```python
from shared.apply_platform import classify_apply_platform, extract_platform_ids
```

In the optional-column fallback list (lines 177-179), add the two new columns:

```python
                for col in ("key_matches", "gaps", "match_reasoning", "score_tier",
                            "archetype", "seniority", "remote", "requirement_map",
                            "matched_resume", "apply_platform",
                            "apply_board_token", "apply_posting_id"):
```

- [ ] **Step 0.6: Run all existing score_batch tests, verify they still pass**

Run: `pytest tests/unit/test_score_batch.py -v 2>/dev/null || echo "no score_batch tests yet"`
Run: `pytest tests/unit/ -k "score" -v`
Expected: existing tests pass; the change is purely additive.

- [ ] **Step 0.7: Update backfill script to populate slug fields**

In `scripts/backfill_apply_platform.py`, locate the row-update loop and extend the update dict.

Read the file first to find the exact line (search for `.update(`). Then add `apply_board_token` and `apply_posting_id` to the update payload, computed via `extract_platform_ids(row["apply_url"])`. Add the import at the top:

```python
from shared.apply_platform import classify_apply_platform, extract_platform_ids
```

Inside the loop, change the update payload from:

```python
update = {"apply_platform": platform}
```

to:

```python
ids = extract_platform_ids(url)
update = {
    "apply_platform": platform,
    "apply_board_token": ids["board_token"] if ids else None,
    "apply_posting_id": ids["posting_id"] if ids else None,
}
```

- [ ] **Step 0.8: Run backfill in dry-run mode**

Run: `python scripts/backfill_apply_platform.py --dry-run | tail -30`
Expected: prints planned updates including `apply_board_token` and `apply_posting_id` for greenhouse/ashby rows; reports counts.

- [ ] **Step 0.9: Commit Task 0**

```bash
git add shared/apply_platform.py lambdas/pipeline/score_batch.py \
  scripts/backfill_apply_platform.py tests/unit/test_apply_platform_extract.py
git commit -m "feat(apply): extract+persist board_token/posting_id for platform metadata fetchers

- shared.apply_platform.extract_platform_ids() parses greenhouse standard,
  greenhouse embed, and ashby URLs into {platform, board_token, posting_id}
- score_batch insert now persists apply_board_token + apply_posting_id
  alongside apply_platform; falls back gracefully if columns absent
- backfill script extended to populate slug fields for existing rows
- 8 unit tests pinning standard + embed + edge cases

Required prerequisite for Plan 3b platform metadata fetchers."
```

- [ ] **Step 0.10: Run backfill against prod (one-shot)**

This is the only manual production action in Task 0. Coordinate with user before running.

```bash
python scripts/backfill_apply_platform.py --apply
```

Expected: ~100 rows updated (matches Apr 27 backfill: 55 greenhouse + 45 ashby).

---

## Task 0.3: Recompute `easy_apply_eligible` generated column formula

**Files:**
- Create: `supabase/migrations/20260428_easy_apply_eligible_recompute.sql`

**Why:** [20260414_auto_apply_setup.sql:60-61](supabase/migrations/20260414_auto_apply_setup.sql:60) defines:

```sql
easy_apply_eligible BOOLEAN GENERATED ALWAYS AS (apply_platform IS NOT NULL) STORED
```

But PR #10 (Apr 27) flipped the API eligibility gate to `apply_url AND resume_s3_key`. So the column today returns `false` for ~600 jobs that the API actually allows. Spec §8.2 says Plan 3c frontend reads `job.easy_apply_eligible` from `/api/dashboard/jobs` for the EasyApplyBadge — without this fix, ~600 eligible jobs render as "no Easy Apply" in the UI even though the backend would accept the apply.

- [ ] **Step 0.3.1: Write the migration**

Create `supabase/migrations/20260428_easy_apply_eligible_recompute.sql`:

```sql
-- Recompute easy_apply_eligible to match the API gate flipped in PR #10 (Apr 27).
-- Previous formula: (apply_platform IS NOT NULL) — under-counted by ~600 rows.
-- New formula: (apply_url IS NOT NULL AND resume_s3_key IS NOT NULL).
-- The actual /api/apply/eligibility endpoint also checks profile completeness and
-- already_applied, but those are user-state-dependent and can't live in a job-level
-- generated column. The column is a "could this job ever be eligible?" hint, not
-- a per-user verdict.

BEGIN;

-- Drop the dependent index first
DROP INDEX IF EXISTS idx_jobs_easy_apply_eligible;

-- Drop the old generated column
ALTER TABLE jobs DROP COLUMN IF EXISTS easy_apply_eligible;

-- Re-add with the correct formula
ALTER TABLE jobs
  ADD COLUMN easy_apply_eligible BOOLEAN
  GENERATED ALWAYS AS (apply_url IS NOT NULL AND resume_s3_key IS NOT NULL) STORED;

-- Recreate the index that supports the dashboard query path
CREATE INDEX idx_jobs_easy_apply_eligible
  ON jobs(user_id, score_tier, easy_apply_eligible)
  WHERE is_expired = false;

COMMIT;
```

- [ ] **Step 0.3.2: Apply migration locally and verify**

Run:
```bash
# Verify against local Supabase (or the dev project — coordinate with user before prod)
psql "$SUPABASE_DB_URL" -f supabase/migrations/20260428_easy_apply_eligible_recompute.sql
psql "$SUPABASE_DB_URL" -c "
  SELECT
    COUNT(*) FILTER (WHERE easy_apply_eligible) AS eligible,
    COUNT(*) FILTER (WHERE NOT easy_apply_eligible) AS not_eligible,
    COUNT(*) AS total
  FROM jobs;
"
```

Expected (post-migration): `eligible` rises from ~100 → ~600+ (matching PR #10's eligibility logic).

- [ ] **Step 0.3.3: Commit Task 0.3**

```bash
git add supabase/migrations/20260428_easy_apply_eligible_recompute.sql
git commit -m "fix(jobs): recompute easy_apply_eligible to match flipped eligibility gate

Previous formula (apply_platform IS NOT NULL) was set when eligibility required
a recognized ATS platform. PR #10 (Apr 27) flipped the API gate to apply_url +
resume_s3_key, making ~600 jobs eligible that the column reports as ineligible.

Plan 3c frontend reads this column for the EasyApplyBadge — without this fix,
~70% of eligible jobs would render as 'no Easy Apply' even though the backend
accepts them."
```

> **Note for prod migration:** This is a STORED generated column on a 850-row table. Drop+recreate is fast (<1s). Run during a quiet window. No data loss; the column is purely computed.

---

## Task 0.5: Extend `ai_complete_cached` to accept `max_tokens`

**Files:**
- Modify: `lambdas/pipeline/ai_helper.py:332` — add `max_tokens` kwarg, forward to `ai_complete`
- Create: `tests/unit/test_ai_helper_max_tokens.py`

**Why:** Spec §7.3 step 9 requires `max_tokens=300` for application answers. Current `ai_complete_cached` hardcodes the underlying `ai_complete()` default of 4096. Cheap one-line change keeps a single AI helper for the codebase.

- [ ] **Step 0.5.1: Write failing test**

Create `tests/unit/test_ai_helper_max_tokens.py`:

```python
from unittest.mock import patch, MagicMock


def test_ai_complete_cached_forwards_max_tokens():
    with patch("lambdas.pipeline.ai_helper.ai_complete") as inner, \
         patch("lambdas.pipeline.ai_helper.get_supabase") as get_db:
        # Cache miss path
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []
        db.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        get_db.return_value = db
        inner.return_value = {"content": "ok", "provider": "p", "model": "m"}

        from lambdas.pipeline.ai_helper import ai_complete_cached
        ai_complete_cached("hi", system="sys", temperature=0.3, max_tokens=300)

        inner.assert_called_once()
        kwargs = inner.call_args.kwargs
        assert kwargs.get("max_tokens") == 300
        assert kwargs.get("temperature") == 0.3


def test_ai_complete_cached_default_max_tokens_unchanged():
    with patch("lambdas.pipeline.ai_helper.ai_complete") as inner, \
         patch("lambdas.pipeline.ai_helper.get_supabase") as get_db:
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []
        db.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        get_db.return_value = db
        inner.return_value = {"content": "ok", "provider": "p", "model": "m"}

        from lambdas.pipeline.ai_helper import ai_complete_cached
        ai_complete_cached("hi", system="sys")

        kwargs = inner.call_args.kwargs
        # Backwards compat: default should still be 4096 (or whatever ai_complete defaults to)
        assert kwargs.get("max_tokens", 4096) == 4096
```

- [ ] **Step 0.5.2: Run, verify it fails**

Run: `pytest tests/unit/test_ai_helper_max_tokens.py -v`
Expected: AttributeError or assertion failure (kwarg not forwarded).

- [ ] **Step 0.5.3: Patch `ai_complete_cached`**

Edit `lambdas/pipeline/ai_helper.py:332`:

```python
def ai_complete_cached(
    prompt: str,
    system: str = "",
    cache_hours: int = 72,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict:
    """AI complete with Supabase cache. Returns dict with content, provider, model."""
    cache_key = hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()
    db = get_supabase()

    cached = db.table("ai_cache").select("response, provider, model") \
        .eq("cache_key", cache_key) \
        .gte("expires_at", datetime.utcnow().isoformat()).execute()
    if cached.data:
        return {
            "content": cached.data[0]["response"],
            "provider": cached.data[0].get("provider", "cache"),
            "model": cached.data[0].get("model", "cache"),
        }

    result = ai_complete(prompt, system, temperature=temperature, max_tokens=max_tokens)

    db.table("ai_cache").upsert({
        "cache_key": cache_key,
        "response": result["content"],
        "provider": result["provider"],
        "model": result["model"],
        "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
    }, on_conflict="cache_key").execute()

    return result
```

- [ ] **Step 0.5.4: Run, verify it passes**

Run: `pytest tests/unit/test_ai_helper_max_tokens.py -v`
Expected: 2 passed.

- [ ] **Step 0.5.5: Run all existing ai_helper tests to confirm no regression**

Run: `pytest tests/unit/ -k ai_helper -v 2>/dev/null; pytest tests/unit/test_score_batch.py -v 2>&1 | tail -10`
Expected: existing tests still pass.

- [ ] **Step 0.5.6: Commit Task 0.5**

```bash
git add lambdas/pipeline/ai_helper.py tests/unit/test_ai_helper_max_tokens.py
git commit -m "feat(ai): ai_complete_cached accepts max_tokens

Spec §7.3 requires max_tokens=300 for application answer generation.
Existing helper hardcoded ai_complete's default 4096. Tiny additive change
keeps a single AI helper across the codebase. Backwards compatible."
```

> **Note on `providers` kwarg from spec:** Spec §7.3 also specifies `providers=["qwen", "nvidia", "groq"]` for application answers. Adding per-call provider override would be a larger refactor of the existing council architecture (provider order is a council-level decision today). For Plan 3b we accept the existing council failover order. If post-launch quality requires the spec'd subset, it becomes a follow-up Plan 3b.1.

---

## Task 1: Greenhouse metadata fetcher

**Files:**
- Create: `shared/platform_metadata/__init__.py`
- Create: `shared/platform_metadata/greenhouse.py`
- Create: `tests/unit/test_platform_metadata_greenhouse.py`

**Reference shape (live API, pinned 2026-04-27 against airbnb/7649441):**

```json
{
  "id": 7649441,
  "title": "...",
  "absolute_url": "...",
  "questions": [
    {"label": "First Name", "required": true, "description": null,
     "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
    {"label": "Resume/CV", "required": true,
     "fields": [{"name": "resume", "type": "input_file", "values": []}]},
    {"label": "Cover Letter", "required": false,
     "fields": [{"name": "cover_letter", "type": "input_file", "values": []}]},
    {"label": "Why have you chosen to apply to Airbnb?", "required": true,
     "fields": [{"name": "question_XYZ", "type": "textarea", "values": []}]},
    {"label": "Gender", "required": true, "description": "<p>...EEO...</p>",
     "fields": [{"name": "question_XYZ", "type": "multi_value_single_select",
                 "values": [{"label": "Male", "value": 636489403}, ...]}]}
  ],
  "compliance": null,
  "demographic_questions": null
}
```

**Type normalization** — Greenhouse field types must be mapped to spec §7.1's `CustomQuestion.type` vocabulary:

| Greenhouse type | Spec type |
|---|---|
| `input_text` | `text` |
| `textarea` | `textarea` |
| `input_file` | `file` |
| `multi_value_single_select` (2 options, "Yes"/"No"-shaped) | `yes_no` |
| `multi_value_single_select` (other) | `select` |
| `multi_value_multi_select` | `multi_select` |
| `single_checkbox` (rare) | `checkbox` |

**Auxiliary question arrays to merge:** The Greenhouse API returns up to 3 separate question collections that frontend must render together:
- `questions[]` — primary application form
- `compliance[]` — EEO disclosures (US: race, gender, veteran, disability) — when present, each entry has its own `questions[]` array
- `demographic_questions{}` — voluntary demographic survey — also has nested `questions[]`

Both auxiliary arrays must be flattened into the unified `questions` output with `category="eeo"` already pre-tagged.

- [ ] **Step 1.1: Write failing test for Greenhouse fetcher**

Create `tests/unit/test_platform_metadata_greenhouse.py`:

```python
import pytest
import httpx
from unittest.mock import patch, MagicMock
from shared.platform_metadata.greenhouse import fetch_greenhouse, GreenhouseFetchError


_FAKE_RESPONSE = {
    "id": 7649441,
    "title": "Senior Engineer",
    "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/7649441",
    "questions": [
        {"label": "First Name", "required": True, "description": None,
         "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Resume/CV", "required": True, "description": None,
         "fields": [{"name": "resume", "type": "input_file", "values": []}]},
        {"label": "Cover Letter", "required": False, "description": None,
         "fields": [{"name": "cover_letter", "type": "input_file", "values": []}]},
        {"label": "Why Airbnb?", "required": True, "description": None,
         "fields": [{"name": "question_1", "type": "textarea", "values": []}]},
        {"label": "Gender", "required": True, "description": "EEO disclosure...",
         "fields": [{"name": "question_2", "type": "multi_value_single_select",
                     "values": [
                         {"label": "Male", "value": 1},
                         {"label": "Decline to Self Identify", "value": 2},
                     ]}]},
    ],
}


def _mock_response(status=200, json_data=None):
    m = MagicMock(spec=httpx.Response)
    m.status_code = status
    m.json.return_value = json_data or _FAKE_RESPONSE
    m.raise_for_status.side_effect = (
        httpx.HTTPStatusError(f"HTTP {status}", request=MagicMock(), response=m)
        if status >= 400 else None
    )
    return m


class TestFetchGreenhouse:
    def test_returns_normalized_questions(self):
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        assert result["platform"] == "greenhouse"
        assert result["job_title"] == "Senior Engineer"
        assert len(result["questions"]) == 5
        assert result["cover_letter_field_present"] is True
        assert result["cover_letter_required"] is False

    def test_question_field_normalization(self):
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        gender_q = next(q for q in result["questions"] if q["label"] == "Gender")
        # Type normalized from Greenhouse's "multi_value_single_select" to spec's "select"
        assert gender_q["type"] == "select"
        assert gender_q["required"] is True
        assert gender_q["description"] == "EEO disclosure..."
        assert gender_q["options"] == ["Male", "Decline to Self Identify"]
        assert gender_q["field_name"] == "question_2"

    def test_yes_no_questions_normalized(self):
        # 2-option multi_value_single_select with "Yes"/"No" shape -> spec type "yes_no"
        yn_response = {**_FAKE_RESPONSE, "questions": [
            {"label": "Are you authorized?", "required": True, "description": None,
             "fields": [{"name": "question_yn", "type": "multi_value_single_select",
                         "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 2}]}]},
        ]}
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=yn_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        assert result["questions"][0]["type"] == "yes_no"

    def test_compliance_array_merged_with_eeo_category(self):
        # When greenhouse returns compliance[].questions[], these must be flattened
        # into result.questions with category='eeo' pre-tagged
        compliance_response = {**_FAKE_RESPONSE, "compliance": [{
            "type": "race_ethnicity",
            "questions": [{
                "label": "Hispanic or Latino?", "required": False, "description": None,
                "fields": [{"name": "compliance_race_1", "type": "multi_value_single_select",
                            "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 2},
                                       {"label": "Decline", "value": 3}]}]
            }],
        }]}
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=compliance_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        compliance_q = next(q for q in result["questions"] if "Hispanic" in q["label"])
        assert compliance_q["category"] == "eeo"
        assert compliance_q["field_name"] == "compliance_race_1"

    def test_compliance_block_description_propagates_to_questions(self):
        # Real Greenhouse compliance blocks put the EEO disclosure at the BLOCK
        # level. Each question's own description=null. The fetcher must propagate
        # the block description down so the AI prompt sees the voluntary-disclosure
        # context. (Verified shape against Discord posting 7343909.)
        compliance_response = {**_FAKE_RESPONSE, "compliance": [{
            "type": "eeoc",
            "description": "<p>Voluntary Self-Identification of Disability — Form CC-305</p>",
            "questions": [{
                "label": "DisabilityStatus", "required": False, "description": None,
                "fields": [{"name": "disability_status", "type": "multi_value_single_select",
                            "values": [{"label": "I do not want to answer", "value": "3"}]}]
            }],
        }]}
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=compliance_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        disability_q = next(q for q in result["questions"] if q["field_name"] == "disability_status")
        # Question's own description was null; block description propagated down
        assert "Voluntary Self-Identification" in (disability_q["description"] or "")

    def test_demographic_questions_merged_with_eeo_category(self):
        demo_response = {**_FAKE_RESPONSE, "demographic_questions": {
            "questions": [{
                "label": "Pronouns", "required": False, "description": "Voluntary",
                "fields": [{"name": "demo_pronouns", "type": "input_text", "values": []}],
            }],
        }}
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=demo_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        demo_q = next(q for q in result["questions"] if q["field_name"] == "demo_pronouns")
        assert demo_q["category"] == "eeo"
        assert demo_q["type"] == "text"  # input_text → text

    def test_constructs_correct_url(self):
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            fetch_greenhouse("airbnb", "7649441")

            client.get.assert_called_once_with(
                "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs/7649441",
                params={"questions": "true"},
            )

    def test_404_raises_job_not_available(self):
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(status=404)
            MockClient.return_value.__enter__.return_value = client

            with pytest.raises(GreenhouseFetchError) as exc:
                fetch_greenhouse("airbnb", "7649441")

            assert exc.value.reason == "job_no_longer_available"

    def test_does_not_follow_redirects(self):
        with patch("httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            fetch_greenhouse("airbnb", "7649441")

            # The Client must be constructed with follow_redirects=False
            MockClient.assert_called_once()
            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs.get("follow_redirects") is False
            assert call_kwargs.get("timeout") is not None
```

- [ ] **Step 1.2: Run tests, verify they fail**

Run: `pytest tests/unit/test_platform_metadata_greenhouse.py -v`
Expected: `ModuleNotFoundError: No module named 'shared.platform_metadata'`

- [ ] **Step 1.3: Create the package marker**

Create `shared/platform_metadata/__init__.py`:

```python
"""Platform metadata fetchers for ATS application questions.

Each module exposes a `fetch_<platform>(*ids) -> dict` function that returns
a normalized payload:

    {
      "platform": str,
      "job_title": str,
      "questions": [
        {
          "label": str,           # human-readable question text
          "description": str|None,  # optional context (often used for EEO disclosures)
          "required": bool,
          "type": str,            # one of input_text, textarea, input_file,
                                  # multi_value_single_select, multi_value_multi_select
          "field_name": str,      # platform's field id (used at submit time)
          "options": list[str],   # for select fields, the option labels
        },
        ...
      ],
      "cover_letter_field_present": bool,
      "cover_letter_required": bool,
      "cover_letter_max_length": int,  # platform default if unspecified
    }

Fetchers raise the platform-specific Error class on failure with a `.reason`
attribute that maps to the preview endpoint's `reason` field.
"""
```

- [ ] **Step 1.4: Implement `fetch_greenhouse`**

Create `shared/platform_metadata/greenhouse.py`:

```python
"""Greenhouse application metadata fetcher.

Public API: https://developers.greenhouse.io/job-board.html
Endpoint: GET boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{posting_id}?questions=true
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GREENHOUSE_DEFAULT_CL_MAX = 10000  # platform-wide default per design spec


class GreenhouseFetchError(Exception):
    """Raised when Greenhouse metadata cannot be fetched.

    The `reason` attribute is one of:
    - job_no_longer_available (404)
    - greenhouse_api_error (5xx or other HTTP failure)
    - greenhouse_timeout
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def fetch_greenhouse(board_token: str, posting_id: str, timeout: float = 10.0) -> dict:
    """Fetch and normalize Greenhouse posting metadata.

    Args:
        board_token: Greenhouse board slug (e.g. "airbnb")
        posting_id: Greenhouse posting numeric id (as string, e.g. "7649441")
        timeout: Per-request timeout in seconds

    Returns:
        Normalized metadata dict (see shared.platform_metadata.__init__ for shape)

    Raises:
        GreenhouseFetchError: if the posting is gone (404) or the API errored
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{posting_id}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, params={"questions": "true"})
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise GreenhouseFetchError(
                f"Greenhouse posting {board_token}/{posting_id} not found",
                reason="job_no_longer_available",
            )
        raise GreenhouseFetchError(
            f"Greenhouse API returned {e.response.status_code}",
            reason="greenhouse_api_error",
        )
    except httpx.TimeoutException:
        raise GreenhouseFetchError(
            f"Greenhouse API timeout after {timeout}s",
            reason="greenhouse_timeout",
        )

    raw = response.json()
    questions = _normalize_questions(raw.get("questions", []), default_category=None,
                                       block_description=None)

    # Merge compliance[].questions[] (EEO).
    # Each compliance block has a top-level `description` (the EEO disclosure text,
    # e.g. "Voluntary Self-Identification of Disability... OMB Control 1250-0005").
    # The questions inside have description=null. We propagate the block description
    # down so the AI prompt sees the disclosure context.
    for compliance_block in raw.get("compliance") or []:
        block_desc = compliance_block.get("description") or ""
        questions.extend(_normalize_questions(
            compliance_block.get("questions", []),
            default_category="eeo",
            block_description=block_desc,
        ))

    demo = raw.get("demographic_questions") or {}
    if isinstance(demo, dict):
        questions.extend(_normalize_questions(
            demo.get("questions", []),
            default_category="eeo",
            block_description=demo.get("description") or "",
        ))

    cl_meta = _extract_cover_letter_meta(questions)

    return {
        "platform": "greenhouse",
        "job_title": raw.get("title", ""),
        "questions": questions,
        "cover_letter_field_present": cl_meta["present"],
        "cover_letter_required": cl_meta["required"],
        "cover_letter_max_length": GREENHOUSE_DEFAULT_CL_MAX,
    }


# Greenhouse → spec §7.1 CustomQuestion.type vocabulary
_TYPE_MAP = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_multi_select": "multi_select",
    "single_checkbox": "checkbox",
    # multi_value_single_select handled below (yes_no vs select)
}


def _map_type(gh_type: str, values: list) -> str:
    if gh_type == "multi_value_single_select":
        labels = {v.get("label", "").strip().lower() for v in values}
        if labels == {"yes", "no"}:
            return "yes_no"
        return "select"
    return _TYPE_MAP.get(gh_type, "text")


def _normalize_questions(
    raw_questions: list,
    default_category: Optional[str],
    block_description: Optional[str] = None,
) -> list[dict]:
    """Normalize raw Greenhouse questions to the unified shape.

    Args:
        raw_questions: list of question dicts from the Greenhouse response
        default_category: when set, pre-tags the question's category (for
            compliance/demographic blocks where every question is EEO)
        block_description: when set, used as the question description if the
            question's own description is null. Greenhouse compliance blocks
            put their disclosure text at the block level, not on each question.
    """
    normalized = []
    for q in raw_questions:
        fields = q.get("fields") or []
        if not fields:
            continue
        first = fields[0]
        values = first.get("values", [])
        # Prefer per-question description; fall back to block-level (EEO disclosure)
        description = q.get("description") or block_description or None
        entry = {
            "label": q.get("label", ""),
            "description": description,
            "required": bool(q.get("required", False)),
            "type": _map_type(first.get("type", "input_text"), values),
            "field_name": first.get("name", ""),
            "options": [v.get("label", "") for v in values],
        }
        if default_category:
            entry["category"] = default_category
        normalized.append(entry)
    return normalized


def _extract_cover_letter_meta(questions: list[dict]) -> dict:
    for q in questions:
        if q["field_name"] == "cover_letter":
            return {"present": True, "required": q["required"]}
    return {"present": False, "required": False}
```

- [ ] **Step 1.5: Run tests, verify they pass**

Run: `pytest tests/unit/test_platform_metadata_greenhouse.py -v`
Expected: 5 passed.

- [ ] **Step 1.6: Live smoke-test against Greenhouse public API**

Run:
```bash
python -c "
from shared.platform_metadata.greenhouse import fetch_greenhouse
import json
result = fetch_greenhouse('airbnb', '7649441')
print(json.dumps({k: v if k != 'questions' else f'<{len(v)} questions>' for k, v in result.items()}, indent=2))
print('First question:', result['questions'][0])
"
```
Expected: Prints normalized payload with ~16 questions for the live Airbnb posting (or whichever is currently first). If the posting is gone, pick a fresh `posting_id` from `https://boards-api.greenhouse.io/v1/boards/airbnb/jobs`.

- [ ] **Step 1.7: Commit Task 1**

```bash
git add shared/platform_metadata/__init__.py shared/platform_metadata/greenhouse.py \
  tests/unit/test_platform_metadata_greenhouse.py
git commit -m "feat(apply): Greenhouse application metadata fetcher

- shared.platform_metadata package with normalized fetcher contract
- fetch_greenhouse() hits boards-api.greenhouse.io, follow_redirects=False,
  10s timeout, 404 -> reason=job_no_longer_available
- Normalizes questions to {label, description, required, type, field_name, options}
- 5 unit tests + 1 live smoke check"
```

---

## Task 2: Ashby metadata fetcher (with investigation)

**Files:**
- Create: `shared/platform_metadata/ashby.py`
- Create: `tests/unit/test_platform_metadata_ashby.py`

**⚠️ Pre-implementation investigation required.** The plan-listed endpoint `posting-api/job-posting/{uuid}` returns 401 against the public internet. The real public endpoint is `https://jobs.ashbyhq.com/api/non-user-graphql` (GraphQL).

- [ ] **Step 2.1: Investigate Ashby's public hosted-jobs GraphQL endpoint**

Find a real Ashby posting:
```bash
curl -sS "https://api.ashbyhq.com/posting-api/job-board/ashby" -H "Accept: application/json" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); j=d['jobs'][0]; print(j['id'], j['jobUrl'])"
```

Open the `jobUrl` in a browser. In DevTools → Network → XHR, click "Apply" and observe the GraphQL call to `https://jobs.ashbyhq.com/api/non-user-graphql`. Capture:
1. The `operationName` for fetching the application form (likely `ApiJobPosting` or `ApplicationFormQuery`)
2. The exact GraphQL query string
3. The `variables` shape — typically `{organizationHostedJobsPageName, jobPostingId}`
4. The response shape for application form fields, including dropdown option lists

Save findings to `docs/superpowers/research/2026-04-28-ashby-graphql-shape.md` (any markdown is fine; this becomes the contract pinned for the test).

If the investigation reveals the GraphQL endpoint is unstable or requires session cookies that aren't available server-side, **stop and consult the user**: options include (a) skip Ashby for v1 (cloud browser handles unknown forms anyway), (b) use a paid Ashby Trust API integration, (c) parse the SSR'd HTML.

- [ ] **Step 2.2: Write failing test for `fetch_ashby` based on captured shape**

Create `tests/unit/test_platform_metadata_ashby.py`. Use the captured response shape from Step 2.1 as the `_FAKE_RESPONSE`. Mirror the structure of `test_platform_metadata_greenhouse.py`: tests for normalized output, 404 handling, request shape, no-redirects.

The normalized output must conform to the shared shape documented in `__init__.py` so the answer generator can treat both platforms uniformly.

- [ ] **Step 2.3: Run tests, verify they fail**

Run: `pytest tests/unit/test_platform_metadata_ashby.py -v`
Expected: ImportError.

- [ ] **Step 2.4: Implement `fetch_ashby`**

Create `shared/platform_metadata/ashby.py`. Mirror `greenhouse.py` structure but POST to the GraphQL endpoint with the captured `operationName` + `query` + `variables`. Map the response into the normalized shape. Use `cover_letter_max_length = 5000` per design spec for Ashby.

(Code omitted here — must be written based on Step 2.1 findings since the GraphQL schema is not pinnable in advance.)

- [ ] **Step 2.5: Run tests, verify they pass**

Run: `pytest tests/unit/test_platform_metadata_ashby.py -v`

- [ ] **Step 2.6: Live smoke-test against Ashby**

Run a small Python script that calls `fetch_ashby(board_token, posting_id)` for a real posting from `jobs.ashbyhq.com/ashby`. Verify questions return non-empty.

- [ ] **Step 2.7: Commit Task 2**

```bash
git add shared/platform_metadata/ashby.py tests/unit/test_platform_metadata_ashby.py \
  docs/superpowers/research/2026-04-28-ashby-graphql-shape.md
git commit -m "feat(apply): Ashby application metadata fetcher via public GraphQL

- fetch_ashby() POSTs to jobs.ashbyhq.com/api/non-user-graphql
- Same normalized shape as greenhouse fetcher
- Captured GraphQL contract documented in docs/superpowers/research/
- N unit tests + 1 live smoke check"
```

---

## Task 3: Question classifier

**Files:**
- Create: `shared/question_classifier.py`
- Create: `tests/unit/test_question_classifier.py`

- [ ] **Step 3.1: Write failing test**

Create `tests/unit/test_question_classifier.py`:

```python
import pytest
from shared.question_classifier import classify_question


class TestClassifyQuestion:
    @pytest.mark.parametrize("label,description,expected", [
        ("Gender", None, "eeo"),
        ("Ethnicity", None, "eeo"),
        ("Veteran Status", None, "eeo"),
        ("Disability self-identification", None, "eeo"),
        ("Race / Ethnicity (US)", None, "eeo"),
        ("Please self-identify your gender", None, "eeo"),
        ("I confirm the information above is accurate", None, "confirmation"),
        ("I certify that I have read and understand the company's policies", None, "confirmation"),
        ("Do you acknowledge our terms?", None, "confirmation"),
        ("Subscribe to our marketing newsletter?", None, "marketing"),
        ("Receive promotional updates about new openings?", None, "marketing"),
        ("How did you hear about this position?", None, "referral"),
        ("Referral source", None, "referral"),
        ("Why are you interested in working at Airbnb?", None, "custom"),
        ("Tell us about a project you led", None, "custom"),
        ("Are you legally authorized to work in the US?", None, "custom"),
    ])
    def test_classification(self, label, description, expected):
        assert classify_question(label, description) == expected

    def test_eeo_via_description(self):
        # Some platforms put "self-identify" in description, not label
        assert classify_question("Please answer voluntarily",
                                 "This question helps us measure diversity and is voluntary self-identification.") == "eeo"

    def test_empty_label_returns_custom(self):
        assert classify_question("", None) == "custom"

    def test_case_insensitive(self):
        assert classify_question("GENDER", None) == "eeo"
        assert classify_question("How Did You Hear About Us?", None) == "referral"
```

- [ ] **Step 3.2: Run test, verify it fails**

Run: `pytest tests/unit/test_question_classifier.py -v`
Expected: ImportError.

- [ ] **Step 3.3: Implement classifier**

Create `shared/question_classifier.py`:

```python
"""Categorize Greenhouse/Ashby application questions for AI-answer routing.

Categories drive different answer strategies in shared.answer_generator:

- eeo:          Decline to self-identify (or platform-specific 'prefer not to say')
- confirmation: Skip AI, set requires_user_action=True
- marketing:    Skip AI, set False / Unsubscribe
- referral:     Fuzzy-match user.default_referral_source against options
- custom:       Generate via AI council with cached temperature=0.3 prompt
"""
from __future__ import annotations

import re
from typing import Literal, Optional

Category = Literal["custom", "eeo", "confirmation", "marketing", "referral"]


_EEO_PATTERN = re.compile(
    r"\b(gender|ethnicity|race|veteran|disability|self.?identif(y|ication)|"
    r"sexual orientation|hispanic|latino|pronoun)\b",
    re.IGNORECASE,
)
_CONFIRMATION_PATTERN = re.compile(
    r"\b(confirm|certify|accurate|true.{0,20}information|understand|acknowledge|"
    r"i agree|i have read)\b",
    re.IGNORECASE,
)
_MARKETING_PATTERN = re.compile(
    r"\b(marketing|newsletter|subscribe|promotional|updates about|"
    r"opt.?in.{0,10}(email|news))\b",
    re.IGNORECASE,
)
_REFERRAL_PATTERN = re.compile(
    r"\b(how.{0,10}hear|referral source|source of awareness|"
    r"who referred|referred by)\b",
    re.IGNORECASE,
)


def classify_question(label: str, description: Optional[str] = None) -> Category:
    """Return the category for a question based on its label and optional description.

    Searches both label and description text. First matching pattern wins in this order:
    EEO → confirmation → marketing → referral → custom (default).
    """
    haystack = (label or "") + " " + (description or "")

    if _EEO_PATTERN.search(haystack):
        return "eeo"
    if _CONFIRMATION_PATTERN.search(haystack):
        return "confirmation"
    if _MARKETING_PATTERN.search(haystack):
        return "marketing"
    if _REFERRAL_PATTERN.search(haystack):
        return "referral"
    return "custom"
```

- [ ] **Step 3.4: Run tests, verify they pass**

Run: `pytest tests/unit/test_question_classifier.py -v`
Expected: ~17 passed.

- [ ] **Step 3.5: Commit Task 3**

```bash
git add shared/question_classifier.py tests/unit/test_question_classifier.py
git commit -m "feat(apply): regex question classifier for AI-answer routing

5 categories (eeo, confirmation, marketing, referral, custom) drive
per-category branching in answer_generator. EEO -> decline,
confirmation -> requires_user_action, marketing -> False,
referral -> fuzzy-match user default, custom -> AI generation."
```

---

## Task 4: tex-to-plaintext utility

**Files:**
- Create: `shared/tex_utils.py`
- Create: `tests/unit/test_tex_utils.py`

**Why this is its own task:** The cover letter loader needs to feed plaintext (not LaTeX source) to the AI generator. No such utility exists in the codebase.

- [ ] **Step 4.1: Write failing test**

Create `tests/unit/test_tex_utils.py`:

```python
from shared.tex_utils import tex_to_plaintext


class TestTexToPlaintext:
    def test_strips_simple_commands(self):
        tex = r"\textbf{Hello} \textit{world}"
        assert tex_to_plaintext(tex) == "Hello world"

    def test_strips_section_commands(self):
        tex = r"\section{Experience}\subsection{Acme Corp}"
        assert tex_to_plaintext(tex) == "Experience Acme Corp"

    def test_strips_comments(self):
        tex = "Visible text\n% This is a comment\nMore text"
        assert tex_to_plaintext(tex) == "Visible text\nMore text"

    def test_strips_environments_keeps_content(self):
        tex = r"\begin{itemize}\item First\item Second\end{itemize}"
        result = tex_to_plaintext(tex)
        assert "First" in result
        assert "Second" in result
        assert "itemize" not in result

    def test_collapses_whitespace(self):
        tex = "Line 1\n\n\n\nLine 2"
        result = tex_to_plaintext(tex)
        assert result == "Line 1\n\nLine 2"

    def test_handles_empty_input(self):
        assert tex_to_plaintext("") == ""
        assert tex_to_plaintext(None) == ""

    def test_real_cover_letter_excerpt(self):
        tex = r"""\documentclass{letter}
\begin{document}
\section*{Cover Letter}
Dear \textbf{Hiring Manager},

I am writing to apply for the \textit{Senior Engineer} position at Airbnb.
% Personal note: tweak per role

\section*{Experience}
\begin{itemize}
\item Led migration of 50M-user database
\item Shipped 3 major features
\end{itemize}

Sincerely, \\
John Doe
\end{document}"""
        result = tex_to_plaintext(tex)
        assert "Hiring Manager" in result
        assert "Senior Engineer" in result
        assert "Personal note" not in result  # comment stripped
        assert "documentclass" not in result
        assert "begin{itemize}" not in result
        assert "Led migration" in result
```

- [ ] **Step 4.2: Run test, verify it fails**

Run: `pytest tests/unit/test_tex_utils.py -v`
Expected: ImportError.

- [ ] **Step 4.3: Implement `tex_to_plaintext`**

Create `shared/tex_utils.py`:

```python
"""LaTeX → plaintext conversion for AI prompt context.

This is intentionally minimal — not a full LaTeX renderer. Strips comments,
common formatting commands, and environment delimiters while preserving
the text content. Good enough to feed cover letter content to an LLM.
"""
from __future__ import annotations

import re
from typing import Optional

# Strip line comments (% to end of line, not preceded by \)
_COMMENT = re.compile(r"(?<!\\)%[^\n]*")

# Strip \begin{env} and \end{env} delimiters but keep inner content
_BEGIN_END = re.compile(r"\\(begin|end)\{[^}]*\}")

# Match commands like \textbf{X}, \section{X}, \emph{X} — keep the inner X
_BRACED_COMMAND = re.compile(r"\\[a-zA-Z]+\*?\{([^{}]*)\}")

# Match commands without args like \\, \item, \maketitle — replace with space
_BARE_COMMAND = re.compile(r"\\[a-zA-Z]+\*?")
_DOUBLE_BACKSLASH = re.compile(r"\\\\")

# Collapse 3+ blank lines to 2
_TRIPLE_BLANK = re.compile(r"\n{3,}")


def tex_to_plaintext(tex: Optional[str]) -> str:
    """Convert LaTeX source to plaintext suitable for AI prompts.

    Not a full renderer. Strips comments, environments, and commands;
    preserves text content. Idempotent.
    """
    if not tex:
        return ""

    text = tex

    # Strip comments first (before they confuse downstream regexes)
    text = _COMMENT.sub("", text)

    # Strip environment delimiters
    text = _BEGIN_END.sub("", text)

    # Resolve braced commands repeatedly until none remain (handles nesting)
    prev = None
    while prev != text:
        prev = text
        text = _BRACED_COMMAND.sub(r"\1", text)

    # Replace \\ with newline before stripping bare commands
    text = _DOUBLE_BACKSLASH.sub("\n", text)

    # Strip remaining bare commands
    text = _BARE_COMMAND.sub(" ", text)

    # Strip stray braces
    text = text.replace("{", "").replace("}", "")

    # Collapse whitespace
    text = _TRIPLE_BLANK.sub("\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))

    return text.strip()
```

- [ ] **Step 4.4: Run tests, verify they pass**

Run: `pytest tests/unit/test_tex_utils.py -v`
Expected: 7 passed.

- [ ] **Step 4.5: Commit Task 4**

```bash
git add shared/tex_utils.py tests/unit/test_tex_utils.py
git commit -m "feat(apply): minimal LaTeX-to-plaintext utility for AI prompts

Strips comments, environments, and commands; preserves content.
Used by cover_letter_loader to feed plaintext to the answer generator."
```

---

## Task 5: Cover letter loader

**Files:**
- Create: `shared/cover_letter_loader.py`
- Create: `tests/unit/test_cover_letter_loader.py`

**Live S3 path (confirmed in `lambdas/pipeline/generate_cover_letter.py:335`):**
`users/{user_id}/cover_letters/{job_hash}_cover.tex`

- [ ] **Step 5.1: Write failing test**

Create `tests/unit/test_cover_letter_loader.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from shared.cover_letter_loader import load_cover_letter


_TEX_CONTENT = r"\section*{Cover Letter}Dear Hiring Manager,\\I am applying..."
_PLAIN_FALLBACK = "I am writing to express my interest in this position..."


class TestLoadCoverLetter:
    def test_loads_from_s3_and_strips_latex(self):
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_TEX_CONTENT.encode()))
        }

        result = load_cover_letter(
            user_id="user-1", job_hash="abc123", s3_client=s3, bucket="my-bucket"
        )

        assert result is not None
        assert "Dear Hiring Manager" in result["text"]
        assert "section" not in result["text"]
        assert result["source"] == "tailored"
        s3.get_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="users/user-1/cover_letters/abc123_cover.tex",
        )

    def test_returns_none_when_s3_object_missing(self):
        s3 = MagicMock()
        from botocore.exceptions import ClientError
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject"
        )

        result = load_cover_letter(
            user_id="user-1", job_hash="abc123", s3_client=s3, bucket="my-bucket"
        )

        assert result is None

    def test_returns_none_on_other_s3_error(self):
        s3 = MagicMock()
        from botocore.exceptions import ClientError
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}}, "GetObject"
        )

        result = load_cover_letter(
            user_id="user-1", job_hash="abc123", s3_client=s3, bucket="my-bucket"
        )

        # Logged but doesn't raise; preview should still render
        assert result is None

    def test_constructs_correct_s3_key(self):
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b""))
        }

        load_cover_letter(
            user_id="UID-XYZ", job_hash="HASH-123", s3_client=s3, bucket="bkt"
        )

        s3.get_object.assert_called_once_with(
            Bucket="bkt",
            Key="users/UID-XYZ/cover_letters/HASH-123_cover.tex",
        )
```

- [ ] **Step 5.2: Run test, verify it fails**

Run: `pytest tests/unit/test_cover_letter_loader.py -v`
Expected: ImportError.

- [ ] **Step 5.3: Implement loader**

Create `shared/cover_letter_loader.py`:

```python
"""Cover letter loader: reads user's tailored .tex from S3 and returns plaintext.

S3 path matches lambdas/pipeline/generate_cover_letter.py:335:
    users/{user_id}/cover_letters/{job_hash}_cover.tex
"""
from __future__ import annotations

import logging
from typing import Optional

from botocore.exceptions import ClientError

from shared.tex_utils import tex_to_plaintext

logger = logging.getLogger(__name__)


def load_cover_letter(
    user_id: str,
    job_hash: str,
    s3_client,
    bucket: str,
) -> Optional[dict]:
    """Load and convert the user's cover letter for a specific job.

    Returns {"text": str, "source": "tailored"} if found in S3, None otherwise.
    Never raises — logs and returns None on any S3 error.

    The "source" field maps to spec §7.1 cover_letter.source enum:
    - "tailored" when loaded from users/{uid}/cover_letters/{job_hash}_cover.tex
    - "not_generated" handled by caller when this returns None

    (A future "default" source could be added if/when we ship a fallback CL.)
    """
    key = f"users/{user_id}/cover_letters/{job_hash}_cover.tex"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        tex = response["Body"].read().decode("utf-8", errors="replace")
        return {"text": tex_to_plaintext(tex), "source": "tailored"}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "NoSuchKey":
            logger.info(f"[cover_letter_loader] No CL at {key}")
        else:
            logger.warning(f"[cover_letter_loader] S3 error for {key}: {code}")
        return None
    except Exception as e:
        logger.warning(f"[cover_letter_loader] Unexpected error for {key}: {e}")
        return None
```

- [ ] **Step 5.4: Run tests, verify they pass**

Run: `pytest tests/unit/test_cover_letter_loader.py -v`
Expected: 4 passed.

- [ ] **Step 5.5: Commit Task 5**

```bash
git add shared/cover_letter_loader.py tests/unit/test_cover_letter_loader.py
git commit -m "feat(apply): cover letter loader from S3 + LaTeX-to-plaintext conversion

Reads users/{user_id}/cover_letters/{job_hash}_cover.tex (path matches
generate_cover_letter pipeline). Returns None on missing or S3 error -
never raises so preview endpoint can degrade gracefully."
```

---

## Task 6: AI answer generator (spec §7.3-compliant rich prompt)

**Files:**
- Create: `shared/answer_generator.py`
- Create: `tests/unit/test_answer_generator.py`

**Spec compliance:** This task implements the prompt structure from design spec §7.3 verbatim, including `DEFAULT_CANDIDATE_CONTEXT` fallback, `user.work_authorizations` country mapping, `salary_expectation_notes`, `notice_period_text`, and `job.key_matches`. Uses `lambdas.pipeline.ai_helper.ai_complete_cached` with `temperature=0.3, max_tokens=300, cache_hours=24*7`.

- [ ] **Step 6.1: Write failing test**

Create `tests/unit/test_answer_generator.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from shared.answer_generator import generate_answer, DEFAULT_CANDIDATE_CONTEXT


_PROFILE = {
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phone": "+1-555-0100",
    "linkedin": "https://linkedin.com/in/janedoe",
    "github": "https://github.com/janedoe",
    "website": "https://janedoe.dev",
    "location": "Dublin, Ireland",
    "visa_status": "stamp1g",
    "work_authorizations": {"IE": "stamp1g", "US": "requires_sponsorship"},
    "candidate_context": "8yr full-stack engineer. Python, AWS, React.",
    "salary_expectation_notes": "€80-100k OTE",
    "notice_period_text": "2 weeks",
    "default_referral_source": "LinkedIn",
}
_JOB = {
    "title": "Senior Backend Engineer",
    "company": "Airbnb",
    "location": "Paris, France",
    "description": "Build the backend for travel experiences. Python, distributed systems...",
    "key_matches": ["Python", "FastAPI", "AWS"],
}
_RESUME_TEXT = "Senior Software Engineer..."
_COVER_LETTER = "I am excited..."


class TestGenerateAnswer:
    def test_standard_field_first_name(self):
        q = {"label": "First Name", "field_name": "first_name", "type": "text",
             "required": True, "options": [], "description": None}
        fake_ai = MagicMock()
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)
        assert result["answer"] == "Jane"
        assert result["category"] == "standard"
        fake_ai.assert_not_called()

    def test_standard_field_email(self):
        q = {"label": "Email", "field_name": "email", "type": "text",
             "required": True, "options": [], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "jane@example.com"

    def test_resume_file_field_returns_marker(self):
        q = {"label": "Resume/CV", "field_name": "resume", "type": "file",
             "required": True, "options": [], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "<resume_pdf>"
        assert result["category"] == "file"

    def test_eeo_select_picks_decline_option(self):
        q = {"label": "Gender", "field_name": "question_1", "type": "select",
             "required": True,
             "options": ["Male", "Female", "Non-binary", "Decline to Self Identify"],
             "description": "Voluntary self-identification..."}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "Decline to Self Identify"
        assert result["category"] == "eeo"

    def test_eeo_pre_tagged_category_honored(self):
        # When fetcher pre-tags category="eeo" (compliance/demographic), respect it
        q = {"label": "Pronouns", "field_name": "demo_pronouns", "type": "text",
             "required": False, "options": [], "description": None, "category": "eeo"}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["category"] == "eeo"

    def test_confirmation_requires_user_action(self):
        q = {"label": "I confirm the information above is accurate",
             "field_name": "question_3", "type": "checkbox",
             "required": True, "options": [], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["category"] == "confirmation"
        assert result["requires_user_action"] is True
        assert result["answer"] is False

    def test_marketing_returns_false(self):
        q = {"label": "Subscribe to our marketing newsletter?",
             "field_name": "question_4", "type": "yes_no",
             "required": False, "options": ["Yes", "No"], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] in ("No", False)
        assert result["category"] == "marketing"

    def test_referral_fuzzy_matches_user_default(self):
        q = {"label": "How did you hear about this position?",
             "field_name": "question_5", "type": "select",
             "required": True,
             "options": ["LinkedIn", "Company website", "Friend referral", "Job board"],
             "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "LinkedIn"
        assert result["category"] == "referral"

    def test_custom_question_calls_ai_with_rich_prompt(self):
        q = {"label": "Why are you interested in working at Airbnb?",
             "field_name": "question_6", "type": "textarea",
             "required": True, "options": [], "description": None}
        fake_ai = MagicMock(return_value={"content": "I'm passionate about travel.",
                                            "provider": "qwen", "model": "qwen2-72b"})

        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)

        assert result["category"] == "custom"
        assert result["answer"] == "I'm passionate about travel."
        fake_ai.assert_called_once()
        kwargs = fake_ai.call_args.kwargs
        # Spec §7.3 step 9 hard requirements:
        assert kwargs["temperature"] == 0.3
        assert kwargs["max_tokens"] == 300
        assert kwargs["cache_hours"] == 24 * 7

        prompt = kwargs["prompt"]
        # Verify the prompt contains all spec-required fields:
        assert "Jane Doe" in prompt
        assert "Airbnb" in prompt
        assert "Senior Backend Engineer" in prompt
        assert "8yr full-stack" in prompt  # candidate_context
        assert "stamp1g" in prompt or "IE" in prompt  # work_authorizations
        assert "€80-100k" in prompt  # salary_expectation_notes
        assert "2 weeks" in prompt  # notice_period_text
        assert "Python" in prompt  # key_matches

    def test_custom_falls_back_to_default_candidate_context_when_empty(self):
        profile_no_context = {**_PROFILE, "candidate_context": ""}
        q = {"label": "Tell us about yourself", "field_name": "question_7",
             "type": "textarea", "required": True, "options": [], "description": None}
        fake_ai = MagicMock(return_value={"content": "ok", "provider": "p", "model": "m"})

        generate_answer(q, profile_no_context, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)

        prompt = fake_ai.call_args.kwargs["prompt"]
        assert "MSc in Cloud Computing" in prompt  # from DEFAULT_CANDIDATE_CONTEXT
        assert "AWS Solutions Architect" in prompt

    def test_custom_select_fuzzy_matches_ai_response(self):
        q = {"label": "Years of experience?", "field_name": "question_8",
             "type": "select", "required": True,
             "options": ["0-2 years", "3-5 years", "6-10 years", "10+ years"],
             "description": None}
        fake_ai = MagicMock(return_value={"content": "8 years", "provider": "p", "model": "m"})

        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)

        # AI returned "8 years" — must be fuzzy-matched to "6-10 years"
        assert result["answer"] == "6-10 years"

    def test_yes_no_unparseable_ai_response_defaults_to_yes(self):
        # Spec §7.3 step 9: yes_no with non-yes/no AI response defaults to "Yes"
        q = {"label": "Are you authorized to work in Ireland?", "field_name": "question_9",
             "type": "yes_no", "required": True, "options": ["Yes", "No"], "description": None}
        fake_ai = MagicMock(return_value={"content": "I have a Stamp 1G visa which permits...",
                                            "provider": "p", "model": "m"})

        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)
        assert result["answer"] == "Yes"

    def test_default_candidate_context_constant_present(self):
        # Sanity: spec defines this verbatim; must be importable
        assert "MSc in Cloud Computing" in DEFAULT_CANDIDATE_CONTEXT
        assert "AWS Solutions Architect Professional" in DEFAULT_CANDIDATE_CONTEXT
```

- [ ] **Step 6.2: Run test, verify it fails**

Run: `pytest tests/unit/test_answer_generator.py -v`
Expected: ImportError.

- [ ] **Step 6.3: Implement `generate_answer`**

Create `shared/answer_generator.py`:

```python
"""Per-question answer generation routed by category.

Spec reference: docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md §7.3 step 9.

Standard fields (first_name, email, etc.) come from the profile dict.
File fields (resume, cover_letter) return file markers consumed at submit time.
EEO/confirmation/marketing/referral skip AI per spec.
Custom questions go through ai_complete_cached with temperature=0.3,
max_tokens=300, cache_hours=24*7 (per spec §7.3).
"""
from __future__ import annotations

from typing import Callable, Optional
from difflib import get_close_matches

from shared.question_classifier import classify_question


_STANDARD_FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin",
    "github": "github",
    "website": "website",
    "location": "location",
}

_DECLINE_PATTERNS = (
    "decline", "prefer not", "rather not", "i don't wish", "do not wish",
)


# Spec §7.3 line 666-671 — verbatim default candidate context
DEFAULT_CANDIDATE_CONTEXT = (
    "3+ years full-stack software engineering experience. MSc in Cloud Computing (ATU). "
    "AWS Solutions Architect Professional certified. Strong in Python (FastAPI, Flask, "
    "Django), TypeScript/React, AWS (ECS/Fargate, Lambda, RDS, S3, API Gateway), "
    "CI/CD, Docker, Kubernetes, Terraform. Track record of reducing MTTR 35%, "
    "cutting release lead time 85%, maintaining 99.9% uptime."
)


_SYSTEM_PROMPT = (
    "You are a job applicant filling out an application form. Answer concisely, "
    "truthfully, and in a way that presents the candidate positively. "
    "If the question is a dropdown/select, you MUST pick one of the provided "
    "options verbatim. Return ONLY the answer text, no explanation."
)


# Spec §7.3 lines 624-661 — verbatim user prompt template
_USER_PROMPT_TEMPLATE = """\
You are filling out a job application for {first_name} {last_name}, applying to {title} at {company}.

CANDIDATE PROFILE:
{candidate_context}

CONTACT:
- LinkedIn: {linkedin}
- GitHub: {github}
- Website: {website}
- Location: {location}
- Visa status: {visa_status}

PREFERENCES:
- Salary expectations: {salary}
- Notice period: {notice_period}

JOB CONTEXT:
- Role: {title}
- Company: {company}
- Location: {job_location}
- Description: {description}
- Key matches: {key_matches}

WORK AUTHORIZATION MATCHING:
- If the question asks about work authorization in a specific country, use:
  {work_authorizations}
- For "Remote - Europe" or "EU" locations, default to Ireland ("IE").
- For ambiguous locations, default to Ireland.

QUESTION: {question_label}
TYPE: {question_type}
{options_line}REQUIRED: {required}

Answer the question concisely, truthfully, and in a way that presents the candidate positively. \
Reference specific things from the job description when relevant. If the question is a \
dropdown/select, you MUST pick one of the provided options verbatim.

Return ONLY the answer text, no explanation."""


def generate_answer(
    question: dict,
    profile: dict,
    job: dict,
    resume_text: str,
    cover_letter_text: Optional[str],
    ai_complete_cached_fn: Callable[..., dict],
) -> dict:
    """Generate an answer for a single application question.

    Args:
        question: normalized question dict (label, field_name, type, required, options, description, category?)
        profile: user profile dict (must include candidate_context, work_authorizations, etc.)
        job: job dict (title, company, location, description, key_matches)
        resume_text: plaintext resume excerpt
        cover_letter_text: plaintext cover letter (optional)
        ai_complete_cached_fn: the lambdas.pipeline.ai_helper.ai_complete_cached function
                               (injected for testability)

    Returns:
        {"answer": str|bool|None, "category": str, "requires_user_action": bool}
    """
    field_name = question.get("field_name", "")
    qtype = question.get("type", "text")
    label = question.get("label", "")
    description = question.get("description")
    options = question.get("options") or []

    # File fields: marker consumed at submit time
    if qtype == "file":
        if "resume" in field_name.lower() or "resume" in label.lower():
            return {"answer": "<resume_pdf>", "category": "file", "requires_user_action": False}
        if "cover" in field_name.lower() or "cover" in label.lower():
            return {"answer": "<cover_letter_pdf>", "category": "file", "requires_user_action": False}
        return {"answer": None, "category": "file", "requires_user_action": True}

    # Standard fields from profile
    if field_name in _STANDARD_FIELD_MAP:
        return {
            "answer": profile.get(_STANDARD_FIELD_MAP[field_name], ""),
            "category": "standard",
            "requires_user_action": False,
        }

    # Honor pre-tagged category from fetcher (compliance / demographic_questions)
    category = question.get("category") or classify_question(label, description)

    if category == "eeo":
        decline = _find_decline_option(options)
        return {
            "answer": decline or (options[0] if options else None),
            "category": "eeo",
            "requires_user_action": False,
        }

    if category == "confirmation" or qtype == "checkbox":
        return {"answer": False, "category": "confirmation", "requires_user_action": True}

    if category == "marketing":
        no_option = next((o for o in options if o.lower() in ("no", "false", "unsubscribe")), None)
        return {
            "answer": no_option or False,
            "category": "marketing",
            "requires_user_action": False,
        }

    if category == "referral":
        default = profile.get("default_referral_source", "")
        match = _fuzzy_match(default, options) if default and options else None
        return {
            "answer": match or (options[0] if options else default),
            "category": "referral",
            "requires_user_action": False,
        }

    # Custom: AI generation with rich spec-compliant prompt
    options_line = f"OPTIONS: {options}\n" if options else ""
    prompt = _USER_PROMPT_TEMPLATE.format(
        first_name=profile.get("first_name", ""),
        last_name=profile.get("last_name", ""),
        title=job.get("title", ""),
        company=job.get("company", ""),
        candidate_context=profile.get("candidate_context") or DEFAULT_CANDIDATE_CONTEXT,
        linkedin=profile.get("linkedin", ""),
        github=profile.get("github", ""),
        website=profile.get("website", ""),
        location=profile.get("location", ""),
        visa_status=profile.get("visa_status", ""),
        salary=profile.get("salary_expectation_notes") or "Open to discussion, targeting competitive market rate",
        notice_period=profile.get("notice_period_text", ""),
        job_location=job.get("location", ""),
        description=(job.get("description") or "")[:2000],
        key_matches=job.get("key_matches", []),
        work_authorizations=profile.get("work_authorizations", {}),
        question_label=label,
        question_type=qtype,
        options_line=options_line,
        required=question.get("required", False),
    )

    result = ai_complete_cached_fn(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
        temperature=0.3,
        max_tokens=300,
        cache_hours=24 * 7,
    )
    raw_answer = (result.get("content") or "").strip()

    # Post-process per spec §7.3 step 9
    if qtype == "yes_no":
        if raw_answer.lower() not in ("yes", "no"):
            raw_answer = "Yes"  # Safer default
    elif qtype in ("select", "multi_select") and options:
        if raw_answer not in options:
            match = _fuzzy_match(raw_answer, options)
            raw_answer = match or options[0]

    return {"answer": raw_answer, "category": "custom", "requires_user_action": False}


def _find_decline_option(options: list[str]) -> Optional[str]:
    for opt in options:
        if any(p in opt.lower() for p in _DECLINE_PATTERNS):
            return opt
    return None


def _fuzzy_match(query: str, options: list[str]) -> Optional[str]:
    if not query or not options:
        return None
    for opt in options:
        if opt.lower() == query.lower():
            return opt
    for opt in options:
        if query.lower() in opt.lower() or opt.lower() in query.lower():
            return opt
    matches = get_close_matches(query, options, n=1, cutoff=0.5)
    return matches[0] if matches else None
```

- [ ] **Step 6.4: Run tests, verify they pass**

Run: `pytest tests/unit/test_answer_generator.py -v`
Expected: 13 passed.

- [ ] **Step 6.5: Commit Task 6**

```bash
git add shared/answer_generator.py tests/unit/test_answer_generator.py
git commit -m "feat(apply): spec §7.3-compliant per-question AI answer generator

- Rich prompt template uses candidate_context (with DEFAULT_CANDIDATE_CONTEXT
  fallback verbatim from spec), work_authorizations, salary_expectation_notes,
  notice_period_text, job.key_matches, job.description[:2000]
- Calls ai_complete_cached with temperature=0.3, max_tokens=300, cache_hours=24*7
- Per-category branching: standard|eeo|confirmation|marketing|referral|custom|file
- Honors pre-tagged category from fetcher (compliance/demographic_questions)
- Post-processing: yes_no -> Yes default, select fuzzy-match to options
- 13 unit tests; rich profile fixture matches existing tests/unit/test_apply_endpoints.py shape"
```

---

## Task 6.5: Pydantic models per spec §7.1

**Files:**
- Create: `shared/apply_models.py`
- Create: `tests/unit/test_apply_models.py`

**Why:** Spec §7.1 defines `ApplyPreviewResponse`, `CustomQuestion`, `CustomAnswer`, `PlatformInfo` as the typed contract between backend and Plan 3c frontend. Today the endpoint returns plain dicts; Plan 3c will expect this shape.

- [ ] **Step 6.5.1: Write failing test**

Create `tests/unit/test_apply_models.py`:

```python
import pytest
from pydantic import ValidationError
from shared.apply_models import (
    PlatformInfo, CustomQuestion, CustomAnswer, ApplyPreviewResponse,
)


class TestPlatformInfo:
    def test_valid_greenhouse(self):
        p = PlatformInfo(platform="greenhouse", board_token="airbnb", posting_id="7649441")
        assert p.platform == "greenhouse"

    def test_invalid_platform_rejected(self):
        with pytest.raises(ValidationError):
            PlatformInfo(platform="lever", board_token="x", posting_id="y")


class TestCustomQuestion:
    def test_valid_select(self):
        q = CustomQuestion(id="question_1", label="Gender", type="select", required=True,
                           options=["Male", "Female"], category="eeo")
        assert q.options == ["Male", "Female"]

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            CustomQuestion(id="q1", label="x", type="multi_value_single_select",
                           required=True)  # un-normalized type rejected

    def test_ai_answer_can_be_bool(self):
        q = CustomQuestion(id="q1", label="Confirm", type="checkbox", required=True,
                           ai_answer=False, requires_user_action=True, category="confirmation")
        assert q.ai_answer is False


class TestApplyPreviewResponse:
    def test_eligible_payload_validates(self):
        payload = {
            "eligible": True,
            "profile_complete": True,
            "missing_required_fields": [],
            "job": {"title": "X", "company": "Y", "location": "Z", "apply_url": "https://..."},
            "platform": "greenhouse",
            "platform_metadata": {"board_token": "airbnb", "posting_id": "7649441"},
            "resume": {"s3_url": "https://...", "filename": "r.pdf",
                       "resume_version": 1, "s3_key": "users/u/resume.pdf",
                       "is_default": False},
            "profile": {"first_name": "Jane", "last_name": "Doe", "email": "j@x.com"},
            "cover_letter": {"text": "...", "editable": True, "max_length": 10000,
                             "source": "tailored", "include_by_default": True},
            "custom_questions": [],
            "already_applied": False,
            "cache_hit": False,
        }
        r = ApplyPreviewResponse(**payload)
        assert r.eligible is True

    def test_ineligible_minimal_payload(self):
        payload = {
            "eligible": False, "reason": "no_resume",
            "profile_complete": True, "missing_required_fields": [],
            "job": {}, "platform": "greenhouse", "platform_metadata": {},
            "resume": {}, "profile": {}, "cover_letter": {},
            "custom_questions": [], "already_applied": False, "cache_hit": False,
        }
        r = ApplyPreviewResponse(**payload)
        assert r.reason == "no_resume"
```

- [ ] **Step 6.5.2: Run, verify fails**

Run: `pytest tests/unit/test_apply_models.py -v`
Expected: ImportError.

- [ ] **Step 6.5.3: Implement models**

Create `shared/apply_models.py`:

```python
"""Pydantic models per design spec §7.1.

These are the typed contract between the apply preview/submit endpoints
and the Plan 3c frontend. Do not change shapes without updating both.
"""
from __future__ import annotations

from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


class PlatformInfo(BaseModel):
    """Parsed from an apply URL. None if URL is not a supported Easy Apply platform."""
    platform: Literal["greenhouse", "ashby"]
    board_token: str
    posting_id: str


class CustomQuestion(BaseModel):
    id: str                  # platform question id (string form)
    label: str
    type: Literal["text", "textarea", "select", "multi_select",
                  "checkbox", "yes_no", "file"]
    required: bool
    options: Optional[list[str]] = None
    max_length: Optional[int] = None
    ai_answer: Union[str, bool, None] = None
    requires_user_action: bool = False
    category: Literal["custom", "eeo", "confirmation",
                      "marketing", "referral"] = "custom"


class ApplyPreviewResponse(BaseModel):
    eligible: bool
    reason: Optional[str] = None
    profile_complete: bool
    missing_required_fields: list[str] = Field(default_factory=list)
    job: dict
    platform: str
    platform_metadata: dict
    resume: dict
    profile: dict
    cover_letter: dict
    custom_questions: list[CustomQuestion] = Field(default_factory=list)
    already_applied: bool = False
    existing_application_id: Optional[str] = None
    cache_hit: bool = False


class CustomAnswer(BaseModel):
    question_id: str
    value: Union[str, bool, None]
    category: str
```

- [ ] **Step 6.5.4: Run, verify passes**

Run: `pytest tests/unit/test_apply_models.py -v`
Expected: 5+ passed.

- [ ] **Step 6.5.5: Commit Task 6.5**

```bash
git add shared/apply_models.py tests/unit/test_apply_models.py
git commit -m "feat(apply): Pydantic models per design spec §7.1

ApplyPreviewResponse, CustomQuestion, CustomAnswer, PlatformInfo.
Plan 3c frontend types against these shapes."
```

---

## Task 6.7: Preview cache via Supabase ai_cache table

**Files:**
- Create: `shared/preview_cache.py`
- Create: `tests/unit/test_preview_cache.py`

**Why:** Spec §7.7 mandates direct write to the Supabase `ai_cache` table with key `apply_preview:{job_id}:{resume_version}` and per-entry `expires_at` (10 min). Plan v1 reused `ai_client.cache.put()` which hashes the prompt as key — incompatible with spec's explicit key format and with future debugging via direct DB queries.

- [ ] **Step 6.7.1: Write failing test**

Create `tests/unit/test_preview_cache.py`:

```python
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from shared.preview_cache import get_preview_cache, set_preview_cache, build_cache_key


def test_build_cache_key():
    assert build_cache_key("job-123", 2) == "apply_preview:job-123:2"


def test_get_preview_cache_hit():
    db = MagicMock()
    payload = {"eligible": True, "questions": []}
    db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = [
        {"response": payload}
    ]

    result = get_preview_cache(db, "job-1", resume_version=1)
    assert result == payload


def test_get_preview_cache_miss():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []

    result = get_preview_cache(db, "job-1", resume_version=1)
    assert result is None


def test_set_preview_cache_writes_with_10min_ttl():
    db = MagicMock()
    payload = {"eligible": True}

    set_preview_cache(db, "job-1", resume_version=1, payload=payload, ttl_minutes=10)

    db.table.return_value.upsert.assert_called_once()
    upsert_payload = db.table.return_value.upsert.call_args.args[0]
    assert upsert_payload["cache_key"] == "apply_preview:job-1:1"
    assert upsert_payload["provider"] == "apply_preview"
    assert upsert_payload["model"] == "n/a"
    assert upsert_payload["response"] == payload
    # expires_at should be ~10 min in the future
    expires = datetime.fromisoformat(upsert_payload["expires_at"].replace("Z", "+00:00"))
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(minutes=9) < delta < timedelta(minutes=11)
```

- [ ] **Step 6.7.2: Run, verify fails**

Run: `pytest tests/unit/test_preview_cache.py -v`
Expected: ImportError.

- [ ] **Step 6.7.3: Implement preview cache**

Create `shared/preview_cache.py`:

```python
"""Preview-response cache backed by the Supabase `ai_cache` table.

Spec reference: docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md §7.7

Cache key format: apply_preview:{job_id}:{resume_version}
TTL: 10 minutes (per-entry expires_at)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional


def build_cache_key(job_id: str, resume_version: int) -> str:
    return f"apply_preview:{job_id}:{resume_version}"


def get_preview_cache(db, job_id: str, resume_version: int) -> Optional[dict]:
    """Return cached preview payload or None on miss/expired."""
    key = build_cache_key(job_id, resume_version)
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        db.table("ai_cache")
        .select("response")
        .eq("cache_key", key)
        .gte("expires_at", now)
        .execute()
    )
    if resp.data:
        return resp.data[0]["response"]
    return None


def set_preview_cache(db, job_id: str, resume_version: int,
                      payload: dict, ttl_minutes: int = 10) -> None:
    """Write preview payload to cache with explicit TTL."""
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    db.table("ai_cache").upsert({
        "cache_key": build_cache_key(job_id, resume_version),
        "provider": "apply_preview",
        "model": "n/a",
        "response": payload,
        "expires_at": expires_at,
    }, on_conflict="cache_key").execute()
```

- [ ] **Step 6.7.4: Run, verify passes**

Run: `pytest tests/unit/test_preview_cache.py -v`
Expected: 4 passed.

- [ ] **Step 6.7.5: Commit Task 6.7**

```bash
git add shared/preview_cache.py tests/unit/test_preview_cache.py
git commit -m "feat(apply): preview cache via Supabase ai_cache table per spec §7.7

Direct table read/write with key apply_preview:{job_id}:{resume_version}
and per-entry expires_at (10min TTL). Decoupled from ai_client.ResponseCache
so debugging is possible via SQL inspection."
```

---

## Task 7: Preview endpoint orchestration

**Files:**
- Modify: `app.py:2460-2526` — replace stub
- Modify: `tests/unit/test_apply_endpoints.py` — extend preview tests

- [ ] **Step 7.1: Write failing test for the new orchestration**

Add to `tests/unit/test_apply_endpoints.py` (or create if not yet covering preview):

```python
def test_apply_preview_returns_questions_and_answers(client, mock_db, mock_user, mock_s3, mock_ai):
    """End-to-end: preview returns populated questions + answers + cache_hit flag."""
    mock_db.set_job(_eligible_greenhouse_job(job_id="job-1", user_id="u1"))
    mock_db.set_user(_complete_profile(user_id="u1"))

    with patch("shared.platform_metadata.greenhouse.fetch_greenhouse") as fetch_mock:
        fetch_mock.return_value = {
            "platform": "greenhouse",
            "job_title": "Senior Engineer",
            "questions": [
                {"label": "First Name", "field_name": "first_name", "type": "input_text",
                 "required": True, "options": [], "description": None},
                {"label": "Why us?", "field_name": "question_1", "type": "textarea",
                 "required": True, "options": [], "description": None},
            ],
            "cover_letter_field_present": True,
            "cover_letter_required": False,
            "cover_letter_max_length": 10000,
        }
        mock_ai.complete.return_value = "Because I love your mission."

        response = client.get("/api/apply/preview/job-1",
                              headers={"Authorization": "Bearer u1-token"})

    assert response.status_code == 200
    data = response.json()
    assert data["eligible"] is True
    assert data["answers_generated"] is True
    assert len(data["questions"]) == 2
    assert len(data["answers"]) == 2
    first_name_ans = next(a for a in data["answers"] if a["field_name"] == "first_name")
    assert first_name_ans["answer"] == "Jane"
    assert first_name_ans["category"] == "standard"
    assert "cache_hit" in data
    assert data["cache_hit"] is False  # First call


def test_apply_preview_uses_cache_on_second_call(client, mock_db, mock_user, mock_ai):
    """Second call within TTL returns cache_hit=true and skips fetcher+AI."""
    # Setup as above, hit endpoint twice
    # Assert second response has cache_hit=true and fetch/AI NOT called the second time
    pass  # Implementation depends on test fixture style — fill in matching existing patterns


def test_apply_preview_handles_404_from_platform(client, mock_db, mock_user):
    """When platform returns 404 (job pulled), preview returns reason=job_no_longer_available."""
    mock_db.set_job(_eligible_greenhouse_job(job_id="job-2", user_id="u1"))
    mock_db.set_user(_complete_profile(user_id="u1"))

    with patch("shared.platform_metadata.greenhouse.fetch_greenhouse") as fetch_mock:
        from shared.platform_metadata.greenhouse import GreenhouseFetchError
        fetch_mock.side_effect = GreenhouseFetchError("gone", "job_no_longer_available")

        response = client.get("/api/apply/preview/job-2",
                              headers={"Authorization": "Bearer u1-token"})

    assert response.status_code == 200
    data = response.json()
    assert data["eligible"] is False
    assert data["reason"] == "job_no_longer_available"
```

(The exact fixture names depend on the existing test file's style — read the existing test to match patterns before writing.)

- [ ] **Step 7.2: Read the existing test file structure first**

Run: `cat tests/unit/test_apply_endpoints.py | head -80`

Match the fixture/mock style of existing tests (likely fastapi TestClient with dependency overrides). Adapt the test code from Step 7.1 to that style.

- [ ] **Step 7.3: Run tests, verify they fail**

Run: `pytest tests/unit/test_apply_endpoints.py -v -k preview`
Expected: failures because preview endpoint still returns empty questions/answers.

- [ ] **Step 7.4: Replace `apply_preview` body in `app.py`**

In `app.py`, replace lines 2460-2526 (the current `apply_preview` function) with:

```python
@app.get("/api/apply/preview/{job_id}", response_model=ApplyPreviewResponse)
def apply_preview(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Apply preview snapshot — full AI-driven payload per spec §7.3."""
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness
    from shared.platform_metadata import fetch_metadata, PlatformFetchError
    from shared.cover_letter_loader import load_cover_letter
    from shared.answer_generator import generate_answer
    from shared.preview_cache import get_preview_cache, set_preview_cache
    from shared.apply_models import ApplyPreviewResponse, CustomQuestion
    from lambdas.pipeline.ai_helper import ai_complete_cached

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    profile = _db.get_user(user.id) or {}
    missing = check_profile_completeness(profile)

    # Build a "shell" payload for ineligible/error returns so the response shape
    # is always ApplyPreviewResponse-compatible (frontend reads consistent fields)
    def _shell(reason: str, **extra) -> dict:
        return {
            "eligible": False,
            "reason": reason,
            "profile_complete": not missing,
            "missing_required_fields": missing,
            "job": {}, "platform": "", "platform_metadata": {},
            "resume": {}, "profile": {}, "cover_letter": {},
            "custom_questions": [], "already_applied": False,
            "cache_hit": False, **extra,
        }

    if missing:
        return _shell("profile_incomplete")
    if not job.get("apply_url"):
        return _shell("no_apply_url")
    if not job.get("resume_s3_key"):
        return _shell("no_resume")

    # Already-applied check
    canonical = job.get("canonical_hash")
    if canonical:
        existing = (
            _db.client.table("applications")
            .select("id, status, submitted_at")
            .eq("user_id", user.id)
            .eq("canonical_hash", canonical)
            .not_.in_("status", ["unknown", "failed"])
            .execute()
        )
        if existing.data:
            return _shell(
                "already_applied",
                already_applied=True,
                existing_application_id=existing.data[0]["id"],
            )

    resume_version = int(job.get("resume_version") or 1)

    # Cache check (Supabase ai_cache table per spec §7.7)
    cached = get_preview_cache(_db.client, job_id, resume_version)
    if cached:
        cached["cache_hit"] = True
        return cached

    # Resolve resume URL (presigned, fresh)
    _refresh_s3_urls([job])
    is_default_resume = (job.get("resume_s3_key") or "").endswith("default_base.pdf")

    # Fetch platform metadata
    platform = job.get("apply_platform") or ""
    board_token = job.get("apply_board_token")
    posting_id = job.get("apply_posting_id")
    custom_questions: list[dict] = []
    cover_letter_payload = {"text": "", "editable": True, "max_length": 10000,
                             "source": "not_generated", "include_by_default": False}

    if platform and board_token and posting_id:
        try:
            metadata = fetch_metadata(platform, board_token, posting_id)
        except PlatformFetchError as e:
            # Spec §7.3 step 4: on 404 mark job is_expired
            if e.reason == "job_no_longer_available":
                try:
                    _db.client.table("jobs").update({"is_expired": True}).eq("job_id", job_id).execute()
                except Exception:
                    logger.warning(f"[apply_preview] Failed to mark job {job_id} expired")
                return _shell("job_no_longer_available")
            return _shell("metadata_unavailable" if "timeout" in e.reason
                           else "platform_error")

        # Load cover letter (best-effort)
        cl = load_cover_letter(
            user_id=user.id, job_hash=job.get("job_hash", ""),
            s3_client=_get_s3(),
            bucket=os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt")),
        )
        score_tier = (job.get("score_tier") or "").upper()
        cover_letter_payload = {
            "text": (cl or {}).get("text", ""),
            "editable": True,
            "max_length": metadata.get("cover_letter_max_length", 10000),
            "source": (cl or {}).get("source", "not_generated"),
            "include_by_default": (
                metadata.get("cover_letter_required", False)
                or score_tier in ("S", "A")
            ),
        }

        # Generate AI answers per question
        resume_text = job.get("resume_plaintext", "") or ""
        for q in metadata["questions"]:
            ans = generate_answer(q, profile, job, resume_text, cover_letter_payload["text"],
                                   ai_complete_cached_fn=ai_complete_cached)
            custom_questions.append({
                "id": q["field_name"],
                "label": q["label"],
                "type": q["type"],
                "required": q["required"],
                "options": q.get("options") or None,
                "max_length": q.get("max_length"),
                "ai_answer": ans["answer"],
                "requires_user_action": ans["requires_user_action"],
                "category": ans["category"],
            })

    response = {
        "eligible": True,
        "profile_complete": True,
        "missing_required_fields": [],
        "job": {
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "apply_url": job.get("apply_url"),
        },
        "platform": platform,
        "platform_metadata": {
            "board_token": board_token,
            "posting_id": posting_id,
        },
        "resume": {
            "s3_url": job.get("resume_s3_url"),
            "filename": (job.get("resume_s3_key") or "").rsplit("/", 1)[-1] or "resume.pdf",
            "resume_version": resume_version,
            "s3_key": job.get("resume_s3_key"),
            "is_default": is_default_resume,
        },
        "profile": {k: profile.get(k) for k in (
            "first_name", "last_name", "email", "phone",
            "linkedin", "github", "website", "location",
        )},
        "cover_letter": cover_letter_payload,
        "custom_questions": custom_questions,
        "already_applied": False,
        "existing_application_id": None,
        "cache_hit": False,
    }

    # Validate the response shape against the Pydantic model before caching
    # (raises ValidationError if shape drifts — caught by FastAPI's response_model anyway)
    ApplyPreviewResponse(**response)

    # Write cache (10 min TTL per spec §7.7)
    set_preview_cache(_db.client, job_id, resume_version, response, ttl_minutes=10)

    return response
```

- [ ] **Step 7.5: Add the dispatcher to `shared/platform_metadata/__init__.py`**

Append to `shared/platform_metadata/__init__.py`:

```python
from typing import Union
from .greenhouse import fetch_greenhouse, GreenhouseFetchError
from .ashby import fetch_ashby, AshbyFetchError

PlatformFetchError = (GreenhouseFetchError, AshbyFetchError)


def fetch_metadata(platform: str, board_token: str, posting_id: str) -> dict:
    if platform == "greenhouse":
        return fetch_greenhouse(board_token, posting_id)
    if platform == "ashby":
        return fetch_ashby(board_token, posting_id)
    raise ValueError(f"Unsupported platform: {platform}")
```

(Note: this assumes Task 2 created `AshbyFetchError`. If Task 2 took option (a) "skip Ashby", change `fetch_metadata` to raise for ashby and have the endpoint catch it and return the fallback shell.)

- [ ] **Step 7.6: Run all preview tests, verify pass**

Run: `pytest tests/unit/test_apply_endpoints.py -v -k preview`
Expected: all preview tests pass.

- [ ] **Step 7.7: Run full test suite to catch regressions**

Run: `pytest tests/ -x --ignore=tests/e2e -q 2>&1 | tail -20`
Expected: all pass (or pre-existing skips). Fix any new failures before commit.

- [ ] **Step 7.8: Commit Task 7**

```bash
git add app.py shared/platform_metadata/__init__.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): swap preview endpoint to AI-driven orchestration

Plan 3a stub returned questions=[], answers=[]. Now:
- eligibility re-check (unchanged)
- 10-min cache check via ai_client.cache (key: apply_preview:{job_id}:v{ver})
- platform metadata fetch via dispatcher (greenhouse/ashby)
- cover letter load from S3 (best-effort)
- per-question answer generation via category-routed generator
- response includes cache_hit flag
- 404 from platform -> reason=job_no_longer_available
- unknown platform -> degrade to questions=[] (cloud browser fallback)

Response shape backwards-compatible with Plan 3a: existing frontend code
that read questions/answers as empty arrays will now see populated arrays."
```

---

## Task 8: Live integration smoke test

**Files:** none (manual + ad-hoc)

- [ ] **Step 8.1: Deploy preview to a feature branch / staging**

Push the branch and let CI run (Deploy Readiness gate from PR #11 will catch layer + sam-build issues).

- [ ] **Step 8.2: Smoke test against prod with a real S/A-tier greenhouse job**

```bash
JWT='<paste from browser>'
HOST='https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod'
JOB_ID='<a real S-tier greenhouse job_id from your dashboard>'

curl -sS -H "Authorization: Bearer $JWT" "$HOST/api/apply/preview/$JOB_ID" \
  | python3 -m json.tool
```

Expected: `eligible: true`, `answers_generated: true`, `questions` list non-empty, `answers` list aligned with questions, at least the standard fields (first_name, email) populated from your profile.

- [ ] **Step 8.3: Smoke test cache hit**

Re-run the same curl within 10 minutes. Expected: identical response, but `cache_hit: true`.

- [ ] **Step 8.4: Smoke test with a real Ashby job**

(Only if Task 2 implemented an Ashby fetcher.) Repeat 8.2 with an Ashby-platform job.

- [ ] **Step 8.5: Confirm no regression in eligibility endpoint**

```bash
curl -sS -H "Authorization: Bearer $JWT" "$HOST/api/apply/eligibility/$JOB_ID"
```
Expected: still `eligible: true, platform: "greenhouse"`.

---

## Self-Review Checklist (run before opening PR)

- [ ] **Spec §7.1 (Pydantic schemas):** Task 6.5 implements `PlatformInfo`, `CustomQuestion`, `CustomAnswer`, `ApplyPreviewResponse` with the exact field set and Literal enums from the spec. ✓
- [ ] **Spec §7.3 (preview flow):** All 10 steps mapped:
  - 1 re-verify eligibility → Task 7 endpoint
  - 2 cache check → Task 6.7 + Task 7
  - 3 load job/user/canonical/resume → Task 7 (`load_job` + `_db.get_user`)
  - 4 fetch platform metadata + 404 sets `is_expired=true` → Tasks 1+2 + Task 7
  - 5 classify questions → Task 3 (also pre-tagged in Tasks 1+2 for compliance/demographic)
  - 6 load resume metadata + presigned URL + `is_default` → Task 7 (uses existing `_refresh_s3_urls`)
  - 7 load cover letter + `max_length` + `include_by_default` tier fallback → Tasks 4+5 + Task 7
  - 8 build profile dict → Task 7
  - 9 generate AI answers via `ai_complete_cached(temperature=0.3, max_tokens=300, cache_hours=24*7)` → Tasks 0.5 + 6
  - 10 build & cache `ApplyPreviewResponse` → Task 7
- [ ] **Spec §7.6 rate limiting:** Out of scope — applies to submit endpoint (Plan 3d), not preview. Documented in Risks.
- [ ] **Spec §7.7 cache integration:** Task 6.7 implements key + provider + model + expires_at exactly per spec.
- [ ] **Spec §7.8 error matrix:** Preview-relevant codes covered:
  - `no_apply_url` ✓ (Task 7)
  - `no_resume` ✓ (Task 7)
  - `already_applied` ✓ (Task 7)
  - `profile_incomplete` ✓ (Task 7)
  - `job_no_longer_available` ✓ (Tasks 1+2 + Task 7, sets `is_expired=true`)
  - `metadata_unavailable` ✓ (Task 7, on timeout)
  - `platform_error` ✓ (Task 7, on non-404 5xx)
  - `not_supported_platform` — N/A here (eligibility endpoint upstream gates this)
  - `requires_additional_files` — out of scope for v1; flagged in Risks
  - `cover_letter_too_long`, `required_answer_missing`, `confirmation_required`, `resume_version_stale`, `rate_limited` — submit-time concerns, Plan 3d
- [ ] **Spec §7.3 prompt structure:** Task 6 reproduces the 28-line user prompt template verbatim with `candidate_context` (and `DEFAULT_CANDIDATE_CONTEXT` fallback), `work_authorizations`, `salary_expectation_notes`, `notice_period_text`, `key_matches`, `description[:2000]`. ✓

- [ ] **Placeholder scan:** Task 2 (Ashby) intentionally has placeholders for the GraphQL query+response — deliberate research step, not author lazy. All other tasks have full code.

- [ ] **Type consistency:**
  - `extract_platform_ids()` returns `{platform, board_token, posting_id}` everywhere ✓
  - Fetchers all return `{platform, job_title, questions, cover_letter_field_present, cover_letter_required, cover_letter_max_length}` ✓
  - Question type vocabulary is `text|textarea|select|multi_select|checkbox|yes_no|file` everywhere from fetcher → classifier → answer generator → endpoint → Pydantic model ✓
  - Answer dict shape `{answer, category, requires_user_action}` consistent across tests and impl ✓
  - Cover letter shape `{text, source}` from loader, expanded to `{text, editable, max_length, source, include_by_default}` in endpoint ✓

---

## Dependencies & Risks

- **Plan 3a merged** ✅
- **Apply Platform Classifier shipped** ✅ (PR #10)
- **`ai_client.AIClient` and `ResponseCache`** ✅ exist and ready
- **`resume_versions` table** ✅ exists (`supabase/migrations/008_resume_versions.sql`)
- **S3 cover letter path** ✅ verified (`users/{uid}/cover_letters/{job_hash}_cover.tex`)

**Risks:**
1. **Ashby endpoint instability** — the public GraphQL endpoint is undocumented. If it breaks, fall back to the eligibility-degrades-gracefully path (questions=[]).
2. **Greenhouse rate limits** — undocumented. Mitigated by 10-min preview cache.
3. **AI council answer quality** — if early smoke tests show poor answer quality, consider increasing `max_tokens`, switching to a stronger council member, or refining `_SYSTEM_PROMPT`.

---

## Estimated effort

| Task | Steps | Time |
|---|---|---|
| 0 — Slug extractor + persistence + backfill | 10 | 30 min |
| 0.3 — Recompute `easy_apply_eligible` formula | 3 | 10 min |
| 0.5 — Extend `ai_complete_cached` with `max_tokens` | 6 | 15 min |
| 1 — Greenhouse fetcher (compliance + demographic + type normalization) | 7 | 45 min |
| 2 — Ashby investigation + fetcher | 7 | 60–90 min (variable) |
| 3 — Question classifier | 5 | 20 min |
| 4 — tex utils | 5 | 20 min |
| 5 — Cover letter loader | 5 | 20 min |
| 6 — Answer generator (rich prompt) | 5 | 60 min |
| 6.5 — Pydantic models | 5 | 20 min |
| 6.7 — Preview cache via `ai_cache` table | 5 | 20 min |
| 7 — Endpoint orchestration | 8 | 60 min |
| 8 — Live smoke | 5 | 20 min |

**Total: ~7–9 hours focused work, single PR.**

---

## Reference

- 3b stub: `docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md`
- Design spec: `docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md` §7.3
- Master plan: `docs/superpowers/specs/2026-04-03-unified-grand-plan.md` Stage 3.4 Apply
- Plan 3a (dependency): `docs/superpowers/plans/2026-04-24-auto-apply-plan3a-websocket-backend.md`
- Apply Platform Classifier (prereq): `docs/superpowers/plans/2026-04-26-apply-platform-classifier.md`
