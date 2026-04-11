# Auto-Apply Mode 1 — Design Spec

**Date**: 2026-04-11
**Status**: Approved (brainstormed iteratively, 4 rounds of gap analysis)
**Phase**: Layer 4 → 3.4 Apply (Mode 1 of 3)
**Scope**: Easy Apply to Greenhouse + Ashby via their public platform APIs, with strict human-in-the-loop review. Covers Phase 0 (data quality prerequisites) and Mode 1 end-to-end in a single PR.

---

## 1. Problem

You have ~1300 S+A tier jobs in the pipeline pool but applying to each one manually takes 5–15 minutes. Greenhouse (job-boards.greenhouse.io) and Ashby (jobs.ashbyhq.com) both expose public APIs for application submission. We can compose a pre-filled application payload from existing data (tailored resume, generated cover letter, user profile, AI-generated custom-question answers) and submit through those APIs after the user reviews the payload.

Mode 2 (remote Fargate browser for LinkedIn/Workday) and Mode 3 (assisted copy-paste) are out of scope for this spec but the shared pieces (`applications` table, AI answer generation, latex-to-text) are designed so they extend cleanly.

---

## 2. Key design principles

1. **Strict human-in-the-loop.** Submit button is disabled until the user reviews the full payload, explicitly checks any confirmation questions, and clicks "Submit". No auto-submit, ever.
2. **One PR covers Phase 0 + Mode 1.** Phase 0 cleanups (non-job filter, profile data, URL parsing columns) ship alongside Mode 1 because the filter and profile data are prereqs for a working Mode 1.
3. **Shared components are pre-factored for Mode 2.** `applications` table, AI answer generation, platform URL parsers, and latex-to-text helpers are all designed to be consumed by Mode 2 later without rewrites.
4. **Idempotent per canonical job.** A canonical job (cross-source-deduped) can only be applied to once; duplicates across LinkedIn/Greenhouse/etc. mirror the Applied state.
5. **All data flows through SAM + QA suite.** No direct Lambda CLI pushes. Migration + code changes go through `sam build && sam deploy` after `pytest` passes.

---

## 3. Architecture overview

```
┌─────────────────────────────────────────────────────────┐
│  React Frontend (Netlify)                                │
│                                                          │
│  ┌─ Dashboard / JobWorkspace ──────────────────────────┐ │
│  │ Job card shows ⚡ Easy Apply badge (eligibility ✓)  │ │
│  │ Click → EasyApplyModal opens                        │ │
│  └─────────────────────┬───────────────────────────────┘ │
│                        │                                 │
│  ┌─ EasyApplyModal ────┴───────────────────────────────┐ │
│  │ Fetches preview, shows resume/CL/profile/Qs,        │ │
│  │ validates user-action checkboxes, submits on click  │ │
│  └─────────────────────┬───────────────────────────────┘ │
└────────────────────────┼─────────────────────────────────┘
                         │ REST (Bearer JWT)
┌────────────────────────┴─────────────────────────────────┐
│  FastAPI Backend (Lambda via SAM, JobHuntApi container)  │
│                                                          │
│  GET  /api/apply/eligibility/{job_id}                    │
│  GET  /api/apply/preview/{job_id}                        │
│  POST /api/apply/submit/{job_id}                         │
│                                                          │
│  ├─ utils/platform_parsers.py  (URL → PlatformInfo)      │
│  ├─ utils/latex_to_text.py     (CL .tex → plaintext)     │
│  ├─ utils/platform_clients.py  (Greenhouse + Ashby API)  │
│  └─ _generate_custom_answers() (AI fills custom Qs)      │
└────────────────────────┬─────────────────────────────────┘
                         │
           ┌─────────────┴───────────────┐
           ▼                             ▼
      ┌────────┐                    ┌──────────┐
      │   S3   │                    │ Supabase │
      │ resume │                    │ users    │
      │ PDFs + │                    │ jobs     │
      │ CL .tex│                    │ applications (new)
      └────────┘                    │ application_timeline
                                    │ ai_cache (reused for preview cache)
                                    └──────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │  External platform APIs        │
        │  boards-api.greenhouse.io      │
        │  api.ashbyhq.com/posting-api   │
        └────────────────────────────────┘
```

---

## 4. Phase 0 — Pre-Auto-Apply cleanup

Phase 0 is a dependency for Mode 1. It ships in the same PR but is independently valuable (cost savings from not scoring non-jobs, cleaner data quality).

### 4.1 Non-job + entry-level title filters

Add to `lambdas/pipeline/score_batch.py::should_skip_scoring()`. Runs **before** the AI scoring call, returns a skip reason and costs zero AI calls.

```python
# Events, talent pools, newsletters — not actual jobs
_NON_JOB_TITLE_PATTERNS = (
    " summit", " conference", " fair", " webinar", " meetup",
    " bootcamp", " rsvp", " register", " networking event",
    "talent community", "talent pool", "talent network",
    "general interest", "general application",
    "join our team pipeline", "stay in touch",
    "newsletter", "subscribe",
    "2026 -", "2025 -",  # MongoDB's event-prefix pattern
)

# Entry-level only — user has 3+ years experience
_ENTRY_LEVEL_ONLY_PATTERNS = (
    " intern ", "internship",
    "new grad", "new graduate", "graduate program",
    "entry level", "entry-level",
    "apprentice", "trainee",
    "early career",
)
```

In `should_skip_scoring()`, after the existing checks, add:

```python
title_lc = (job.get("title") or "").lower()
if any(p in title_lc for p in _NON_JOB_TITLE_PATTERNS):
    return "not_a_job"
if any(p in title_lc for p in _ENTRY_LEVEL_ONLY_PATTERNS):
    return "entry_level_only"
```

Titles checked with a leading/trailing space (e.g., `" intern "`) to avoid false positives like "International" or "Printer".

### 4.2 Backfill existing non-jobs

`scripts/phase0_cleanup.py` (one-off, idempotent):

1. Iterate all non-expired `jobs` rows
2. Apply the same filter logic to each title
3. For matches: set `is_expired = true`, `score_status = 'not_a_job'` or `'entry_level_only'`
4. Print summary: `N non-jobs marked expired, M entry-level marked expired`

Run once after migration. Never modifies data that already passes the filter.

### 4.3 User profile data + new columns

Single migration `supabase/migrations/20260411_auto_apply_setup.sql` handles both the profile defaults and the new columns. See §6 for the complete SQL.

---

## 5. Data model

### 5.1 New table: `applications`

