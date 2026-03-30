# Phase 1 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 35 known bugs (security, backend, frontend) and replace broken Lambda async architecture with SQS queue before Phase 2 development begins.

**Architecture:** Replace daemon-thread async with SQS Standard queue + DLQ. Lambda handler routes between API Gateway events (Mangum) and SQS worker events. Frontend polls Supabase `pipeline_tasks` via existing `/api/tasks/{id}` endpoint. All fixes are backward-compatible with local development (uvicorn).

**Tech Stack:** Python 3.11, FastAPI, Mangum, boto3 (SQS), Supabase, React 18, Vite, Tailwind CSS, AWS SAM (CloudFormation)

**Spec:** `docs/superpowers/specs/2026-03-30-phase1-completion-design.md`

---

## File Structure

### Backend (Modified)
| File | Responsibility |
|------|---------------|
| `template.yaml` | SAM template: +TaskQueue, +TaskDLQ, +SQS event, +IAM, timeout, CORS |
| `app.py` | Handler routing, SQS enqueue/worker, CORS, IDOR, tailor/drive fixes |
| `db_client.py` | Resume delete ownership, job status guard, pagination total count |
| `quality_logger.py` | Lambda-aware log path |
| `contact_finder.py` | Seniority prompt fix, message length |
| `resume_scorer.py` | Fabrication prompt, remove extra score call |
| `main.py` | Add tailoring_model + drive URLs to Supabase upsert |
| `scrapers/gradireland_scraper.py` | Company field extraction |

### Frontend (Modified)
| File | Responsibility |
|------|---------------|
| `web/src/api.js` | Null-supabase guard, poll progress callback |
| `web/src/auth/useAuth.js` | OAuth redirect preserves path |
| `web/src/pages/Settings.jsx` | Load data on mount, field names, setState fix, auth guard, resume list |
| `web/src/pages/Dashboard.jsx` | Live filters, pagination total, slider trigger |
| `web/src/pages/Onboarding.jsx` | Field name fix, setState fix, replace alert() |
| `web/src/components/ContactsCard.jsx` | Link label fix, clipboard guard |
| `web/src/components/JobTable.jsx` | Asset icon dedup |
| `web/src/components/ScoreBadge.jsx` | Null/zero guard |

---

## Task 1: SQS Infrastructure (template.yaml)

**Files:**
- Modify: `template.yaml`

- [ ] **Step 1: Add SQS queues and update Lambda config**

Add the TaskQueue, TaskDLQ, SQS event source mapping, IAM policies, env vars, and increase timeout:

```yaml
# In template.yaml, replace the entire file with:

AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Job Hunt API — FastAPI on Lambda (container image with tectonic)

Globals:
  Function:
    Timeout: 900
    MemorySize: 1024

Parameters:
  GroqApiKey:
    Type: String
    NoEcho: true
  OpenRouterApiKey:
    Type: String
    NoEcho: true
  QwenApiKey:
    Type: String
    NoEcho: true
    Default: ""
  NvidiaApiKey:
    Type: String
    NoEcho: true
    Default: ""
  GoogleCredentialsJson:
    Type: String
    NoEcho: true
  SupabaseUrl:
    Type: String
  SupabaseServiceKey:
    Type: String
    NoEcho: true
  SupabaseJwtSecret:
    Type: String
    NoEcho: true
  SerperApiKey:
    Type: String
    NoEcho: true
    Default: ""
  ApifyApiKey:
    Type: String
    NoEcho: true
    Default: ""

Resources:

  # --- SQS Task Queue + Dead Letter Queue ---
  TaskQueue:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: !Sub "${AWS::StackName}-tasks"
      VisibilityTimeout: 5400
      MessageRetentionPeriod: 86400
      RedrivePolicy:
        deadLetterTargetArn: !GetAtt TaskDLQ.Arn
        maxReceiveCount: 3

  TaskDLQ:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: !Sub "${AWS::StackName}-tasks-dlq"
      MessageRetentionPeriod: 1209600

  # --- Lambda Function (Docker container with tectonic) ---
  JobHuntApi:
    Type: AWS::Serverless::Function
    Properties:
      PackageType: Image
      Description: Job Hunt FastAPI backend with LaTeX (tectonic) support
      Policies:
        - SQSSendMessagePolicy:
            QueueName: !GetAtt TaskQueue.QueueName
        - SQSPollerPolicy:
            QueueName: !GetAtt TaskQueue.QueueName
      Environment:
        Variables:
          GROQ_API_KEY: !Ref GroqApiKey
          OPENROUTER_API_KEY: !Ref OpenRouterApiKey
          QWEN_API_KEY: !Ref QwenApiKey
          NVIDIA_API_KEY: !Ref NvidiaApiKey
          GOOGLE_CREDENTIALS_JSON: !Ref GoogleCredentialsJson
          SUPABASE_URL: !Ref SupabaseUrl
          SUPABASE_SERVICE_KEY: !Ref SupabaseServiceKey
          SUPABASE_JWT_SECRET: !Ref SupabaseJwtSecret
          SERPER_API_KEY: !Ref SerperApiKey
          APIFY_API_KEY: !Ref ApifyApiKey
          TASK_QUEUE_URL: !Ref TaskQueue
      Events:
        ApiGateway:
          Type: HttpApi
          Properties:
            ApiId: !Ref HttpApi
            Path: /{proxy+}
            Method: ANY
        TaskWorker:
          Type: SQS
          Properties:
            Queue: !GetAtt TaskQueue.Arn
            BatchSize: 1
            FunctionResponseTypes:
              - ReportBatchItemFailures
    Metadata:
      DockerTag: latest
      DockerContext: .
      Dockerfile: Dockerfile.lambda

  # --- HTTP API Gateway ---
  HttpApi:
    Type: AWS::Serverless::HttpApi
    Properties:
      StageName: prod
      CorsConfiguration:
        AllowOrigins:
          - "https://naukribaba.netlify.app"
          - "http://localhost:5173"
        AllowMethods:
          - "*"
          - GET
          - POST
          - PUT
          - PATCH
          - DELETE
          - OPTIONS
        AllowHeaders:
          - "*"
        AllowCredentials: true

  # --- S3 Bucket for Frontend ---
  FrontendBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub "${AWS::StackName}-frontend"
      WebsiteConfiguration:
        IndexDocument: index.html
      PublicAccessBlockConfiguration:
        BlockPublicAcls: false
        BlockPublicPolicy: false
        IgnorePublicAcls: false
        RestrictPublicBuckets: false

  FrontendBucketPolicy:
    Type: AWS::S3::BucketPolicy
    Properties:
      Bucket: !Ref FrontendBucket
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Sid: PublicRead
            Effect: Allow
            Principal: "*"
            Action: s3:GetObject
            Resource: !Sub "${FrontendBucket.Arn}/*"

Outputs:
  ApiUrl:
    Description: API Gateway URL
    Value: !Sub "https://${HttpApi}.execute-api.${AWS::Region}.amazonaws.com/prod"
  FrontendUrl:
    Description: Frontend S3 website URL
    Value: !GetAtt FrontendBucket.WebsiteURL
  TaskQueueUrl:
    Description: SQS Task Queue URL
    Value: !Ref TaskQueue
  TaskDLQUrl:
    Description: SQS Dead Letter Queue URL
    Value: !Ref TaskDLQ
```

