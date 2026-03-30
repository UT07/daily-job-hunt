# Phase 1 Completion — SQS Async Architecture + 44 Bug Fixes

**Date**: 2026-03-30
**Status**: Pending approval
**Scope**: Fix all known bugs before Phase 2 development begins

---

## Context

Phase 1 (pipeline + landing page + deployment) is complete but has 44 bugs discovered during deep audit. The most critical is that the Lambda async task architecture is fundamentally broken — daemon threads are killed when Lambda freezes the container after returning the HTTP response. This makes the tailor, cover letter, and contacts endpoints non-functional on production.

The fix replaces the broken thread-based async with an SQS queue, which also prepares the infrastructure for Phase 2 multi-user pipeline runs (WS-6).

---

## Phase 0: SQS Architecture + Security Fixes

### SQS Queue Design

**New resources in template.yaml:**

```yaml
TaskQueue:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub "${AWS::StackName}-tasks"
    VisibilityTimeout: 5400     # 6x Lambda max timeout (900s)
    MessageRetentionPeriod: 86400  # 24 hours
    RedrivePolicy:
      deadLetterTargetArn: !GetAtt TaskDLQ.Arn
      maxReceiveCount: 3

TaskDLQ:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub "${AWS::StackName}-tasks-dlq"
    MessageRetentionPeriod: 1209600  # 14 days
```

**Event source mapping on Lambda:**

```yaml
# Add to JobHuntApi Events:
TaskWorker:
  Type: SQS
  Properties:
    Queue: !GetAtt TaskQueue.Arn
    BatchSize: 1
    FunctionResponseTypes:
      - ReportBatchItemFailures
```

**IAM policies:**

```yaml
Policies:
  - SQSSendMessagePolicy:
      QueueName: !Ref TaskQueue
  - SQSPollerPolicy:
      QueueName: !Ref TaskQueue
```

**Environment variables:**

```yaml
TASK_QUEUE_URL: !Ref TaskQueue
SELF_FUNCTION_ARN: !GetAtt JobHuntApi.Arn
```

**Lambda timeout increase:** 300s → 900s (15 min max, covers worst-case AI council + tectonic cold start)

### Handler Routing (app.py)

Replace the current handler setup:

```python
# Current (broken for async):
try:
    from mangum import Mangum
    handler = Mangum(app, api_gateway_base_path="/prod")
except ImportError:
    handler = None
```

With:

```python
try:
    from mangum import Mangum
    _mangum = Mangum(app, api_gateway_base_path="/prod")
except ImportError:
    _mangum = None

def handler(event, context):
    if "Records" in event and event["Records"][0].get("eventSource") == "aws:sqs":
        return _process_sqs_task(event, context)
    return _mangum(event, context)
```

Note: `api_gateway_base_path="/prod"` must be preserved — it strips the `/prod` stage prefix from API Gateway URLs.

The SQS worker function `_process_sqs_task` reads `task_id` from the SQS message body, fetches task details from Supabase `pipeline_tasks`, and dispatches to the appropriate worker (`_do_tailor`, `_do_cover_letter`, `_do_contacts`).

### Replace _run_in_background

Current (broken):
```python
def _run_in_background(task_id, user_id, fn, *args):
    _save_task(task_id, user_id, {"status": "running"})
    threading.Thread(target=_worker, daemon=True).start()
    return task_id
```

New:
```python
def _enqueue_task(task_id, user_id, task_type, payload):
    _save_task(task_id, user_id, {"status": "queued"})
    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=os.environ["TASK_QUEUE_URL"],
        MessageBody=json.dumps({"task_id": task_id, "task_type": task_type, "payload": payload}),
    )
    return task_id
```

Task payload contains `task_id`, `task_type`, and minimal routing info. The full task input (JD text, resume type, user config) is stored in `pipeline_tasks.payload` (JSONB column) when the task is created, and the worker reads it back from Supabase using the `task_id`. This keeps SQS messages under the 256KB limit and makes task replay possible from the DLQ.

**pipeline_tasks schema addition:**
```sql
ALTER TABLE pipeline_tasks ADD COLUMN IF NOT EXISTS payload JSONB;
```