```sql
CREATE TABLE applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  job_hash TEXT NOT NULL,
  canonical_hash TEXT,  -- denormalized from jobs.canonical_hash at insert time

  -- Submission method
  submission_method TEXT NOT NULL CHECK (submission_method IN (
    'greenhouse_api', 'ashby_api', 'remote_browser', 'assisted_manual'
  )),
  platform TEXT NOT NULL,  -- 'greenhouse' | 'ashby' | future: 'linkedin' | 'workday' | 'custom'
  posting_id TEXT,         -- extracted from apply_url
  board_token TEXT,        -- Greenhouse board slug / Ashby company slug

  -- What we sent
  resume_s3_key TEXT NOT NULL,
  resume_version INT NOT NULL DEFAULT 1,
  resume_is_default BOOLEAN NOT NULL DEFAULT FALSE,  -- true if the default_base.pdf was used
  cover_letter_text TEXT,                             -- null if "include cover letter" was off
  include_cover_letter BOOLEAN NOT NULL DEFAULT FALSE,
  answers JSONB NOT NULL DEFAULT '[]',                -- list of {question_id, value, category}
  profile_snapshot JSONB NOT NULL DEFAULT '{}',       -- name/email/phone/... frozen at submit time

  -- Status progression
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN (
    'submitted', 'unknown', 'confirmed', 'viewed',
    'rejected', 'interview', 'offer', 'ghosted', 'failed'
  )),
  platform_response JSONB,                            -- raw API response (for debugging)
  confirmation_screenshot_s3_key TEXT,                -- Mode 2 only
  dry_run BOOLEAN NOT NULL DEFAULT FALSE,

  -- Timestamps
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  response_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One active application per canonical job — retries allowed only for status='unknown' or 'failed'.
-- Dry-run rows are excluded so a dry run doesn't block a real submission.
CREATE UNIQUE INDEX idx_applications_one_active_per_canonical
  ON applications(user_id, canonical_hash)
  WHERE status NOT IN ('unknown', 'failed')
    AND canonical_hash IS NOT NULL
    AND dry_run = false;

-- Fallback uniqueness for rows without canonical_hash
CREATE UNIQUE INDEX idx_applications_one_active_per_job
  ON applications(user_id, job_id)
  WHERE status NOT IN ('unknown', 'failed')
    AND dry_run = false;

CREATE INDEX idx_applications_user_status ON applications(user_id, status);
CREATE INDEX idx_applications_job ON applications(job_id);
CREATE INDEX idx_applications_submitted ON applications(submitted_at DESC);

-- RLS (full SQL with DROP-first pattern in §6 — this is the intent)
ALTER TABLE applications ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own applications" ON applications FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users insert own applications" ON applications FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users update own applications" ON applications FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Service role full access applications" ON applications FOR ALL USING (auth.role() = 'service_role');

-- updated_at trigger
CREATE TRIGGER trg_applications_updated_at
  BEFORE UPDATE ON applications
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

### 5.1a New-user JIT provisioning

`db_client.create_user()` is called on first profile fetch for new Supabase-auth users. It currently creates `{id, email}`. Update to also set:

- `first_name = email.split('@')[0]` (placeholder from email local-part)
- `last_name = ''`
- `default_referral_source = 'LinkedIn'` (from the new column default)
- `notice_period_text = 'Available in 2 weeks'` (from the new column default)

This ensures new users don't hit `profile_incomplete` on day 1 for Easy Apply. They can override the placeholder first_name in Settings later.

### 5.2 New columns on `users`

```sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS first_name TEXT,
  ADD COLUMN IF NOT EXISTS last_name TEXT,
  ADD COLUMN IF NOT EXISTS default_referral_source TEXT DEFAULT 'LinkedIn',
  ADD COLUMN IF NOT EXISTS salary_expectation_notes TEXT,
  ADD COLUMN IF NOT EXISTS notice_period_text TEXT DEFAULT 'Available in 2 weeks';

-- Backfill first/last from existing name
UPDATE users SET
  first_name = split_part(name, ' ', 1),
  last_name  = CASE
    WHEN position(' ' in name) > 0
      THEN substring(name from position(' ' in name) + 1)
    ELSE ''
  END
WHERE first_name IS NULL AND name IS NOT NULL;

-- Populate work authorization for the single existing user
UPDATE users SET
  location = 'Dublin, Ireland',
  visa_status = 'Stamp 1G',
  work_authorizations = '{
    "IE": "authorized",
    "UK": "requires_visa",
    "US": "requires_sponsorship",
    "EU": "requires_visa"
  }'::jsonb
WHERE id = '7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39'
  AND (visa_status IS NULL OR visa_status = '');
```

### 5.3 New columns on `jobs`

```sql
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS apply_platform TEXT,
  ADD COLUMN IF NOT EXISTS apply_board_token TEXT,
  ADD COLUMN IF NOT EXISTS apply_posting_id TEXT,
  ADD COLUMN IF NOT EXISTS easy_apply_eligible BOOLEAN
    GENERATED ALWAYS AS (apply_platform IS NOT NULL) STORED;

CREATE INDEX IF NOT EXISTS idx_jobs_easy_apply_eligible
  ON jobs(user_id, score_tier, easy_apply_eligible)
  WHERE is_expired = false;
```

### 5.4 Application status mirroring

When an `applications` row is inserted with `status='submitted'` or `status='confirmed'`, mirror to `jobs.application_status`:

```sql
-- All jobs sharing the same canonical_hash get mirrored status
UPDATE jobs SET application_status = 'Applied'
WHERE user_id = $1
  AND canonical_hash = $2;
```

This fixes the "I applied on Greenhouse, LinkedIn still shows not applied" duplication.

---

## 6. Migration file

`supabase/migrations/20260411_auto_apply_setup.sql`:

```sql
-- ========== Phase 0 + Mode 1 — Auto-Apply setup ==========

BEGIN;

-- 1. set_updated_at() trigger helper (skip if exists)
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. users: new columns
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS first_name TEXT,
  ADD COLUMN IF NOT EXISTS last_name TEXT,
  ADD COLUMN IF NOT EXISTS default_referral_source TEXT DEFAULT 'LinkedIn',
  ADD COLUMN IF NOT EXISTS salary_expectation_notes TEXT,
  ADD COLUMN IF NOT EXISTS notice_period_text TEXT DEFAULT 'Available in 2 weeks';

-- 3. Backfill first/last_name from existing name
UPDATE users SET
  first_name = split_part(name, ' ', 1),
  last_name  = CASE
    WHEN position(' ' in name) > 0
      THEN substring(name from position(' ' in name) + 1)
    ELSE ''
  END
WHERE first_name IS NULL AND name IS NOT NULL;

-- 4. Populate profile defaults for the existing single user
UPDATE users SET
  location = COALESCE(NULLIF(location, ''), 'Dublin, Ireland'),
  visa_status = COALESCE(NULLIF(visa_status, ''), 'Stamp 1G'),
  work_authorizations = CASE
    WHEN work_authorizations IS NULL OR work_authorizations = '{}'::jsonb
      THEN '{
        "IE": "authorized",
        "UK": "requires_visa",
        "US": "requires_sponsorship",
        "EU": "requires_visa"
      }'::jsonb
    ELSE work_authorizations
  END
WHERE id = '7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39';

-- 5. jobs: new apply_* columns + generated eligibility flag
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS apply_platform TEXT,
  ADD COLUMN IF NOT EXISTS apply_board_token TEXT,
  ADD COLUMN IF NOT EXISTS apply_posting_id TEXT;

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS easy_apply_eligible BOOLEAN
  GENERATED ALWAYS AS (apply_platform IS NOT NULL) STORED;

CREATE INDEX IF NOT EXISTS idx_jobs_easy_apply_eligible
  ON jobs(user_id, score_tier, easy_apply_eligible)
  WHERE is_expired = false;