- [ ] **Step 2: Validate template syntax**

Run: `python -c "import yaml; yaml.safe_load(open('template.yaml'))"`
Expected: No error (valid YAML)

- [ ] **Step 3: Commit**

```bash
git add template.yaml
git commit -m "infra: add SQS task queue + DLQ, increase Lambda timeout to 900s, fix CORS origins"
```

---

## Task 2: SQS Handler Routing + Enqueue (app.py core async)

**Files:**
- Modify: `app.py:27-34` (imports), `app.py:362-418` (async helpers), `app.py:470-503` (endpoints), `app.py:798-802` (handler)

- [ ] **Step 1: Add boto3 import and SQS enqueue function**

In `app.py`, add `import boto3` after the existing imports (around line 34), then replace the entire async task helper section (lines 362-418) with:

```python
# ---------------------------------------------------------------------------
# Async task helpers — SQS-based for Lambda compatibility
# ---------------------------------------------------------------------------

def _save_task(task_id: str, user_id: str, data: dict):
    """Persist task state to Supabase pipeline_tasks table."""
    if not _db:
        logger.error("Cannot save task %s: database not configured", task_id)
        return
    row = {
        "task_id": task_id,
        "user_id": user_id,
        "status": data.get("status", "running"),
        "result": data.get("result"),
        "error": data.get("error"),
        "payload": data.get("payload"),
    }
    _db.client.table("pipeline_tasks").upsert(row, on_conflict="task_id").execute()


def _load_task(task_id: str) -> dict | None:
    """Load task state from Supabase."""
    if not _db:
        return None
    result = _db.client.table("pipeline_tasks").select("*").eq("task_id", task_id).maybe_single().execute()
    if not result or not result.data:
        return None
    row = result.data
    task = {"status": row["status"]}
    if row.get("result"):
        task["result"] = row["result"]
    if row.get("error"):
        task["error"] = row["error"]
    return task


def _enqueue_task(task_id: str, user_id: str, task_type: str, payload: dict):
    """Save task to Supabase and send to SQS for processing."""
    _save_task(task_id, user_id, {"status": "queued", "payload": payload})

    queue_url = os.environ.get("TASK_QUEUE_URL")
    if queue_url:
        sqs = boto3.client("sqs")
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "task_id": task_id,
                "task_type": task_type,
                "user_id": user_id,
            }),
        )
    else:
        # Local dev fallback: run synchronously in a thread
        logger.info("No TASK_QUEUE_URL — running task %s synchronously", task_id)
        _save_task(task_id, user_id, {"status": "running"})
        def _worker():
            try:
                result = _dispatch_task(task_type, payload)
                _save_task(task_id, user_id, {"status": "done", "result": result})
            except Exception as e:
                logger.error("Task %s failed: %s", task_id, e)
                _save_task(task_id, user_id, {"status": "error", "error": str(e)})
        threading.Thread(target=_worker, daemon=True).start()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: AuthUser = Depends(get_current_user)):
    """Poll for the result of an async task."""
    task = _load_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task
```

- [ ] **Step 2: Add the SQS worker dispatcher and process function**

Add this right after the `get_task` endpoint:

```python
def _dispatch_task(task_type: str, payload: dict):
    """Route a task to the appropriate worker function."""
    if task_type == "tailor":
        job = _Job(payload["job_title"], payload["company"], payload["job_description"])
        base_tex = _resumes.get(payload["resume_type"], "")
        if not base_tex:
            raise ValueError(f"Unknown resume type: {payload['resume_type']}")
        return _do_tailor(job, base_tex, payload["resume_type"], payload["company"], payload["job_title"])
    elif task_type == "cover_letter":
        job = _Job(payload["job_title"], payload["company"], payload["job_description"])
        resume_tex = _resumes.get(payload["resume_type"], "")
        if not resume_tex:
            raise ValueError(f"Unknown resume type: {payload['resume_type']}")
        return _do_cover_letter(job, resume_tex, payload["company"], payload["job_title"])
    elif task_type == "contacts":
        job = _Job(payload["job_title"], payload["company"], payload["job_description"])
        return _do_contacts(job)
    else:
        raise ValueError(f"Unknown task type: {task_type}")


def _process_sqs_task(event, context):
    """Process an SQS message containing a task to execute."""
    # Ensure app startup has run (loads config, AI client, resumes)
    startup()

    batch_failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record["body"])
            task_id = body["task_id"]
            task_type = body["task_type"]
            user_id = body["user_id"]

            # Load payload from Supabase (stored by _enqueue_task)
            task_row = _db.client.table("pipeline_tasks").select("payload").eq("task_id", task_id).maybe_single().execute()
            if not task_row or not task_row.data or not task_row.data.get("payload"):
                raise ValueError(f"No payload found for task {task_id}")

            payload = task_row.data["payload"]
            _save_task(task_id, user_id, {"status": "running"})
            result = _dispatch_task(task_type, payload)
            _save_task(task_id, user_id, {"status": "done", "result": result})
        except Exception as e:
            logger.error("SQS task %s failed: %s", message_id, e, exc_info=True)
            try:
                _save_task(body.get("task_id", ""), body.get("user_id", ""),
                          {"status": "error", "error": str(e)})
            except Exception:
                logger.error("Failed to save error state for task %s", message_id)
            batch_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_failures}
```

- [ ] **Step 3: Update the three POST endpoints to use _enqueue_task**

Replace the tailor, cover-letter, and contacts endpoints (lines 474-503) with:

```python
@app.post("/api/tailor", status_code=202)
def tailor_job(req: TailorRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}")

    task_id = str(uuid.uuid4())
    payload = {
        "job_title": req.job_title,
        "company": req.company,
        "job_description": req.job_description,
        "resume_type": req.resume_type,
    }
    _enqueue_task(task_id, user.id, "tailor", payload)
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


@app.post("/api/cover-letter", status_code=202)
def cover_letter(req: CoverLetterRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}")

    task_id = str(uuid.uuid4())
    payload = {
        "job_title": req.job_title,
        "company": req.company,
        "job_description": req.job_description,
        "resume_type": req.resume_type,
    }
    _enqueue_task(task_id, user.id, "cover_letter", payload)
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


@app.post("/api/contacts", status_code=202)
def contacts(req: ContactsRequest, user: AuthUser = Depends(get_current_user)):
    task_id = str(uuid.uuid4())
    payload = {
        "job_title": req.job_title,
        "company": req.company,
        "job_description": req.job_description,
    }
    _enqueue_task(task_id, user.id, "contacts", payload)
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}
```

- [ ] **Step 4: Update the Lambda handler at the bottom of app.py**

Replace lines 798-802 with:

```python
# ---------------------------------------------------------------------------
# Lambda handler (Mangum for API Gateway, direct dispatch for SQS)
# ---------------------------------------------------------------------------

try:
    from mangum import Mangum
    _mangum = Mangum(app, api_gateway_base_path="/prod")
except ImportError:
    _mangum = None


def handler(event, context):
    """Lambda entry point — routes API Gateway events to Mangum, SQS events to worker."""
    if "Records" in event and event.get("Records", [{}])[0].get("eventSource") == "aws:sqs":
        return _process_sqs_task(event, context)
    if _mangum:
        return _mangum(event, context)
    raise RuntimeError("Mangum not installed and event is not SQS")
```

- [ ] **Step 5: Fix CORS middleware**

Replace lines 75-81:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://naukribaba.netlify.app", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 6: Verify local startup works**

Run: `python -c "from app import app; print('OK')"`
Expected: `OK` (no import errors)

- [ ] **Step 7: Commit**

```bash
git add app.py
git commit -m "feat: replace daemon-thread async with SQS queue, fix CORS origins"
```

---

## Task 3: Fix _do_tailor Crash + Drive Folder + pdf_url (app.py worker fixes)

**Files:**
- Modify: `app.py:424-462` (worker functions), `app.py:277-301` (Drive upload)

- [ ] **Step 1: Fix _do_tailor — guard empty tex_path, score failure indicator, pdf_url**

Replace the `_do_tailor` function (lines 424-450) with:

```python
def _do_tailor(job, base_tex, resume_type, company, job_title):
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = tailor_resume(job, base_tex, _ai_client, Path(tmpdir))
        if not tex_path or not Path(tex_path).exists():
            raise RuntimeError(f"Tailoring failed for {company} — AI returned empty result")
        tailored_tex = Path(tex_path).read_text()

        scoring_failed = False
        try:
            final_tex, scores = score_and_improve(tailored_tex, job, _ai_client)
        except Exception as e:
            logger.warning("score_and_improve failed: %s — using unscored resume", e)
            scores = {"ats_score": 0, "hiring_manager_score": 0, "tech_recruiter_score": 0}
            final_tex = tailored_tex
            scoring_failed = True

        final_tex_path = Path(tmpdir) / "final_resume.tex"
        final_tex_path.write_text(final_tex)
        pdf_path = compile_tex_to_pdf(str(final_tex_path), tmpdir)
        if not pdf_path:
            raise RuntimeError("LaTeX compilation failed")

        safe_name = f"{company}_{job_title}_resume.pdf".replace(" ", "_")
        drive_url = _upload_pdf_to_drive(pdf_path, safe_name)

        ats = scores.get("ats_score", 0)
        hm = scores.get("hiring_manager_score", 0)
        tr = scores.get("tech_recruiter_score", 0)
        return {
            "ats_score": ats, "hiring_manager_score": hm,
            "tech_recruiter_score": tr, "avg_score": round((ats + hm + tr) / 3),
            "drive_url": drive_url or "",
            "scoring_failed": scoring_failed,
        }
```

- [ ] **Step 2: Fix _do_cover_letter — same pdf_url fix**

Replace `_do_cover_letter` (lines 453-462) with:

```python
def _do_cover_letter(job, resume_tex, company, job_title):
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = generate_cover_letter(job, resume_tex, _ai_client, Path(tmpdir))
        pdf_path = compile_tex_to_pdf(tex_path, tmpdir)
        if not pdf_path:
            raise RuntimeError("LaTeX compilation failed")

        safe_name = f"{company}_{job_title}_cover_letter.pdf".replace(" ", "_")
        drive_url = _upload_pdf_to_drive(pdf_path, safe_name)
        return {"drive_url": drive_url or ""}
```

- [ ] **Step 3: Fix Drive folder hierarchy**

Replace `_upload_pdf_to_drive` (lines 277-301) with:

```python
def _upload_pdf_to_drive(pdf_path: str, filename: str) -> str:
    """Upload a PDF to Google Drive and return the shareable link."""
    drive_cfg = _config.get("google_drive", {})
    if not drive_cfg.get("enabled"):
        return ""
    try:
        from drive_uploader import _authenticate, _get_or_create_folder, _upload_file
        creds_path = drive_cfg.get("credentials_path", "google_credentials.json")
        if not Path(creds_path).exists() and os.environ.get("GOOGLE_CREDENTIALS_JSON"):
            creds_path = "/tmp/google_credentials.json"
            with open(creds_path, "w") as f:
                f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])
        service = _authenticate(creds_path)
        import datetime
        date_str = datetime.date.today().isoformat()
        root_id = drive_cfg.get("folder_id", "")
        # Build folder hierarchy one level at a time
        jh_id = _get_or_create_folder(service, "Job Hunt", root_id)
        date_id = _get_or_create_folder(service, date_str, jh_id)
        web_id = _get_or_create_folder(service, "web", date_id)
        url = _upload_file(service, pdf_path, web_id,
                          share_with=drive_cfg.get("share_with", ""))
        return url
    except Exception as e:
        logger.error("Drive upload failed: %s", e)
        return ""
```

- [ ] **Step 4: Verify no syntax errors**

Run: `python -c "from app import app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "fix: tailor crash on empty result, Drive folder hierarchy, remove temp pdf_url"
```

---

## Task 4: Backend Security + Data Fixes (db_client.py, app.py endpoints)

**Files:**
- Modify: `db_client.py:114-117` (delete_resume), `db_client.py:197-209` (update_job_status), `db_client.py:160-195` (get_jobs)
- Modify: `app.py:739-745` (delete endpoint), `app.py:554-561` (update_profile), `app.py:576-584` (field map), `app.py:642-643` (jobs response)

- [ ] **Step 1: Fix IDOR — add user_id to delete_resume**

In `db_client.py`, replace `delete_resume` (lines 114-117):

```python
    def delete_resume(self, resume_id: str, user_id: str) -> None:
        """Delete a resume by primary key, scoped to the owning user."""
        result = (
            self.client.table("user_resumes")
            .delete()
            .eq("id", resume_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            raise ValueError(f"Resume {resume_id} not found for user")
        logger.info(f"[DB] Deleted resume {resume_id} for user {user_id}")
```

In `app.py`, update the delete endpoint (lines 739-745):

```python
@app.delete("/api/resumes/{resume_id}")
def delete_resume(resume_id: str, user: AuthUser = Depends(get_current_user)):
    """Delete a resume owned by the authenticated user."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    try:
        _db.delete_resume(resume_id, user.id)
    except ValueError:
        raise HTTPException(404, "Resume not found")
    return {"status": "deleted"}
```

- [ ] **Step 2: Fix update_job_status IndexError**

In `db_client.py`, replace `update_job_status` (lines 197-209):

```python
    def update_job_status(
        self, user_id: str, job_id: str, status: str
    ) -> Dict[str, Any]:
        """Update a job's application status."""
        result = (
            self.client.table("jobs")
            .update({"application_status": status})
            .eq("job_id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            raise ValueError(f"Job {job_id} not found for user {user_id}")
        logger.info(f"[DB] Job {job_id} status -> {status}")
        return result.data[0]
```