### Security Fix: CORS (B1)

**File:** `app.py:75-81` and `template.yaml:83-91`

Replace `allow_origins=["*"]` with explicit origins:
```python
allow_origins=["https://naukribaba.netlify.app", "http://localhost:5173"]
```

Same change in template.yaml `CorsConfiguration.AllowOrigins`.

### Security Fix: Resume Delete IDOR (B6)

**File:** `db_client.py:114-117`

Add `.eq("user_id", user_id)` to the delete query. Pass `user_id` from the endpoint.

### Fix: Tailor Crash on Empty Return (B3/B14)

**File:** `app.py:426-427`, `tailorer.py:243-245`

In `_do_tailor`, check `if not tex_path` before `Path(tex_path).read_text()`. Raise a descriptive error: `"Tailoring failed — AI returned empty result"`.

### Fix: Drive Folder Hierarchy (B17)

**File:** `app.py:295`

Replace single `_get_or_create_folder(service, f"Job Hunt/{date_str}/web", root_id)` with chained calls:
```python
jh_id = _get_or_create_folder(service, "Job Hunt", root_id)
date_id = _get_or_create_folder(service, date_str, jh_id)
web_id = _get_or_create_folder(service, "web", date_id)
```

### Fix: _save_task Recursion Guard (B-misc)

**File:** `app.py:404-407`

Guard `_save_task` in the error handler: `if _db: _save_task(...)` with a fallback to `logger.error()`.

### Fix: quality_logger Lambda Path (B-misc)

**File:** `quality_logger.py:14`

Add Lambda detection:
```python
LOG_PATH = Path("/tmp/ai_quality_log.jsonl") if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else Path("output/ai_quality_log.jsonl")
```

### Fix: score_and_improve Silent Failure Indicator (B-misc)

**File:** `app.py:429-432`

When `score_and_improve` fails, include `"scoring_failed": true` in the task result so the frontend can show a warning.

---

## Phase 1A: Backend Data Fixes

### B18: Write tailoring_model to Supabase

**File:** `main.py:114-135`

Add `"tailoring_model": job.tailoring_model` to `_job_to_supabase_row()`.

### B19: Write Drive URLs to Supabase

**File:** `main.py:1120-1134`

Add `resume_drive_url` and `cover_letter_drive_url` to the upsert row alongside the existing S3 URLs.

### B4: Fix update_job_status IndexError

**File:** `db_client.py:208`

Check `if not result.data` → raise 404 instead of crashing on `result.data[0]`.

### B5: Fix update_user None guard

**File:** `app.py:554-561`

Check `if row is None: raise HTTPException(404, "User not found")`.

### B7/B15: Add total count to paginated jobs

**File:** `db_client.py:160-195`

Add `count="exact"` to the Supabase query. Return `{"jobs": [...], "total": count, "page": page, "per_page": per_page}`.

### B9: Tune contact_finder prompt (seniority)

**File:** `contact_finder.py:304-330`

The prompt says "hiring manager" without seniority constraints. The AI interprets this as VP/Director/Head-of because those are technically "hiring managers" for most roles. Fix: change "hiring manager" to "direct hiring manager or team lead at the appropriate level for the role (e.g., Engineering Manager for mid-level roles, NOT VP/Director/Head-of unless the position reports directly to them)". Add: "The hiring manager contact should be someone the candidate would realistically report to."

### B11: Fabrication detection threshold

**File:** `resume_scorer.py:55-61`

Add clarification to the AI prompt: "Only flag fabrication for outright invented companies, degrees, or certifications. Rephrasing existing experience (e.g., 'led' vs 'contributed to') is NOT fabrication — it is resume optimization."

### B12: Remove extra score_resume call

**File:** `resume_scorer.py:498-505`

Reuse scores from the last loop iteration instead of making a 4th API call.

### B13: GradIreland company extraction

**File:** `scrapers/gradireland_scraper.py:164-228`

For Strategies 2 and 3, add extraction of company from the job detail page's employer section. GradIreland typically has the company in a `div.employer-name` or similar element near the job title.