-- 6. applications table
CREATE TABLE IF NOT EXISTS applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  job_hash TEXT NOT NULL,
  canonical_hash TEXT,
  submission_method TEXT NOT NULL CHECK (submission_method IN (
    'greenhouse_api', 'ashby_api', 'remote_browser', 'assisted_manual'
  )),
  platform TEXT NOT NULL,
  posting_id TEXT,
  board_token TEXT,
  resume_s3_key TEXT NOT NULL,
  resume_version INT NOT NULL DEFAULT 1,
  resume_is_default BOOLEAN NOT NULL DEFAULT FALSE,
  cover_letter_text TEXT,
  include_cover_letter BOOLEAN NOT NULL DEFAULT FALSE,
  answers JSONB NOT NULL DEFAULT '[]',
  profile_snapshot JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN (
    'submitted', 'unknown', 'confirmed', 'viewed',
    'rejected', 'interview', 'offer', 'ghosted', 'failed'
  )),
  platform_response JSONB,
  confirmation_screenshot_s3_key TEXT,
  dry_run BOOLEAN NOT NULL DEFAULT FALSE,
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  response_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_one_active_per_canonical
  ON applications(user_id, canonical_hash)
  WHERE status NOT IN ('unknown', 'failed')
    AND canonical_hash IS NOT NULL
    AND dry_run = false;

CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_one_active_per_job
  ON applications(user_id, job_id)
  WHERE status NOT IN ('unknown', 'failed')
    AND dry_run = false;

CREATE INDEX IF NOT EXISTS idx_applications_user_status ON applications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_applications_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_submitted ON applications(submitted_at DESC);

ALTER TABLE applications ENABLE ROW LEVEL SECURITY;

-- Postgres 15 doesn't support "CREATE POLICY IF NOT EXISTS" — use DROP + CREATE
DROP POLICY IF EXISTS "Users read own applications" ON applications;
CREATE POLICY "Users read own applications" ON applications
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users insert own applications" ON applications;
CREATE POLICY "Users insert own applications" ON applications
  FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users update own applications" ON applications;
CREATE POLICY "Users update own applications" ON applications
  FOR UPDATE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Service role full access applications" ON applications;
CREATE POLICY "Service role full access applications" ON applications
  FOR ALL USING (auth.role() = 'service_role');

DROP TRIGGER IF EXISTS trg_applications_updated_at ON applications;
CREATE TRIGGER trg_applications_updated_at
  BEFORE UPDATE ON applications
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
```

After the migration runs, execute `scripts/phase0_cleanup.py` to:

1. Backfill `jobs.apply_platform/board_token/posting_id` from existing `apply_url` via the new `parse_apply_url()` helper
2. Apply the non-job + entry-level filters to existing data, marking matches as expired

---

## 7. Backend — endpoints and helpers

### 7.1 Pydantic schemas

```python
class PlatformInfo(BaseModel):
    """Parsed from an apply URL. None if URL is not a supported Easy Apply platform."""
    platform: Literal["greenhouse", "ashby"]
    board_token: str         # Greenhouse: board slug; Ashby: company slug
    posting_id: str          # Greenhouse: numeric id; Ashby: UUID

class CustomQuestion(BaseModel):
    id: str                  # platform question id (string form)
    label: str
    type: Literal["text", "textarea", "select", "multi_select",
                  "checkbox", "yes_no", "file"]
    required: bool
    options: list[str] | None = None           # for select / multi_select
    max_length: int | None = None              # for text / textarea
    ai_answer: str | bool | None = None        # pre-filled by AI (or false for confirmations)
    requires_user_action: bool = False         # true for confirmation checkboxes
    category: Literal["custom", "eeo", "confirmation",
                      "marketing", "referral"] = "custom"

class ApplyPreviewResponse(BaseModel):
    eligible: bool
    reason: str | None = None
    profile_complete: bool
    missing_required_fields: list[str] = []
    job: dict                # {title, company, location, apply_url}
    platform: str            # "greenhouse" | "ashby"
    platform_metadata: dict  # {board_token, posting_id, application_form_id?}
    resume: dict             # {s3_url, filename, resume_version, s3_key, is_default}
    profile: dict            # {first_name, last_name, email, phone, linkedin, github, location}
    cover_letter: dict       # {text, editable, max_length, source, include_by_default}
    custom_questions: list[CustomQuestion]
    already_applied: bool
    existing_application_id: str | None = None
    cache_hit: bool

class CustomAnswer(BaseModel):
    question_id: str
    value: str | bool | None
    category: str

class SubmitApplicationRequest(BaseModel):
    resume_version: int                     # optimistic lock — must match preview
    include_cover_letter: bool
    cover_letter_text: str | None = None    # required if include_cover_letter
    custom_answers: list[CustomAnswer]      # must include every question from preview
    dry_run: bool = False

class SubmitApplicationResponse(BaseModel):
    application_id: str
    status: str                # 'submitted' | 'unknown' | 'failed'
    submitted_at: str
    platform_response_summary: str | None  # short human-readable line, e.g.,
                                           # "Greenhouse: Application received"
                                           # Full raw response lives in
                                           # applications.platform_response JSONB
    dry_run: bool
```

**Note on field names verified via fixtures:** The `GreenhouseClient.submit_application()` field names (`first_name`, `last_name`, `email`, `phone`, `resume`, `cover_letter_text`, `question_{id}`) are based on Greenhouse's public API docs. **Verify against the fetched fixture in F3 before shipping.** If Greenhouse uses different names (e.g., `cover_letter` instead of `cover_letter_text`), update the client accordingly. Implementation checklist: run `scripts/fetch_platform_fixtures.py`, inspect the returned JSON, confirm field names match the client.

### 7.2 Endpoint: `GET /api/apply/eligibility/{job_id}`

**Purpose:** On-demand detailed eligibility check for a specific job. Called when the user is about to interact with Easy Apply (badge click, modal open, Apply tab load). **NOT** called for every dashboard card — the dashboard badge visibility is driven by the `jobs.easy_apply_eligible` column returned in the batch `/api/dashboard/jobs` response.

The endpoint also returns extra context (`already_applied`, `application_id`, `missing_required_fields`) that the modal uses to decide which view to render.

**Logic:**

```python
@app.get("/api/apply/eligibility/{job_id}")
def check_eligibility(job_id: str, user: AuthUser = Depends(get_current_user)):
    job = load_job(job_id, user.id)  # RLS enforced
    if not job:
        raise HTTPException(404)

    if not job.get("apply_platform"):
        return {"eligible": False, "reason": "not_supported_platform"}
    if not job.get("resume_s3_key"):
        return {"eligible": False, "reason": "no_resume"}

    # Check if already applied (by canonical_hash)
    existing = _db.client.table("applications").select("id, status, submitted_at").eq(
        "user_id", user.id
    ).eq(
        "canonical_hash", job.get("canonical_hash") or ""
    ).not_.in_("status", ["unknown", "failed"]).execute()

    if existing.data:
        return {
            "eligible": False,
            "reason": "already_applied",
            "application_id": existing.data[0]["id"],
            "applied_at": existing.data[0]["submitted_at"],
        }

    # Profile completeness check
    profile = _db.get_user(user.id)
    missing = _check_profile_completeness(profile)
    if missing:
        return {
            "eligible": False,
            "reason": "profile_incomplete",
            "missing_required_fields": missing,
        }

    return {
        "eligible": True,
        "platform": job["apply_platform"],
        "board_token": job.get("apply_board_token"),
        "posting_id": job.get("apply_posting_id"),
    }