In `app.py`, update the endpoint (around line 662):

```python
    try:
        result = _db.update_job_status(user.id, job_id, status)
    except ValueError:
        raise HTTPException(404, "Job not found")
    return result
```

- [ ] **Step 3: Fix update_user None guard**

In `app.py`, replace lines 554-561 in `update_profile`:

```python
    row = _db.update_user(user.id, update_data)
    if row is None:
        raise HTTPException(404, "User not found")
    return ProfileResponse(
        id=row["id"],
        email=row["email"],
        full_name=row.get("name"),
        plan=row.get("plan", "free"),
        created_at=row.get("created_at"),
    )
```

- [ ] **Step 4: Add total count to paginated jobs**

In `db_client.py`, replace `get_jobs` (lines 160-195):

```python
    def get_jobs(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Get paginated jobs for a user with optional filters.

        Returns (jobs_list, total_count).
        """
        query = (
            self.client.table("jobs")
            .select("*", count="exact")
            .eq("user_id", user_id)
        )

        if filters:
            if "source" in filters:
                query = query.eq("source", filters["source"])
            if "min_score" in filters:
                query = query.gte("match_score", filters["min_score"])
            if "status" in filters:
                query = query.eq("application_status", filters["status"])
            if "company" in filters:
                query = query.eq("company", filters["company"])

        offset = (page - 1) * per_page
        query = query.order("first_seen", desc=True).range(offset, offset + per_page - 1)

        result = query.execute()
        total = result.count if result.count is not None else len(result.data)
        return result.data, total
```

In `app.py`, update `get_dashboard_jobs` (around line 642):

```python
    jobs, total = _db.get_jobs(user.id, filters=filters, page=page, per_page=per_page)
    return {"jobs": jobs, "page": page, "per_page": per_page, "total": total}
```

- [ ] **Step 5: Add search_queries to _FIELD_MAP**

In `app.py`, add `"search_queries"` to `_FIELD_MAP` (line 577):

```python
    _FIELD_MAP = {
        "queries": "queries", "keywords": "queries", "job_titles": "queries",
        "search_queries": "queries",
        "locations": "locations",
        "geo_regions": "geo_regions",
        "experience_levels": "experience_levels", "experience_level": "experience_levels",
        "days_back": "days_back",
        "max_jobs_per_run": "max_jobs_per_run",
        "min_match_score": "min_match_score", "min_score": "min_match_score",
    }
```

- [ ] **Step 6: Verify**

Run: `python -c "from app import app; from db_client import SupabaseClient; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app.py db_client.py
git commit -m "fix: IDOR on resume delete, job status 404, profile None guard, pagination total, field map"
```

---

## Task 5: Backend Module Fixes (quality_logger, contact_finder, resume_scorer, main.py)

**Files:**
- Modify: `quality_logger.py:14`
- Modify: `contact_finder.py:304-330`, `contact_finder.py:424-426`
- Modify: `resume_scorer.py:55-61`, `resume_scorer.py:498-505`
- Modify: `main.py:114-135`, `main.py:1120-1134`

- [ ] **Step 1: Fix quality_logger Lambda path**

In `quality_logger.py`, replace the `LOG_PATH` line (around line 14):

```python
LOG_PATH = (
    Path("/tmp/ai_quality_log.jsonl")
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    else Path("output/ai_quality_log.jsonl")
)
```

Add `import os` at the top if not already present.

- [ ] **Step 2: Fix contact_finder seniority prompt**

In `contact_finder.py`, find the line in `CONTACT_SYSTEM_PROMPT` (around line 310) that says `"hiring manager"` and replace with:

```
"direct hiring manager or team lead at the appropriate level for the role (e.g., Engineering Manager for mid-level roles, NOT VP/Director/Head-of unless the position reports directly to them)"
```

The exact line will be in the prompt template that specifies 3 contact types. Change `"hiring manager, peer, recruiter"` to:
```
"direct hiring manager/team lead (at the candidate's reporting level, NOT VP/Director), peer (someone in a similar role), recruiter (internal recruiter at the company)"
```

- [ ] **Step 3: Fix contact message length guard**

In `contact_finder.py`, find the line (around line 424-426):

```python
if len(message) > 300:
    message = message[:297] + "..."
```

Replace with:

```python
if len(message) > 280:
    message = message[:277] + "..."
```

- [ ] **Step 4: Fix fabrication detection prompt**

In `resume_scorer.py`, find the fabrication instruction in the scoring prompt (around lines 55-61). Add this clarification:

```
"IMPORTANT: Only flag fabrication_detected=true for outright invented companies, degrees, or certifications that do not appear in the original resume. Rephrasing existing experience (e.g., 'led' vs 'contributed to', quantifying existing achievements) is resume optimization, NOT fabrication."
```

- [ ] **Step 5: Remove extra score_resume call after loop**

In `resume_scorer.py`, find the `_score_and_improve_latex` function (around line 498-505). After the `for` loop, there's:

```python
final_scores = score_resume(current_tex, job, ai_client)
```

Replace with:

```python
# Reuse scores from last loop iteration (no extra API call)
final_scores = last_scores if last_scores else score_resume(current_tex, job, ai_client)
```