### B2: Fix pdf_url temp path

**File:** `app.py:449`

When Drive upload fails, return `"pdf_url": ""` (not the temp path) and include `"drive_upload_failed": true` in the result.

### B8: Improve field validation error

**File:** `app.py:576-591`

Add `"search_queries": "queries"` to `_FIELD_MAP` (currently has `queries`, `keywords`, `job_titles` mapping to `queries` column, but NOT `search_queries`). This fixes the frontend/backend field name mismatch at the source. Also fix the frontend to use `queries` going forward for consistency.

### B10: Connection message length

**File:** `contact_finder.py:424-426`

Change `> 300` to `> 280` to match the prompt's stated limit.

---

## Phase 1B: Frontend Fixes

### F1 + F2: Settings loads data + correct field names

**File:** `web/src/pages/Settings.jsx`

- Add `useEffect` on mount to fetch `GET /api/profile` and `GET /api/search-config`, populate `profile` and `prefs` state
- Change `search_queries` → `queries` in the prefs state key (or backend now accepts both per B8)

### F3: Fix setState during render

**Files:** `web/src/pages/Settings.jsx:546-549`, `web/src/pages/Onboarding.jsx:527-529`

Move `setProfile` call into a `useEffect` with `[user]` dependency.

### F14: Add auth guard to Settings

**File:** `web/src/pages/Settings.jsx`

Add `if (!user && !loading) return <LoginPage />` pattern (same as Dashboard).

### F19: Fix filter re-fetch logic

**File:** `web/src/pages/Dashboard.jsx:161-211`

The dropdowns call `setPage(1)` on change, and `page` is in `fetchJobs` dependencies `[filterVersion, page]`. This means a filter change triggers a fetch ONLY if page was not already 1. On the first page (most common case), changing a dropdown does nothing until "Apply Filters" is clicked.

Fix: add `statusFilter` and `sourceFilter` to the `fetchJobs` dependency array so any dropdown change triggers an immediate re-fetch. Remove the separate "Apply Filters" button — all controls become live.

### F18: Fix pagination with total count

**File:** `web/src/pages/Dashboard.jsx:91`

Use `total` from the API response (now returned by B7/B15 fix) instead of `stats.total_jobs`.

### F6: Fix contacts link label

**File:** `web/src/components/ContactsCard.jsx:13-14`

```js
const linkLabel = contact.profile_url ? 'View Profile' : contact.google_url ? 'Find on Google' : 'Search LinkedIn';
```

### F7: Null-guard clipboard

**File:** `web/src/components/ContactsCard.jsx:7`

`navigator.clipboard.writeText(contact.message || "")`

### F9: Fix duplicate asset icons

**File:** `web/src/components/JobTable.jsx:266-278`

Only show Google Doc icon when `resume_doc_url` exists AND differs from the S3 URL.

### F17: ScoreBadge null/zero guard

**File:** `web/src/components/ScoreBadge.jsx:3`

Add `if (score == null || score === 0) return <span>--</span>` at the top.

### F16: Null-supabase guard in api.js

**File:** `web/src/api.js:5-11`

Add `if (!supabase) return {}` guard in `authHeaders()`.

### F12: Poll progress indicator

**File:** `web/src/api.js:42-43`

Add an optional `onProgress` callback to `pollTask` that fires with the current status on each poll iteration. Frontend components show "Processing..." instead of a bare spinner.

### F15: OAuth redirect preserves path

**File:** `web/src/auth/useAuth.js:25-26`

Change `redirectTo: window.location.origin` → `redirectTo: window.location.href`.

### F22: Onboarding replace alert()

**File:** `web/src/pages/Onboarding.jsx:567`

Replace `alert()` with inline success state (same pattern as other forms).

### F5: Resume listing

**File:** `web/src/pages/Settings.jsx:349-352`

Call `GET /api/resumes` on mount and render the actual resume list.

### F20: Score slider trigger

**File:** `web/src/pages/Dashboard.jsx:188-192`

Make `onMouseUp`/`onTouchEnd` call `handleFilterApply()`.