```

**`_check_profile_completeness(profile)` required fields:**

`first_name`, `last_name`, `email`, `phone`, `linkedin`, `visa_status`, `work_authorizations` (non-empty dict), `default_referral_source`, `notice_period_text`.

**Optional:** `github`, `location`, `salary_expectation_notes`, `website`.

### 7.3 Endpoint: `GET /api/apply/preview/{job_id}`

**Purpose:** Full preview payload the modal renders. Cached in `ai_cache` for 10 minutes, keyed by `f"apply_preview:{job_id}:{resume_version}"`.

**Flow:**

1. **Re-verify eligibility** (cheap) — if ineligible, return `{eligible: false, reason, ...}` without building the payload.
2. **Check cache** — if `ai_cache` has a fresh entry, return it with `cache_hit=true`.
3. **Load job, user, canonical_hash, resume_s3_key, resume_version**.
4. **Fetch platform metadata** via `GreenhouseClient.fetch_job_metadata()` or `AshbyClient.fetch_job_posting()`.
   - Greenhouse: `GET boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true`, merge `questions[]` + `compliance[]`, read `cover_letter_required`.
   - Ashby: `GET api.ashbyhq.com/posting-api/job-posting/{uuid}`, return `(posting, application_form_id)`.
   - On 404: mark `jobs.is_expired = true`, return 404 with `reason=job_no_longer_available`.
   - Set `follow_redirects=False` to prevent redirect-based URL spoofing.
5. **Classify each question** into `custom` / `eeo` / `confirmation` / `marketing` / `referral` using label regex:
   - EEO: `(gender|ethnicity|race|veteran|disability|self-identify|self identify)`
   - Confirmation: `(confirm|certify|accurate|true.*information|understand|acknowledge)`
   - Marketing: `(marketing|newsletter|subscribe|promotional|updates about)`
   - Referral: `(how.*hear|referral source|source of awareness)`
   - Else: `custom`
6. **Load resume metadata** — `resume_s3_url` (fresh presigned via `_refresh_s3_urls`), filename, `resume_version`, `is_default` (true if `resume_s3_key` points to `default_base.pdf`).
7. **Load cover letter** — try `users/{uid}/cover_letters/{job_hash}.tex` from S3, pass through `tex_to_plaintext()`. If missing, use a hardcoded default CL template from config. If both fail, set `cover_letter.text = ''` and `cover_letter.source = 'not_generated'`.
   - **max_length** — source priority: (1) platform metadata field if present, (2) per-platform hardcoded default:
     - Greenhouse: **10000** chars
     - Ashby: **5000** chars
   - **include_by_default** — source priority: (1) platform metadata `cover_letter_required` (if true → always on), (2) tier-based fallback (S/A → on, B/C → off).
8. **Build profile dict** — read from `users` table, snapshot the fields for display.
9. **Generate AI answers** via `_generate_custom_answers()`:
   - For `category='confirmation'` questions: skip AI, set `ai_answer=False`, `requires_user_action=True`.
   - For `category='eeo'` questions: skip AI, set `ai_answer="Decline to self-identify"` (or equivalent from options).
   - For `category='marketing'` questions: skip AI, set `ai_answer=False`.
   - For `category='referral'` questions: if options are present, match `user.default_referral_source` to the closest option. If not in options, set to the dropdown's first entry that includes "LinkedIn" or similar.
   - For `category='custom'` questions: AI call using `ai_complete_cached`:
     ```python
     ai_complete_cached(
         system_prompt=SYSTEM_PROMPT,
         user_prompt=USER_PROMPT.format(...),
         temperature=0.3,              # deterministic-ish, allows slight creativity
         max_tokens=300,               # answers are short
         providers=["qwen", "nvidia", "groq"],  # existing council failover
         cache_hours=24 * 7,           # same job+resume → same answers for 7 days
     )
     ```
   - **Post-process every AI answer against the question schema:**
     ```python
     if question.type in ("select", "multi_select") and question.options:
         if ai_answer not in question.options:
             # Fuzzy match (case-insensitive substring) or fall back to first option
             match = next((opt for opt in question.options
                          if ai_answer.lower() in opt.lower() or opt.lower() in ai_answer.lower()),
                          None)
             ai_answer = match or question.options[0]
             logger.warning(f"AI hallucinated option '{ai_answer}' not in {question.options}")
     if question.type == "yes_no":
         if ai_answer.lower() not in ("yes", "no"):
             ai_answer = "Yes"  # safer default for most application Qs
     ```
10. **Build and return `ApplyPreviewResponse`**, write to cache.

**AI answer generation prompt:**

The prompt is built from the user's stored profile data, not hardcoded facts. `user.candidate_context` is a free-text field in the `users` table the user can populate with their bio, years of experience, certifications, and skills — it becomes the primary source. A hardcoded fallback applies only if `candidate_context` is empty.

```
You are filling out a job application for {user.first_name} {user.last_name}, applying to {job.title} at {job.company}.

CANDIDATE PROFILE:
{user.candidate_context or DEFAULT_CANDIDATE_CONTEXT}

CONTACT:
- LinkedIn: {user.linkedin}
- GitHub: {user.github}
- Website: {user.website}
- Location: {user.location}
- Visa status: {user.visa_status}

PREFERENCES:
- Salary expectations: {user.salary_expectation_notes or "Open to discussion, targeting competitive market rate"}
- Notice period: {user.notice_period_text}

JOB CONTEXT:
- Role: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description: {job.description[:2000]}
- Key matches: {job.key_matches}

WORK AUTHORIZATION MATCHING:
- If the question asks about work authorization in a specific country, use user.work_authorizations:
  {user.work_authorizations}
- For "Remote - Europe" or "EU" locations, default to Ireland ("IE").
- For ambiguous locations, default to Ireland.

QUESTION: {question.label}
TYPE: {question.type}
{OPTIONS: {question.options} (if present)}
REQUIRED: {question.required}

Answer the question concisely, truthfully, and in a way that presents the candidate positively. Reference specific things from the job description when relevant. If the question is a dropdown/select, you MUST pick one of the provided options verbatim.