And at the top of the loop, before the score call, add `last_scores = None`. Inside the loop after scoring, set `last_scores = scores`.

Do the same for `_score_and_improve_text` if it has the same pattern.

- [ ] **Step 6: Add tailoring_model to _job_to_supabase_row**

In `main.py`, find `_job_to_supabase_row` (around lines 114-135). Add to the returned dict:

```python
        "tailoring_model": getattr(job, "tailoring_model", None) or "",
```

- [ ] **Step 7: Add Drive URLs to Step 8d upsert**

In `main.py`, find the Step 8d upsert (around lines 1120-1134). After the existing conditional fields (`resume_doc_url`, `resume_s3_url`, `cover_letter_s3_url`), add:

```python
        if getattr(job, "resume_drive_url", None):
            row["resume_drive_url"] = job.resume_drive_url
        if getattr(job, "cover_letter_drive_url", None):
            row["cover_letter_drive_url"] = job.cover_letter_drive_url
```

- [ ] **Step 8: Verify**

Run: `python -c "import quality_logger; import contact_finder; import resume_scorer; import main; print('OK')"`
Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add quality_logger.py contact_finder.py resume_scorer.py main.py
git commit -m "fix: Lambda log path, contact seniority, fabrication threshold, drive URLs, tailoring_model"
```

---

## Task 6: GradIreland Company Extraction

**Files:**
- Modify: `scrapers/gradireland_scraper.py:164-228`

- [ ] **Step 1: Investigate GradIreland HTML structure**

Run: `python -c "
import requests
r = requests.get('https://gradireland.com/graduate-jobs', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
print(r.status_code)
print(r.text[:3000])
"`

This will show the HTML structure to determine the correct CSS selector for company names.

- [ ] **Step 2: Fix company extraction in fallback strategies**

Based on the HTML inspection, update `_extract_nearby` or add direct extraction in Strategies 2 and 3. The fix will depend on the actual HTML structure found in Step 1.

Common patterns on GradIreland:
- `<span class="employer-name">Company Name</span>`
- `<a class="company-link" href="...">Company Name</a>`
- JSON-LD `hiringOrganization.name` (already works in Strategy 1)

If HTML selectors are unreliable, use the job detail page: each job's `apply_url` leads to a detail page where the company name is more reliably placed.

- [ ] **Step 3: Commit**

```bash
git add scrapers/gradireland_scraper.py
git commit -m "fix: extract company name from GradIreland fallback strategies"
```

---

## Task 7: Frontend — api.js + auth Fixes

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/auth/useAuth.js`

- [ ] **Step 1: Add null-supabase guard to authHeaders**

In `web/src/api.js`, replace `authHeaders` (lines 5-12):

```javascript
async function authHeaders() {
  if (!supabase) return { 'Content-Type': 'application/json' }
  const { data: { session } } = await supabase.auth.getSession()
  const headers = { 'Content-Type': 'application/json' }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }
  return headers
}
```

- [ ] **Step 2: Add progress callback to pollTask**

Replace `pollTask` (lines 34-46):

```javascript
async function pollTask(pollUrl, { intervalMs = 2000, maxWaitMs = 240000, onProgress } = {}) {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, intervalMs));
    const headers = await authHeaders();
    const res = await fetch(`${API_BASE}${pollUrl}`, { method: 'GET', headers });
    if (!res.ok) throw new Error(`Poll failed: HTTP ${res.status}`);
    const task = await res.json();
    if (onProgress) onProgress(task.status);
    if (task.status === 'done') return task.result;
    if (task.status === 'error') throw new Error(task.error || 'Task failed');
  }
  throw new Error('Task timed out — please try again');
}
```

Update `apiCall` to pass through options (replace lines 14-32):

```javascript
export async function apiCall(endpoint, body, options = {}) {
  const headers = await authHeaders()
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();

  if (data.task_id && data.poll_url) {
    return pollTask(data.poll_url, options);
  }
  return data;
}
```

- [ ] **Step 3: Fix OAuth redirect**

In `web/src/auth/useAuth.js`, find the `signIn` function and change:

```javascript
options: { redirectTo: window.location.origin },
```

to:

```javascript
options: { redirectTo: window.location.href },
```

- [ ] **Step 4: Verify build**

Run: `cd web && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 5: Commit**

```bash
git add web/src/api.js web/src/auth/useAuth.js
git commit -m "fix: null-supabase guard, poll progress callback, OAuth redirect preserves path"
```

---

## Task 8: Frontend — Settings Page Fixes

**Files:**
- Modify: `web/src/pages/Settings.jsx`

- [ ] **Step 1: Add auth guard and data loading**

At the top of the `Settings` component function, add an auth guard and useEffect for loading:

Find `export default function Settings()` and add after the existing `useState` calls:

```jsx
  // Auth guard
  if (!loading && !user) return <LoginPage />

  // Load existing data on mount
  useEffect(() => {
    if (!user) return
    async function loadData() {
      try {
        const profile = await apiGet('/api/profile')
        setProfile(prev => ({
          ...prev,
          full_name: profile.full_name || '',
          email: profile.email || '',
          linkedin_url: profile.linkedin_url || '',
          github_url: profile.github_url || '',
          phone: profile.phone || '',
        }))
      } catch (e) { console.warn('Failed to load profile:', e) }
      try {
        const config = await apiGet('/api/search-config')
        if (config && Object.keys(config).length > 0) {
          setPrefs(prev => ({
            ...prev,
            queries: config.queries || prev.queries,
            locations: config.locations || prev.locations,
            experience_levels: config.experience_levels || prev.experience_levels,
            days_back: config.days_back || prev.days_back,
            max_jobs_per_run: config.max_jobs_per_run || prev.max_jobs_per_run,
            min_match_score: config.min_match_score || prev.min_match_score,
          }))
        }
      } catch (e) { console.warn('Failed to load search config:', e) }
    }
    loadData()
  }, [user])
```

Add `import { apiGet } from '../api'` at the top if not already imported.
Add `import LoginPage from './LoginPage'` if not already imported.

- [ ] **Step 2: Fix setState during render**

Find the block (around lines 546-549):

```jsx
if (user?.email && !profile.email) {
  setProfile((prev) => ({ ...prev, email: user.email }))
}
```

Remove it entirely — the `useEffect` in Step 1 handles loading the email.

- [ ] **Step 3: Fix field name — search_queries → queries**

In the `PreferencesSection` save handler, find where `search_queries` is used as the key name in the prefs object and replace with `queries`. Search for `search_queries` in the file and replace all occurrences with `queries`.

- [ ] **Step 4: Add resume listing**

Find the `ResumeSection` placeholder text "Resume listing will appear here" and replace with actual API call:

```jsx
// In ResumeSection, add state and effect:
const [resumes, setResumes] = useState([])

useEffect(() => {
  async function loadResumes() {
    try {
      const data = await apiGet('/api/resumes')
      setResumes(data.resumes || [])
    } catch (e) { console.warn('Failed to load resumes:', e) }
  }
  loadResumes()
}, [])

// In the render, replace placeholder with:
{resumes.length === 0 ? (
  <p className="text-gray-500 text-sm">No resumes uploaded yet.</p>
) : (
  <ul className="space-y-2">
    {resumes.map(r => (
      <li key={r.id} className="flex items-center justify-between p-2 bg-gray-800 rounded">
        <span className="text-sm">{r.resume_key || r.filename || 'Resume'}</span>
        <button onClick={() => handleDeleteResume(r.id)} className="text-red-400 text-xs hover:text-red-300">Delete</button>
      </li>
    ))}
  </ul>
)}
```

- [ ] **Step 5: Verify build**

Run: `cd web && npm run build`
Expected: Build succeeds

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/Settings.jsx
git commit -m "fix: Settings loads data on mount, auth guard, field name, resume listing"
```

---

## Task 9: Frontend — Dashboard Fixes

**Files:**
- Modify: `web/src/pages/Dashboard.jsx`

- [ ] **Step 1: Fix filter re-fetch — make dropdowns live**

In the `fetchJobs` useCallback dependency array (around line 57), add the filter state variables:

```javascript
}, [filterVersion, page, statusFilter, sourceFilter, minScore, companySearch])
```

This makes every filter change trigger an immediate re-fetch. Remove the `handleFilterApply` function and the "Apply Filters" button since all controls are now live.

- [ ] **Step 2: Fix pagination to use total from API**

Replace the `totalPages` calculation (around line 91):

```javascript
const totalPages = Math.max(1, Math.ceil((total || jobs.length) / perPage));
```

And in the `fetchJobs` callback, destructure `total` from the response:

```javascript
const data = await apiGet(`/api/dashboard/jobs?page=${page}&per_page=${perPage}${params}`)
setJobs(data.jobs || [])
setTotal(data.total || data.jobs?.length || 0)
```

Add `const [total, setTotal] = useState(0)` to the state declarations.

- [ ] **Step 3: Fix score slider trigger**

In the score range slider's `onMouseUp`/`onTouchEnd`, remove the `setPage(1)` call — it's now handled automatically by the dependency array change in Step 1.

- [ ] **Step 4: Verify build**

Run: `cd web && npm run build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Dashboard.jsx
git commit -m "fix: live dashboard filters, pagination uses API total count"
```

---

## Task 10: Frontend — Component Fixes (ContactsCard, JobTable, ScoreBadge, Onboarding)

**Files:**
- Modify: `web/src/components/ContactsCard.jsx`
- Modify: `web/src/components/JobTable.jsx`
- Modify: `web/src/components/ScoreBadge.jsx`
- Modify: `web/src/pages/Onboarding.jsx`

- [ ] **Step 1: Fix ContactsCard link label + clipboard**

In `web/src/components/ContactsCard.jsx`, replace lines 7 and 13-14:

```jsx
// Line 7: guard clipboard
const copy = (text) => navigator.clipboard.writeText(text || '')