---

## Phase 1C: Validation

### Backend validation
- Deploy with `sam build && sam deploy`
- Test `POST /api/tailor` → verify SQS message appears in queue
- Verify worker Lambda processes the message → task status goes to "done" in Supabase
- Verify Drive upload creates proper folder hierarchy
- Verify `GET /api/tasks/{id}` returns completed result with Drive URL
- Test `DELETE /api/resumes/{id}` with wrong user → verify 403/404

### Frontend validation
- Open Settings → verify existing data loads
- Change search queries → save → reload → verify persistence
- Open Dashboard → verify filters trigger re-fetch immediately
- Verify pagination shows correct total
- Verify contacts show correct link labels
- Test tailor flow E2E: paste JD → tailor → poll → get PDF link

### Pipeline validation
- Trigger daily pipeline run
- Verify `tailoring_model` and `resume_drive_url` populate in Supabase
- Verify GradIreland jobs have company names
- Verify contact suggestions are appropriately senior (not VP/Director)

---

## Files Modified

| File | Changes |
|------|---------|
| `template.yaml` | +TaskQueue, +TaskDLQ, +SQS event, +IAM policies, timeout 900s, CORS origins |
| `Dockerfile.lambda` | No changes needed (boto3 already in requirements-web.txt) |
| `requirements-web.txt` | No changes needed (boto3>=1.34.0 already present) |
| `app.py` | Handler routing, _enqueue_task, _process_sqs_task, CORS fix, IDOR fix, tailor crash fix, Drive folder fix, save_task guard, score failure indicator, field map fix, pdf_url fix, update_user guard |
| `db_client.py` | Resume delete ownership, update_job_status guard, total count, |
| `tailorer.py` | (no change — error handled by caller) |
| `resume_scorer.py` | Fabrication prompt tuning, remove extra score call |
| `contact_finder.py` | Seniority prompt fix, message length guard |
| `quality_logger.py` | Lambda-aware path |
| `drive_uploader.py` | (no change — caller in app.py fixed) |
| `main.py` | Add tailoring_model + drive URLs to _job_to_supabase_row |
| `scrapers/gradireland_scraper.py` | Company field extraction for strategies 2/3 |
| `web/src/pages/Settings.jsx` | Load on mount, field name fix, setState fix, auth guard, resume listing |
| `web/src/pages/Dashboard.jsx` | Filter re-fetch, pagination total, slider trigger |
| `web/src/pages/Onboarding.jsx` | Field name fix, setState fix, replace alert() |
| `web/src/components/ContactsCard.jsx` | Link label fix, clipboard guard |
| `web/src/components/JobTable.jsx` | Asset icon dedup, contact URL validation |
| `web/src/components/ScoreBadge.jsx` | Null/zero guard |
| `web/src/api.js` | Null-supabase guard, poll progress callback |
| `web/src/auth/useAuth.js` | OAuth redirect fix |

---

## Multi-User Readiness

The SQS architecture is designed for Phase 2 WS-6:
- Task payload includes `user_id` — worker fetches user-specific config from Supabase
- Queue handles tasks from any user, processed independently
- DLQ captures failures per-task for debugging
- `BatchSize: 1` ensures one task per Lambda invocation (no cross-user interference)
- When WS-6 adds `POST /api/pipeline/run`, it uses the same queue with `task_type: "pipeline_run"`

---

## Not In Scope

These items are deferred to Phase 2:
- F24: AI Quality Stats refresh (cosmetic, Phase 2 WS-3 dashboard redesign)
- F23: Account deletion signOut error handling (edge case)
- F21: ConsentBanner apiCall overhead (cosmetic)
- F13: Auth token refresh during poll (1-hour JWT TTL makes this extremely unlikely)
- F11: Double-slash URL risk (cosmetic, works in practice)
- F10: Whitespace-only URL check (edge case)
- B16: Stats full-table scan optimization (Phase 2 WS-4 dashboard redesign)
- F25: StatusDropdown failure feedback (Phase 2 WS-4)
- F8: profile_url validation (Phase 2 WS-4)