Return ONLY the answer text, no explanation.
```

**`DEFAULT_CANDIDATE_CONTEXT` fallback** (used only if `user.candidate_context` is empty):

```
3+ years full-stack software engineering experience. MSc in Cloud Computing (ATU).
AWS Solutions Architect Professional certified. Strong in Python (FastAPI, Flask,
Django), TypeScript/React, AWS (ECS/Fargate, Lambda, RDS, S3, API Gateway),
CI/CD, Docker, Kubernetes, Terraform. Track record of reducing MTTR 35%,
cutting release lead time 85%, maintaining 99.9% uptime.
```

The user should populate `users.candidate_context` in Settings for better personalization, but the default makes Easy Apply work on day 1.

### 7.4 Endpoint: `POST /api/apply/submit/{job_id}`

**Execution order** (critical):

1. Auth (JWT via Depends)
2. Load job, verify RLS
3. Idempotency check: query `applications` for existing active rows that would block a real submission — i.e., `status NOT IN ('unknown', 'failed') AND dry_run = false`. Priority: (a) by `canonical_hash` if the job has one, (b) fall back to `(user_id, job_id)` if `canonical_hash` is NULL. Both cases are enforced by the two partial unique indexes in §5.1. **Dry-run rows do NOT block real submissions, and real rows do NOT block dry runs** — this lets users dry-run repeatedly and then submit for real. Return 409 `already_applied` on match.
4. Rate limit check: count `applications` rows inserted in last 60 min, reject if ≥ 20
5. Profile completeness re-check
6. Re-verify eligibility
7. Resume version optimistic lock — if `submitted resume_version != current resume_version`, return 409 `resume_version_stale`
8. Validate submit payload:
   - Every question from preview has an answer (match by question_id)
   - Every `category='confirmation'` answer has `value == true`
   - Cover letter length ≤ platform `max_length` (if `include_cover_letter`)
   - Required fields non-empty
9. S3 download — `s3.get_object(...)["Body"].read()` → `pdf_bytes` (in-memory, Lambda)
10. Build platform-specific payload via `GreenhouseClient` or `AshbyClient`
11. Platform POST with `httpx.post(..., timeout=20, follow_redirects=False)`
12. Handle response:
    - 2xx → write `applications` row with `status='submitted'` + full `platform_response`
    - 404/410 → mark `jobs.is_expired=true`, return 404 with `reason=job_no_longer_available`
    - 4xx/5xx → return 502 `platform_error` with detail, no DB write
    - Timeout / network error → write `applications` row with `status='unknown'`, return 202 with warning
13. Mirror `jobs.application_status = 'Applied'` to all rows sharing `canonical_hash` (forward-looking: see §9.3 for the new-job insert path)
14. Insert **one** `application_timeline` event for the specific `job_id` the user submitted through — siblings mirrored in step 13 do NOT get duplicate timeline entries. Row: `{user_id, job_id, status: 'Applied', notes: 'Easy Apply via {platform}'}`
15. Return `SubmitApplicationResponse`

If `dry_run=True`, steps 1-10 run normally, then **skip step 11** (no actual POST), write the intended payload to `applications` with `dry_run=true` and `status='submitted'`, return success.

### 7.5 Helpers

**File locations — `utils/` exists in two places:**

| File | Location | Consumer |
|------|----------|----------|
| `platform_parsers.py` | `utils/` (root) AND `lambdas/pipeline/utils/` | `app.py` (FastAPI) + `lambdas/pipeline/score_batch.py` — keep them identical, either hand-sync or symlink |
| `latex_to_text.py` | `utils/` (root) only | `app.py` preview endpoint |
| `platform_clients.py` | `utils/` (root) only | `app.py` preview + submit endpoints |

The duplication for `platform_parsers.py` is because the two Lambdas package their sibling directories independently. A unit test (`tests/unit/test_utils_sync.py`) byte-compares the two copies and fails CI if they drift.

```python
# tests/unit/test_utils_sync.py
import hashlib
from pathlib import Path

def test_platform_parsers_sync():
    root = Path(__file__).parent.parent.parent
    a = (root / "utils" / "platform_parsers.py").read_bytes()
    b = (root / "lambdas" / "pipeline" / "utils" / "platform_parsers.py").read_bytes()
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), (
        "utils/platform_parsers.py out of sync between root and lambdas/pipeline. "
        "Update both copies."
    )
```

**`utils/platform_parsers.py`:**

```python
def parse_apply_url(url: str) -> PlatformInfo | None:
    """
    Recognized patterns:
      - https://job-boards.greenhouse.io/{board}/jobs/{numeric_id}
      - https://jobs.ashbyhq.com/{company}/{uuid}
    Returns None for custom-hosted boards (stripe.com/jobs/..., mongodb.com/careers/...)
    which are not API-submittable.
    """
    if not url:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return None

    if p.netloc == "job-boards.greenhouse.io":
        parts = p.path.strip("/").split("/")
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            return PlatformInfo(platform="greenhouse", board_token=parts[0], posting_id=parts[2])

    if p.netloc == "jobs.ashbyhq.com":
        parts = p.path.strip("/").split("/")
        if len(parts) >= 2:
            uuid_str = parts[1]
            # basic UUID validation (8-4-4-4-12 hex)
            if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", uuid_str):
                return PlatformInfo(platform="ashby", board_token=parts[0], posting_id=uuid_str)

    return None


def resolve_work_auth_country(location: str, work_authorizations: dict) -> str:
    """
    Given a job location and the user's work_authorizations dict,
    return the country code whose auth rules apply.

    Priority:
      1. Exact country match in location string
      2. "Remote" + region (Europe, EU, APAC) → default to Ireland
      3. Ambiguous → Ireland (default)
    """
    loc_lc = (location or "").lower()
    country_map = {"united states": "US", "usa": "US", "u.s.": "US",
                   "united kingdom": "UK", "uk": "UK",
                   "ireland": "IE", "dublin": "IE",
                   "germany": "DE", "france": "FR"}
    for kw, code in country_map.items():
        if kw in loc_lc:
            return code
    return "IE"  # default
```

**`utils/latex_to_text.py`:**

```python
# Macros to strip, with their replacements
_MACRO_REPLACEMENTS = [
    (r"\\textbf\{([^}]*)\}", r"\1"),                  # \textbf{x} → x
    (r"\\textit\{([^}]*)\}", r"\1"),                  # \textit{x} → x
    (r"\\emph\{([^}]*)\}", r"\1"),                    # \emph{x} → x
    (r"\\section\*?\{([^}]*)\}", r"\1\n"),            # \section*{x} → x\n
    (r"\\opening\{([^}]*)\}", r"\1\n\n"),             # \opening{Dear...} → Dear...
    (r"\\closing\{([^}]*)\}", r"\1,"),                # \closing{Best} → Best,
    (r"\\signature\{([^}]*)\}", r""),                 # drop \signature{}
    (r"\\address\{[^}]*\}", r""),                     # drop \address{}
    (r"\\begin\{letter\}\{[^}]*\}", r""),             # drop letter envelope
    (r"\\end\{letter\}", r""),
    (r"\\begin\{document\}", r""),
    (r"\\end\{document\}", r""),
    (r"\\documentclass\[[^]]*\]\{[^}]*\}", r""),
    (r"\\usepackage(?:\[[^]]*\])?\{[^}]*\}", r""),
    (r"\\newcommand.*", r""),                         # drop all \newcommand lines
    (r"\\\\(?!\{)", r"\n"),                           # \\ → newline (line break)
    (r"\\&", r"&"),                                   # \& → &
    (r"\\%", r"%"),                                   # \% → %
    (r"\\#", r"#"),                                   # \# → #
    (r"\\_", r"_"),                                   # \_ → _
    (r"\\\$", r"$"),                                  # \$ → $
    (r"%.*$", r""),                                   # LaTeX comments
]

def tex_to_plaintext(tex: str) -> str:
    """Convert a cover letter .tex source to clean plaintext."""
    out = tex
    for pattern, replacement in _MACRO_REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.MULTILINE)
    # Collapse multiple blank lines, trim whitespace
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out
```

**`utils/platform_clients.py`:**

```python
class GreenhouseClient:
    BASE = "https://boards-api.greenhouse.io/v1"

    def fetch_job_metadata(self, board_token: str, posting_id: str) -> dict:
        url = f"{self.BASE}/boards/{board_token}/jobs/{posting_id}?questions=true"
        resp = httpx.get(url, timeout=15, follow_redirects=False)
        resp.raise_for_status()
        data = resp.json()
        questions = []
        for q in data.get("questions") or []:
            questions.append({**q, "category": "custom"})
        for q in data.get("compliance") or []:
            questions.append({**q, "category": "eeo"})
        return {
            "job": {
                "title": data.get("title"),
                "location": (data.get("location") or {}).get("name"),
                "cover_letter_required": data.get("cover_letter_required", False),
            },
            "questions": questions,
        }

    def submit_application(
        self,
        board_token: str,
        posting_id: str,
        *,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        resume_pdf: bytes,
        cover_letter_text: str | None,
        answers: dict,          # {question_id: value}
    ) -> httpx.Response:
        url = f"{self.BASE}/boards/{board_token}/jobs/{posting_id}"
        files = {"resume": ("resume.pdf", resume_pdf, "application/pdf")}
        data = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
        }
        if cover_letter_text:
            data["cover_letter_text"] = cover_letter_text
        for qid, value in answers.items():
            data[f"question_{qid}"] = value
        return httpx.post(url, data=data, files=files, timeout=20, follow_redirects=False)