// Lines 13-14: correct link label
const linkUrl = contact.profile_url || contact.google_url || contact.search_url;
const linkLabel = contact.profile_url ? 'View Profile' : contact.google_url ? 'Find on Google' : 'Search LinkedIn';
```

- [ ] **Step 2: Fix JobTable duplicate asset icons**

In `web/src/components/JobTable.jsx`, find the resume asset icons section (around lines 266-278). The Google Doc icon should only render when `resume_doc_url` exists AND is different from `resume_s3_url`:

```jsx
{job.resume_doc_url && job.resume_doc_url !== job.resume_s3_url && (
  // Google Doc icon
)}
```

- [ ] **Step 3: Fix ScoreBadge null/zero guard**

In `web/src/components/ScoreBadge.jsx`, add at the top of the component:

```jsx
export default function ScoreBadge({ score, label }) {
  if (score == null || score === 0) return <span className="text-gray-500 text-sm">--</span>
  // ... rest of existing component
```

- [ ] **Step 4: Fix Onboarding — setState, field name, alert**

In `web/src/pages/Onboarding.jsx`:

a) Find the same `if (user?.email && !profile.email)` pattern (around line 527-529) and wrap it in a `useEffect`:

```jsx
useEffect(() => {
  if (user?.email && !profile.email) {
    setProfile(prev => ({ ...prev, email: user.email }))
  }
}, [user])
```

b) Replace `search_queries` with `queries` in the prefs object.

c) Replace `alert('Setup complete!...')` (around line 567) with inline state:

```jsx
setSuccess(true)
```

And add `const [success, setSuccess] = useState(false)` to state, render a success banner when true:

```jsx
{success && (
  <div className="bg-emerald-900/50 border border-emerald-500 rounded p-3 text-emerald-200 text-sm">
    Setup complete! Your preferences have been saved.
  </div>
)}
```

- [ ] **Step 5: Verify build**

Run: `cd web && npm run build`
Expected: Build succeeds

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ContactsCard.jsx web/src/components/JobTable.jsx web/src/components/ScoreBadge.jsx web/src/pages/Onboarding.jsx
git commit -m "fix: contacts label, asset icons, score badge null guard, onboarding setState"
```

---

## Task 11: Database Migration — Add payload column

**Files:**
- New: `scripts/add_payload_column.sql`

- [ ] **Step 1: Create migration script**

Create `scripts/add_payload_column.sql`:

```sql
-- Add payload column to pipeline_tasks for SQS worker pattern
-- The payload stores the full task input (JD text, resume type, etc.)
-- so the SQS message only needs to carry the task_id
ALTER TABLE pipeline_tasks ADD COLUMN IF NOT EXISTS payload JSONB;
```

- [ ] **Step 2: Run migration against Supabase**

Run this SQL in the Supabase dashboard (SQL Editor) or via the CLI. The migration is idempotent (`IF NOT EXISTS`).

- [ ] **Step 3: Commit**

```bash
git add scripts/add_payload_column.sql
git commit -m "db: add payload column to pipeline_tasks for SQS worker"
```

---

## Task 12: Deploy + Validate

- [ ] **Step 1: Build and deploy backend**

Run: `sam build`

If build succeeds:
Run: `sam deploy --stack-name job-hunt-api --region eu-west-1 --capabilities CAPABILITY_IAM --image-repository 339712966843.dkr.ecr.eu-west-1.amazonaws.com/job-hunt-api`

- [ ] **Step 2: Build and deploy frontend**

Run: `cd web && npm run build && npx netlify deploy --prod`

- [ ] **Step 3: Verify SQS queue exists**

Run: `aws sqs get-queue-url --queue-name job-hunt-api-tasks --region eu-west-1`
Expected: Returns the queue URL

- [ ] **Step 4: Test tailor endpoint E2E**

Use curl or the frontend to POST to `/api/tailor`. Verify:
- Returns 202 with `task_id` and `poll_url`
- SQS message appears in the queue (check CloudWatch)
- Worker Lambda processes the message
- `GET /api/tasks/{task_id}` returns `{status: "done", result: {...}}`
- Drive URL is present in the result

- [ ] **Step 5: Test settings save/load roundtrip**

1. Open `https://naukribaba.netlify.app/settings`
2. Verify existing data loads
3. Change search queries → save → reload → verify persistence

- [ ] **Step 6: Test dashboard filters**

1. Open dashboard
2. Change status dropdown → verify jobs re-fetch immediately
3. Verify pagination shows correct total
4. Verify contacts show correct link labels

- [ ] **Step 7: Test resume delete authorization**

Attempt to delete a resume via API with a different user's JWT — should return 404.

- [ ] **Step 8: Commit validation notes**

```bash
git add -A
git commit -m "chore: Phase 1 completion — all 35 fixes deployed and validated"
```

---

## Summary

| Task | Files | Bug IDs Fixed |
|------|-------|---------------|
| 1 | template.yaml | SQS infra, CORS (B1) |
| 2 | app.py (async) | Lambda thread death, SQS routing |
| 3 | app.py (workers) | B2, B3/B14, B17, scoring_failed |
| 4 | app.py + db_client.py | B4, B5, B6, B7/B15, B8 |
| 5 | quality_logger, contact_finder, resume_scorer, main.py | B9, B10, B11, B12, B18, B19 |
| 6 | gradireland_scraper.py | B13 |
| 7 | api.js, useAuth.js | F12, F15, F16 |
| 8 | Settings.jsx | F1, F2, F3, F5, F14 |
| 9 | Dashboard.jsx | F18, F19, F20 |
| 10 | ContactsCard, JobTable, ScoreBadge, Onboarding | F3, F6, F7, F9, F17, F22 |
| 11 | SQL migration | pipeline_tasks.payload |
| 12 | Deploy + validate | E2E verification |