class AshbyClient:
    BASE = "https://api.ashbyhq.com/posting-api"

    def fetch_job_posting(self, posting_id: str) -> tuple[dict, str]:
        """Returns (posting_data, application_form_id)."""
        url = f"{self.BASE}/job-posting/{posting_id}"
        resp = httpx.get(url, timeout=15, follow_redirects=False)
        resp.raise_for_status()
        data = resp.json().get("results", {})
        return data, data.get("applicationForm", {}).get("id")

    def upload_file(self, pdf_bytes: bytes, filename: str) -> str:
        """Upload a file, return the fileHandle."""
        url = f"{self.BASE}/upload-file"
        files = {"file": (filename, pdf_bytes, "application/pdf")}
        resp = httpx.post(url, files=files, timeout=20)
        resp.raise_for_status()
        return resp.json()["results"]["fileHandle"]

    def submit_application(
        self,
        application_form_id: str,
        *,
        field_submissions: list[dict],
    ) -> httpx.Response:
        url = f"{self.BASE}/application?applicationFormId={application_form_id}"
        payload = {"fieldSubmissions": field_submissions}
        return httpx.post(url, json=payload, timeout=20, follow_redirects=False)
```

Internal representation stays simple (`{question_id: value}`); `AshbyClient` translates to Ashby's `fieldSubmissions` format inside `submit_application`.

**Ashby `fieldSubmissions` mapping rules** — implementer reference:

```json
{
  "fieldSubmissions": [
    {"path": "_RESUME", "value": {"fileHandle": "..."}},
    {"path": "_COVER_LETTER", "value": {"fileHandle": "..."}},
    {"path": "_FIRST_NAME", "value": "Utkarsh"},
    {"path": "_LAST_NAME", "value": "Singh"},
    {"path": "_EMAIL", "value": "254utkarsh@gmail.com"},
    {"path": "_PHONE", "value": "+353..."},
    {"path": "_LINKEDIN_URL", "value": "https://..."},
    {"path": "_GITHUB_URL", "value": "https://..."},
    {"path": "_WEBSITE", "value": "https://..."},
    {"path": "_SOURCE", "value": "LinkedIn"},
    {"path": "<customFieldPath>", "value": "answer text"},
    {"path": "<customFieldPath>", "value": ["option A", "option B"]}
  ]
}
```

Rules:

- **Standard fields** use underscore-capitalized paths prefixed with `_` (`_RESUME`, `_EMAIL`, `_FIRST_NAME`, etc.). The full list comes from Ashby's posting metadata `applicationForm.fieldDefinitions[]` — each definition has a `path` attribute that's either a standard `_PATH` or a custom path string.
- **File fields** (resume, cover letter, other uploads) take `{"fileHandle": str}` as the value, obtained by calling `upload_file()` first (2-step flow).
- **Text/textarea fields** take the raw string as the value.
- **Single-select dropdowns** take the string value (must match one of the options exactly).
- **Multi-select** fields take a list of strings.
- **Yes/no** fields take `"Yes"` or `"No"` as a string.

Build the `fieldSubmissions` list by iterating `applicationForm.fieldDefinitions` from the cached posting response: for each definition, look up the user's answer by the field's path, pick the correct value shape based on the field type. Cover letter mode: if the form has a `_COVER_LETTER` field of type `File`, upload and include the fileHandle. If it's of type `Text` or `LongText`, include the cover letter as a plain string. Check `fieldDefinition.type` before picking the value shape.

### 7.6 Rate limiting

```python
def _check_rate_limit(user_id: str) -> None:
    """Raise 429 if user has submitted ≥ 20 applications in the last hour."""
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    resp = _db.client.table("applications").select("id", count="exact").eq(
        "user_id", user_id
    ).gte("submitted_at", cutoff).eq("dry_run", False).execute()
    if resp.count and resp.count >= 20:
        raise HTTPException(429, "Rate limit: 20 Easy Apply submissions per hour")
```

Dry runs are not rate-limited.

### 7.7 Cache integration

Reuse the existing `ai_cache` table:

```python
# Cache key and storage
cache_key = f"apply_preview:{job_id}:{resume_version}"
cached = _db.client.table("ai_cache").select("response").eq(
    "cache_key", cache_key
).gte("expires_at", datetime.now(timezone.utc).isoformat()).execute()

if cached.data:
    payload = cached.data[0]["response"]
    payload["cache_hit"] = True
    return payload

# ... build payload ...

_db.client.table("ai_cache").upsert({
    "cache_key": cache_key,
    "provider": "apply_preview",
    "model": "n/a",
    "response": payload,
    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
}, on_conflict="cache_key").execute()
```

### 7.8 Error matrix

| Failure | HTTP | Reason code | User message |
|---------|------|-------------|--------------|
| URL not supported | 400 | `not_supported_platform` | "This job doesn't support Easy Apply" (badge hidden by eligibility check upstream) |
| No tailored resume | 400 | `no_resume` | "Your tailored resume is still being generated" |
| Already applied | 409 | `already_applied` | "You already applied on {date}" |
| Profile incomplete | 412 | `profile_incomplete` | "Complete your profile: {missing_fields}" |
| Additional files required | 400 | `requires_additional_files` | "This job requires a transcript — use external apply" |
| Cover letter too long | 422 | `cover_letter_too_long` | "Cover letter exceeds {max_length} chars" |
| Required answer missing | 422 | `required_answer_missing` | "Answer these required questions: {labels}" |
| Confirmation not checked | 422 | `confirmation_required` | "Please review and check the confirmation" |
| Resume version stale | 409 | `resume_version_stale` | "Your resume was updated — refresh to see new version" |
| Custom Qs fetch failed | 503 | `metadata_unavailable` | "Can't fetch application form — use external apply" |
| Rate limit exceeded | 429 | `rate_limited` | "20 submissions per hour — try again in {retry_after}" |
| Platform 404 | 404 | `job_no_longer_available` | "This job is no longer accepting applications" |
| Platform 4xx/5xx | 502 | `platform_error` | "Platform error: {detail}" + Retry |
| Network timeout mid-submit | 202 | `unknown_status` | "Status unclear — check platform directly" |

---

## 8. Frontend

### 8.1 Components

```
web/src/
├── components/
│   ├── EasyApplyBadge.jsx               (new)
│   ├── EasyApplyModal.jsx               (new)
│   ├── apply/
│   │   ├── ResumeSection.jsx            (new)
│   │   ├── CoverLetterSection.jsx       (new)
│   │   ├── QuestionsSection.jsx         (new)
│   │   ├── EEOSection.jsx               (new)
│   │   └── ConfirmationSection.jsx      (new)
├── api.js                               (+ getApplyEligibility, getApplyPreview, submitApplication)
├── pages/
│   ├── Dashboard.jsx                    (+ <EasyApplyBadge /> in card render)
│   └── JobWorkspace.jsx                 (populate Apply tab)
```

### 8.2 `EasyApplyBadge`

- Reads `job.easy_apply_eligible` from the job object returned by `/api/dashboard/jobs` (no per-card API call).
- Shows `⚡ Easy Apply` pill if eligible AND `job.application_status` is null / "New".
- Shows `✓ Applied {date}` pill if `job.application_status === 'Applied'` or later. Click opens modal in "already applied" view.
- Hidden if `easy_apply_eligible === false`.

### 8.3 `EasyApplyModal`

On open:

1. Render loading skeleton.
2. Call `getApplyPreview(jobId)`.
3. On response:
   - If `eligible=false` → render error state with specific reason message + external apply link.
   - If `profile_complete=false` → render "Complete your profile" message + link to Settings.
   - If `already_applied=true` → render history view (what was sent, status, submitted_at, link to platform).
   - Otherwise → render full form.

Form sections (all editable except profile):

- **Header**: job title, company, location, score tier
- **Resume**: preview iframe + filename + version dropdown
- **Profile**: read-only card (name / email / phone / linkedin / github / location)
- **Cover letter**: `[☑] Include cover letter` checkbox + textarea (if checked), `[X / max_length]` counter
- **Standard questions** (`category=custom` + `category=referral`): one input per question, AI-pre-filled
- **EEO section** (collapsed by default): questions all default to "Decline to self-identify"
- **Confirmations section** (if any `requires_user_action=true` questions): checkboxes all default to unchecked, required to enable Submit
- **Footer**: Cancel | Submit (disabled until all required + confirmations satisfied)

Submit handler:

```js
async function onSubmit() {
  const payload = {
    resume_version: preview.resume.resume_version,
    include_cover_letter: includeCL,
    cover_letter_text: includeCL ? coverLetterText : null,
    custom_answers: questions.map(q => ({
      question_id: q.id,
      value: answers[q.id],
      category: q.category,
    })),
    dry_run: urlSearchParams.get('dry_run') === 'true',
  };
  try {
    setSubmitting(true);
    const result = await submitApplication(jobId, payload);
    setSubmitState({ status: 'success', result });
    onApplied?.(result); // refetch dashboard
  } catch (e) {
    setSubmitState({ status: 'error', error: e });
  } finally {
    setSubmitting(false);
  }
}
```

### 8.4 Error message constants

```js
const ERROR_MESSAGES = {
  'not_supported_platform': "This job doesn't support Easy Apply. Use the external apply link.",
  'already_applied': "You already applied on {date}.",
  'no_resume': "Your tailored resume is still being generated. Try again in a moment.",
  'metadata_unavailable': "Can't fetch this job's application form. Use the external apply link.",
  'profile_incomplete': "Complete your profile before applying: {missing_fields}",
  'requires_additional_files': "This job requires {files}. Use the external apply link.",
  'cover_letter_too_long': "Cover letter exceeds {max_length} characters.",
  'required_answer_missing': "Answer these required questions: {labels}",
  'confirmation_required': "Please review and check the confirmation checkbox.",
  'rate_limited': "You've reached the hourly Easy Apply limit. Try again in {retry_after}.",
  'resume_version_stale': "Your resume was updated. Please refresh to see the new version.",
  'job_no_longer_available': "This job is no longer accepting applications.",
  'platform_error': "The platform returned an error: {detail}. You can retry or apply externally.",
  'unknown_status': "Submission status unclear. Check the platform directly.",
};
```

### 8.5 JobWorkspace Apply tab

Alternative full-page view of the same form for users who navigate to JobWorkspace directly instead of clicking the dashboard badge. Renders `<EasyApplyForm />` — the same subcomponent that `<EasyApplyModal />` wraps. No modal chrome (no close X, no overlay). Submit behavior identical.

**Badge click semantics:**
- From Dashboard → opens `<EasyApplyModal />` (overlay, stays on dashboard)
- From JobWorkspace → opens `<EasyApplyModal />` (overlay, stays on workspace)
- Visiting `/workspace/{job_id}` directly and switching to "Apply" tab → renders inline `<EasyApplyForm />` without a modal

Consistent: badge always opens modal. The tab is a no-modal alternative for direct URL users.

### 8.6 Dashboard integration

- In `Dashboard.jsx`, pass the new `easy_apply_eligible` field through the existing job card render loop.
- Mount `<EasyApplyModal />` at app root via React portal.
- On badge click, set `selectedJobId` state, which opens the modal.
- On successful submit (`onApplied` callback), refetch current page of jobs.

---

## 9. Pipeline integration

### 9.1 `lambdas/pipeline/score_batch.py`

Two changes:

1. `should_skip_scoring()` — add `_NON_JOB_TITLE_PATTERNS` and `_ENTRY_LEVEL_ONLY_PATTERNS` checks (§4.1).
2. Before `.insert()`, populate apply columns from the raw job's URL:

```python
from utils.platform_parsers import parse_apply_url  # lambdas/pipeline/utils/platform_parsers.py

platform_info = parse_apply_url(job.get("apply_url", ""))
if platform_info:
    job_record["apply_platform"] = platform_info.platform
    job_record["apply_board_token"] = platform_info.board_token
    job_record["apply_posting_id"] = platform_info.posting_id
```

`utils/platform_parsers.py` must exist inside `lambdas/pipeline/utils/` (packaged with pipeline Lambdas) as well as at root `utils/` (packaged with FastAPI container). Keep them identical.

### 9.2 `app.py::run_single_job`

Same — after score_batch completes and creates the job row, the new columns are populated by score_batch itself. No additional change needed in `run_single_job`.

### 9.3 Forward-looking canonical-hash mirroring (new job insert path)

**Problem:** §7.4 step 13 mirrors `jobs.application_status = 'Applied'` across all existing jobs sharing `canonical_hash` at submit time. But tomorrow's scrape may create a new `jobs` row with the same `canonical_hash` (duplicate of an already-applied job on a different source). The new row would start with `application_status='New'` — wrong.

**Fix:** In `score_batch.py`'s insert path, after building the `job_record` but before the final `.insert()`, check for existing applications by canonical_hash and pre-set the status:

```python
# After URL parsing, before insert
canonical = job_record.get("canonical_hash")
if canonical:
    existing = db.table("applications").select("id, status").eq(
        "user_id", user_id
    ).eq("canonical_hash", canonical).not_.in_(
        "status", ["unknown", "failed"]
    ).limit(1).execute()
    if existing.data:
        # Already applied to a canonical sibling — mark this new row as Applied
        job_record["application_status"] = "Applied"
```

This runs once per new job insert. Cost: one extra indexed lookup per insert. Worth it for consistency.

Same logic must be added to any other code path that inserts into `jobs` — currently only `score_batch.py` (no other path writes directly to `jobs`).

---

## 10. Testing

### 10.1 Unit tests

| File | What |
|------|------|
| `tests/unit/test_platform_parsers.py` | `parse_apply_url()` for all supported and unsupported URL formats; `resolve_work_auth_country()` edge cases |
| `tests/unit/test_latex_to_text.py` | Tailored CL → plaintext (fixtures for several known CL layouts); edge cases (empty, malformed, no document env) |
| `tests/unit/test_platform_clients.py` | `GreenhouseClient.fetch_job_metadata()` with mocked httpx; `AshbyClient.fetch_job_posting()` returning (data, form_id); `GreenhouseClient.submit_application()` builds correct multipart payload |
| `tests/unit/test_score_batch.py` (existing) | Add cases for non-job patterns, entry-level patterns (both should return skip reasons) |
| `tests/unit/test_utils_sync.py` | Byte-compares `utils/platform_parsers.py` in root vs `lambdas/pipeline/utils/`. Fails CI if drift |

### 10.2 Integration tests

| File | What |
|------|------|
| `tests/integration/test_apply_eligibility.py` | All branches: eligible, not_supported_platform, no_resume, already_applied, profile_incomplete |
| `tests/integration/test_easy_apply_flow.py` | Full flow with mocked Greenhouse API: preview → submit → verify `applications` row + `jobs.application_status` mirror + `application_timeline` event |
| `tests/integration/test_apply_idempotency.py` | Submit twice (same canonical_hash), second → 409 |
| `tests/integration/test_apply_rate_limit.py` | 21st submission in hour → 429 (but dry runs don't count) |
| `tests/integration/test_apply_dry_run.py` | Dry run returns payload without hitting the mocked platform, writes row with `dry_run=true` |
| `tests/integration/test_apply_resume_version_lock.py` | Preview at v2, resume re-tailored to v3, submit at v2 → 409 resume_version_stale |
| `tests/integration/test_apply_canonical_mirror.py` | Apply to the Greenhouse row, the LinkedIn row (same canonical_hash) also flips to Applied |
| `tests/integration/test_apply_phase_0_cleanup.py` | `scripts/phase0_cleanup.py` marks the MongoDB Women in Tech Summit entry as expired; Twilio Intern entry expired |

### 10.3 Fixture data

`scripts/fetch_platform_fixtures.py` (one-time, commits output):

1. `httpx.get("https://boards-api.greenhouse.io/v1/boards/intercom/jobs/6949785?questions=true")`
2. `httpx.get("https://api.ashbyhq.com/posting-api/job-posting/3cdfdc0c-c69d-42cf-b61b-3806b35fcc85")`
3. Sanitize (remove tracking IDs, applicant-specific fields if any)
4. Save to `tests/fixtures/greenhouse_intercom.json` and `tests/fixtures/ashby_livekit.json`

Tests load from fixtures — no real API calls in CI.

### 10.4 Manual E2E smoke test

1. **Dry run**: submit preview + dry_run against `intercom/jobs/6949785`. Inspect the payload in the `applications` row.
2. **Dry run**: submit preview + dry_run against the Livekit Frontend Engineer Ashby posting. Inspect the fieldSubmissions format.
3. **Real submission** (only with explicit user approval): flip `dry_run=false` for one job, submit, verify on the Greenhouse/Ashby platform (via a received confirmation email or by logging into the candidate portal if possible).

---

## 11. Rollout plan

Phases within PR #1 (all in one commit series on a feature branch):

| # | Step |
|---|------|
| F1 | Apply migration `20260411_auto_apply_setup.sql` to Supabase |
| F2 | Run `scripts/phase0_cleanup.py` — backfill apply_platform, mark existing non-jobs expired |
| F3 | Run `scripts/fetch_platform_fixtures.py` once, commit fixtures |
| F4 | Run full `pytest` — must pass 100% |
| F5 | `sam build && sam deploy` |
| F6 | `cd web && npm run build && netlify deploy --prod` |
| F7 | Dry-run against Intercom (Greenhouse) |
| F8 | Dry-run against Livekit (Ashby) |
| F9 | User reviews both dry-run payloads |
| F10 | User approves one specific job for a real submission |
| F11 | Real submission to that one job |
| F12 | Monitor `applications` table for 24h, look for issues |

### Rollback plan

- Migration is additive (new columns + table), no DROPs. Rollback = leave columns in place, revert code.
- Code revert: `git revert <sha>` + redeploy.
- If a real submission was wrong (e.g., wrong resume sent), the user contacts the company directly.

---

## 12. Out of scope for this PR

Explicitly deferred to later PRs in the Auto-Apply series:

- **Mode 2** (Remote Browser via Fargate + WebSocket + DynamoDB session store)
- **Mode 3** (Assisted Manual Apply with clipboard helper)
- **Batch Easy Apply** (multi-select + sequential modal)
- **Outcome tracking automation** (email polling for "Application viewed" notifications → update status)
- **Analytics events** (apply_preview_clicked, apply_submitted with properties)
- **Inline profile edit** in modal (vs link to Settings)
- **Auto-close modal on success** (defaults to manual close)
- **CloudWatch alarms** for submit error rate
- **Greenhouse custom-host probe** (stripe.com/jobs, mongodb.com/careers — try boards-api with extracted gh_jid)
- **Ashby conditional questions** (hide dependent questions based on parent answers)

---

## 13. Cost estimate

| Component | Cost per submission |
|-----------|---------------------|
| S3 GET (resume PDF) | ~$0.000004 |
| Greenhouse/Ashby API | free (public application endpoints) |
| Lambda invocation (preview + submit) | ~$0.00002 (fits in free tier) |
| AI answer generation (custom Qs) | ~$0.001 (3–6 Qs × 1 call each) — cached per job_id+resume_version for 10 min |
| DB writes (applications, timeline) | negligible |
| **Total per submission** | **~$0.001** |
| 100 submissions/month | **~$0.10** |

The cost is dominated by AI answer generation. Phase 0's non-job filter + num_calls=1 reductions already cut AI spend ~80%, so Mode 1's added cost is marginal.

---

## 14. Success criteria

Before declaring Mode 1 done:

- [ ] `pytest` 100% pass rate including all new tests
- [ ] `sam build && sam deploy` succeeds with no drift
- [ ] `scripts/phase0_cleanup.py` has run; MongoDB summit + Twilio intern entries are marked expired
- [ ] User profile has populated: first_name, last_name, phone, linkedin, github, location, visa_status, work_authorizations, default_referral_source, notice_period_text
- [ ] `jobs.apply_platform` populated for all non-expired Greenhouse/Ashby jobs
- [ ] `jobs.easy_apply_eligible` column returns true for 57+ current jobs (37 direct Greenhouse + 20 Ashby)
- [ ] `⚡ Easy Apply` badge appears on eligible jobs in Dashboard
- [ ] Dry run against Intercom (Greenhouse) produces a valid payload
- [ ] Dry run against Livekit (Ashby) produces a valid fieldSubmissions format
- [ ] One real submission succeeds end-to-end with confirmation
- [ ] `applications` row written with correct data; `jobs.application_status = 'Applied'` mirrored across canonical-hash siblings; timeline event inserted

---

## 15. Open questions / decisions made during brainstorming

Resolved:

- Mode 1 first, separate from Modes 2+3 (less architectural risk, faster value)
- Strict review with mandatory confirmation checkbox detection (no auto-submit)
- Full scope in one PR (Phase 0 + Mode 1 shared foundation)
- Greenhouse + Ashby both supported
- Cross-canonical-hash Applied mirroring: YES
- `github` = optional, `location` = required
- `ai_cache` reused for preview caching (no new table)
- Backfill included in migration, not separate script
- E2E test targets: Intercom (Greenhouse), Livekit (Ashby)

No remaining open questions. Design is complete.
