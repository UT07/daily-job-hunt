# Auto-Apply — Design Spec

**Date**: 2026-04-08
**Status**: Approved — full product, not MVP
**Phase**: 3.4 Apply
**Approach**: Remote browser session with persistent logins

---

## Problem

User has 300+ matched jobs but can't apply manually to all of them. Each application takes 5-15 minutes of repetitive form filling. The pipeline generates tailored resumes and cover letters but there's no way to use them to actually apply.

## Solution

Embedded remote browser session in the NaukriBaba web app. Playwright runs a real Chrome on AWS Fargate, streams the view into the Apply tab, pre-fills forms with AI-generated answers, and lets the user handle CAPTCHAs/auth while reviewing and submitting.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  NaukriBaba Web App (React)                      │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │  Apply Tab (JobWorkspace)                    │ │
│  │                                              │ │
│  │  ┌─────────────────────────────────────────┐ │ │
│  │  │  Remote Browser View (WebSocket stream) │ │ │
│  │  │                                         │ │ │
│  │  │  [Live view of application form]        │ │ │
│  │  │  [Fields pre-filled by Playwright]      │ │ │
│  │  │  [User can click/type/solve CAPTCHAs]   │ │ │
│  │  │                                         │ │ │
│  │  └─────────────────────────────────────────┘ │ │
│  │                                              │ │
│  │  Status: Ready to submit                     │ │
│  │  [Edit Answers]  [Submit Application]        │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────┬──────────────────────────┘
                       │ WebSocket
                       ▼
┌─────────────────────────────────────────────────┐
│  Fargate Task (Persistent Chrome Session)        │
│                                                  │
│  Playwright + Chrome (non-headless)              │
│  ├── Session cookies persisted across applies    │
│  ├── Bright Data proxy for anti-bot              │
│  ├── Screenshot stream → WebSocket → frontend    │
│  ├── Click/type events ← WebSocket ← frontend   │
│  └── Form detection + AI fill pipeline           │
│                                                  │
│  Session lifecycle:                              │
│  - Created on first "Apply" click                │
│  - Reused for all applies in same dashboard      │
│  - Warm timeout: 30 min idle → shut down         │
│  - Platform logins persist within session         │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  NaukriBaba API (Lambda)                         │
│                                                  │
│  POST /api/apply/start-session                   │
│  POST /api/apply/fill-form                       │
│  POST /api/apply/submit                          │
│  GET  /api/apply/session-status                  │
│  POST /api/apply/close-session                   │
│                                                  │
│  For Greenhouse/Ashby: direct API submission     │
│  (skip Fargate, faster, no browser needed)       │
└─────────────────────────────────────────────────┘

## Infrastructure (All SAM — No External Services)

```
┌─────────────────────────────────────────────────────────┐
│  SAM Template Additions                                  │
│                                                          │
│  ┌─ WebSocket API Gateway ────────────────────────────┐  │
│  │  wss://xxxxx.execute-api.eu-west-1.amazonaws.com   │  │
│  │                                                    │  │
│  │  Routes:                                           │  │
│  │  $connect    → ConnectHandler Lambda               │  │
│  │  $disconnect → DisconnectHandler Lambda            │  │
│  │  browser     → BrowserCommandHandler Lambda        │  │
│  │                                                    │  │
│  │  Auth: JWT from query string (?token=xxx)          │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Fargate Chrome Task ──────────────────────────────┐  │
│  │  Docker: Dockerfile.playwright (already exists)     │  │
│  │  Image: ECR (already exists)                        │  │
│  │                                                    │  │
│  │  Entrypoint: browser_session.py                    │  │
│  │  ├── Launches Chrome via Playwright                │  │
│  │  ├── Connects to WebSocket API Gateway             │  │
│  │  ├── Streams screenshots (JPEG, 5fps)              │  │
│  │  ├── Receives click/type events                    │  │
│  │  ├── Runs form detection + auto-fill               │  │
│  │  └── Persists cookies in /tmp (EFS optional)       │  │
│  │                                                    │  │
│  │  Networking:                                       │  │
│  │  ├── VPC with public subnet (for Bright Data)      │  │
│  │  ├── Security group: outbound 443 only             │  │
│  │  └── No inbound (Fargate connects OUT to WSS)      │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Session Management ───────────────────────────────┐  │
│  │  DynamoDB Table: browser_sessions                   │  │
│  │  ├── session_id (PK)                               │  │
│  │  ├── user_id                                       │  │
│  │  ├── fargate_task_arn                              │  │
│  │  ├── ws_connection_id (frontend)                   │  │
│  │  ├── ws_connection_id_browser (Fargate)            │  │
│  │  ├── platform_cookies (encrypted)                  │  │
│  │  ├── status: starting|ready|filling|submitting     │  │
│  │  ├── created_at                                    │  │
│  │  └── ttl (auto-expire after 30 min idle)           │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ New Lambdas ──────────────────────────────────────┐  │
│  │  naukribaba-ws-connect       (WebSocket $connect)  │  │
│  │  naukribaba-ws-disconnect    (WebSocket $disconnect)│  │
│  │  naukribaba-ws-command       (WebSocket browser)   │  │
│  │  naukribaba-submit-application (Easy Apply API)    │  │
│  │  naukribaba-start-browser    (Fargate task launch) │  │
│  │  naukribaba-generate-answers (AI custom Q answers) │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Data Flow: Remote Browser Session

```
Frontend (React)                  API Gateway              Fargate Chrome
     │                               │                         │
     │─── WSS connect ──────────────→│                         │
     │                               │── store connection_id──→│ DynamoDB
     │                               │                         │
     │─── POST /apply/start ────────→│                         │
     │                               │── RunTask ─────────────→│ ECS
     │                               │                         │
     │                               │                   Chrome starts
     │                               │                   Navigates to URL
     │                               │                   Detects form fields
     │                               │                         │
     │                               │←── screenshot (JPEG) ──│ WSS
     │←── screenshot frame ─────────│                         │
     │    (renders in Apply tab)     │                         │
     │                               │                         │
     │                               │←── fields extracted ───│
     │←── field list ───────────────│                         │
     │    AI generates answers       │                         │
     │                               │                         │
     │─── fill_form {answers} ─────→│                         │
     │                               │── fill command ────────→│
     │                               │                   Playwright fills
     │                               │                         │
     │←── screenshot (filled) ──────│←── screenshot ──────────│
     │    User reviews               │                         │
     │                               │                         │
     │─── submit ──────────────────→│                         │
     │                               │── click submit ────────→│
     │                               │                   Clicks real button
     │                               │←── confirmation ───────│
     │←── applied! ─────────────────│                         │
```

### Why This Works Without External Services

- **WebSocket API Gateway**: AWS-managed, serverless, pay-per-message (~$1/million messages). Already supported in SAM via `AWS::ApiGatewayV2::Api` with `ProtocolType: WEBSOCKET`.
- **Fargate**: we already have the task definition and Docker image. Just need to run it.
- **DynamoDB**: serverless session store. Free tier covers our usage. Auto-TTL for cleanup.
- **No ALB needed**: Fargate connects OUTBOUND to WebSocket API Gateway (not inbound). The browser pushes screenshots to the WSS endpoint, frontend connects to the same endpoint. API Gateway routes messages between them.
- **No Browserbase**: we own the entire stack. Zero per-session API costs.

### Cost: ~$0.00-0.02 per application session

| Component | Cost |
|-----------|------|
| WebSocket API Gateway | $1.14/million messages (~$0.001/session) |
| Fargate (0.25 vCPU, 0.5GB, 5 min) | ~$0.01/session |
| DynamoDB | Free tier (25 WCU/RCU) |
| Lambda (WS handlers) | Free tier |
| **Total per browser session** | **~$0.01** |
| Easy Apply (no Fargate) | **~$0.001** |
```

## Three Submission Modes

### Mode 1: NaukriBaba Easy Apply (Greenhouse/Ashby)
Like LinkedIn Easy Apply — minimal friction, one-click with quick review.

**UX Flow:**
1. Job card in dashboard shows green "⚡ Easy Apply" badge (for Greenhouse/Ashby jobs)
2. User clicks "⚡ Easy Apply" → compact modal appears (NOT a full page)
3. Modal shows: tailored resume preview, cover letter preview, 2-3 custom questions pre-answered by AI
4. User reviews for 5 seconds, edits if needed
5. Clicks "Submit Application" → backend POSTs to Greenhouse/Ashby API → done
6. Badge changes to "✓ Applied" with timestamp
7. Total time: **<10 seconds per application**

**Why "Easy Apply" matters:**
- No browser session needed, no Fargate cost
- Feels instant — removes the friction that stops people from applying
- Covers Stripe, MongoDB, Twilio, Anthropic, Linear, Notion, Vercel, Datadog, etc.
- Can batch-apply: "Easy Apply to all 8 S-tier Greenhouse jobs" with a per-job confirmation step

**API Details:**
- Greenhouse: `POST https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}`
- Ashby: `POST https://api.ashbyhq.com/posting-api/application`
- Fields: name, email, phone, resume (multipart file from S3), cover letter, custom answers
- Custom questions fetched from job metadata, answered by AI, editable by user
- `posting_id` and `board_token`/`company_slug` already stored by scrapers

### Mode 2: Remote Browser (LinkedIn, Workday, Custom Forms)
For job sites without application APIs — full browser session.

- Fargate Chrome navigates to apply URL
- Playwright detects form fields (labels, types, options)
- AI generates answers for each field
- Playwright pre-fills all fields + uploads resume PDF
- Browser view streamed to Apply tab via WebSocket
- User reviews, solves CAPTCHA if needed, clicks Submit
- Screenshot captured as proof of submission
- ~30-60 seconds per application

### Mode 3: Assisted Manual Apply (fallback)
For sites that block automation or require complex multi-step flows.

- NaukriBaba generates all answers and copies them to clipboard
- Opens the real application page in a new tab
- User pastes answers manually but doesn't have to think/write
- Dashboard tracks that user clicked "Apply" and prompts for outcome later
- ~2-3 minutes but user does zero writing

## Session Management

### Per-Platform Login Persistence
```
Platform Sessions (within one dashboard session):
├── LinkedIn: logged in after first apply, reused for all LinkedIn jobs
├── Workday: logged in after first apply, reused for all Workday jobs
├── Greenhouse: no login needed (public application forms)
├── Ashby: no login needed (public application forms)
└── Custom: login persists per domain
```

### Session Lifecycle
1. **Create**: First "Apply" click → Fargate ECS task starts with Chrome
2. **Reuse**: Subsequent applies use same task (cookies preserved)
3. **Warm idle**: 30 min inactivity → task auto-terminates
4. **Extend**: Any apply resets the 30 min timer
5. **Close**: User closes dashboard or clicks "End Session"

### Batch Easy Apply
For Greenhouse/Ashby jobs (Easy Apply mode), batch is natural:
- Dashboard shows "⚡ Easy Apply to 8 S-tier jobs" button
- Compact modal cycles through each job:
  - Shows: company, title, score, resume preview, custom Qs
  - User clicks "Submit" or "Skip" for each
  - 5-10 seconds per job
- Total: 8 applications in ~60-90 seconds
- Progress bar: "Applied 5/8 — [Pause] [Stop]"
- Each application tracked individually in `applications` table

## Form Detection & AI Filling

### Field Extraction
Playwright extracts form structure:
```json
{
  "fields": [
    {"label": "Full Name", "type": "text", "required": true, "id": "name"},
    {"label": "Email", "type": "email", "required": true, "id": "email"},
    {"label": "Resume", "type": "file", "required": true, "id": "resume"},
    {"label": "Cover Letter", "type": "textarea", "required": false},
    {"label": "Are you authorized to work in Ireland?", "type": "select", "options": ["Yes", "No"]},
    {"label": "Years of Python experience", "type": "text", "required": true},
    {"label": "Why do you want to work at [Company]?", "type": "textarea", "required": true}
  ]
}
```

### Artifact Retrieval from S3

Resume and cover letter PDFs live in S3. Both submission paths need them:

**Easy Apply (API):** Lambda downloads PDF from S3 → sends as multipart file to Greenhouse/Ashby API.
```python
# Lambda has direct S3 access — no presigned URL needed
s3 = boto3.client("s3")
pdf_bytes = s3.get_object(Bucket="utkarsh-job-hunt", Key=job.resume_s3_key)["Body"].read()
# Multipart upload to Greenhouse
files = {"resume": ("resume.pdf", pdf_bytes, "application/pdf")}
requests.post(greenhouse_url, data=form_fields, files=files)
```

**Remote Browser (Playwright):** Fargate downloads PDF from S3 → saves to /tmp → Playwright uploads via file input.
```python
# Fargate task has S3 access via IAM role
s3.download_file("utkarsh-job-hunt", job.resume_s3_key, "/tmp/resume.pdf")
# Playwright uploads to form
page.locator("input[type=file]").set_input_files("/tmp/resume.pdf")
```

**Key requirement:** `resume_s3_key` must be stored in the `jobs` table (not just the presigned URL which expires). We added this column on Apr 8 — SaveJob now stores it. Older jobs need the key extracted from their presigned URL.

**Cover letter handling:** Same pattern. For Greenhouse/Ashby, cover letter text is often a textarea (not file upload). We paste the generated cover letter text directly. For file upload forms, same S3 download → upload flow.

### AI Answer Generation
Standard fields filled from user profile:
- Name → user.name
- Email → user.email
- Phone → user.phone
- Resume → tailored PDF from S3
- Cover letter → generated cover letter text
- LinkedIn → user.linkedin
- GitHub → user.github
- Location → user.location
- Work authorization → "Yes" (Stamp 1G visa)

Custom questions filled by AI:
```
Prompt: Answer this job application question for {user.name}, applying to {job.title} at {job.company}.

Candidate context:
- 3 years at Clover IT Services (SRE/Backend, AWS, Python, React)
- MSc Cloud Computing from ATU (completed)
- Based in Dublin, Ireland (Stamp 1G work authorization)
- Key skills: {job.key_matches}

Question: {field.label}
Field type: {field.type}
{f"Options: {field.options}" if field.options else ""}

Answer concisely and truthfully. For yes/no questions about work authorization in Ireland, answer "Yes".
For years of experience questions, calculate from resume dates.
For "why this company" questions, reference specific things about the company from the JD.
```

### Easy Apply Modal (Mode 1 — Greenhouse/Ashby)
```
┌──────────────────────────────────────────────┐
│  ⚡ Easy Apply                          [✕]  │
│                                              │
│  Senior SRE @ Stripe              Score: 94  │
│  Dublin, Ireland                             │
│                                              │
│  ┌─ Your Application ─────────────────────┐  │
│  │  📄 Resume: Utkarsh_Singh_SRE_Stripe ▼ │  │
│  │  📝 Cover Letter: [Preview]       [▼]  │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌─ Quick Questions ──────────────────────┐  │
│  │  Work authorized in Ireland? [Yes ▼]   │  │
│  │  Kubernetes experience?      [3 yrs]   │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  [Skip]              [⚡ Submit Application] │
│                                              │
│  Applying as: Utkarsh Singh                  │
│  254utkarsh@gmail.com · +353 892515620       │
└──────────────────────────────────────────────┘
```

### Apply Tab (Mode 2 — Remote Browser)
```
┌─────────────────────────────────────────────────┐
│  Apply to: DevOps Engineer @ Microsoft           │
│  Via: Remote Browser (LinkedIn)                  │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │                                             │ │
│  │  [Live browser view — LinkedIn Easy Apply]  │ │
│  │  [Fields pre-filled, user can interact]     │ │
│  │  [CAPTCHA? User solves it here]             │ │
│  │                                             │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  Status: ✅ All fields filled                    │
│  [Take Screenshot]  [Submit — click in browser]  │
└─────────────────────────────────────────────────┘
```

### Assisted Apply Panel (Mode 3 — Fallback)
```
┌─────────────────────────────────────────────────┐
│  Apply to: Platform Engineer @ Workday           │
│  Via: Assisted (copy-paste)                      │
│                                                  │
│  Your answers are ready. Open the application    │
│  page and paste these in:                        │
│                                                  │
│  ┌─ Pre-generated Answers ─────────────────────┐ │
│  │  Work auth: Yes                    [copy]   │ │
│  │  Experience: 3 years in SRE...     [copy]   │ │
│  │  Why Workday: I'm drawn to...      [copy]   │ │
│  │  Cover letter: Dear hiring...      [copy]   │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  [Copy All]        [Open Application Page →]     │
└─────────────────────────────────────────────────┘
```

## Data Model

### New Table: `applications`
```sql
CREATE TABLE applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  job_id UUID NOT NULL,
  job_hash TEXT NOT NULL,
  submission_method TEXT NOT NULL,  -- 'greenhouse_api', 'ashby_api', 'remote_browser'
  platform TEXT,                    -- 'greenhouse', 'ashby', 'linkedin', 'workday', 'custom'
  status TEXT DEFAULT 'pending',    -- 'pending', 'submitted', 'confirmed', 'rejected', 'interview', 'offer'
  answers JSONB,                    -- all field values submitted
  confirmation_screenshot TEXT,     -- S3 key for submission proof
  submitted_at TIMESTAMPTZ,
  response_at TIMESTAMPTZ,         -- when company responded
  response_type TEXT,               -- 'rejection', 'interview_invite', 'offer', 'ghosted'
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### Application Outcome Tracking
When user updates status (interview → offer → accepted/rejected):
- Stored in `applications` table
- Fed back to self-improvement loop (Phase 2.9)
- Ground truth: "jobs I got interviews for had avg score X"
- Calibrates scoring accuracy over time

## Implementation Plan (Full Product)

### Step 1: Infrastructure (SAM resources)
- WebSocket API Gateway in template.yaml
- DynamoDB `browser_sessions` table
- New Lambda functions: ws-connect, ws-disconnect, ws-command, submit-application, start-browser, generate-answers
- Update Fargate task definition with browser_session.py entrypoint
- ECR image update with WebSocket client deps
- `applications` table in Supabase
- SAM deploy + verify

### Step 2: Easy Apply (Greenhouse/Ashby API)
- `POST /api/apply/easy-apply` endpoint in app.py
- Fetch custom questions from Greenhouse/Ashby job metadata APIs
- `POST /api/apply/generate-answers` — AI fills custom Qs from user profile + JD
- Easy Apply modal component in React (compact review UI)
- "⚡ Easy Apply" badge on Greenhouse/Ashby job cards in Dashboard + JobWorkspace
- Submit to Greenhouse/Ashby APIs with resume file + cover letter + answers
- `applications` table tracking
- Job status auto-updates to "Applied"
- Resume version selector (default: tailored)

### Step 3: Batch Easy Apply
- Dashboard: multi-select S+A Greenhouse/Ashby jobs → "⚡ Easy Apply to N jobs"
- Sequential modal: company/title/score → quick Qs → Submit → next
- Progress bar with pause/skip/stop controls
- Summary view: "Applied 8/10 (2 skipped)" with per-job status

### Step 4: Remote Browser Session
- `browser_session.py` on Fargate: Chrome + Playwright + WebSocket client
- Screenshot streaming (JPEG, 5fps via WebSocket)
- Click/type event forwarding (frontend → WSS → Fargate)
- Form field detection: extract labels, types, options from any page
- AI pre-fill pipeline: profile fields + AI custom answers
- Resume PDF upload via Playwright file input
- Per-platform cookie persistence (LinkedIn, Workday sessions reused)
- Session lifecycle: create on first apply, reuse, 30-min idle timeout
- Apply tab: embedded browser view component in React

### Step 5: Assisted Manual Fallback
- For sites that block Fargate (CAPTCHA-heavy, complex multi-step)
- Pre-generate all answers in dashboard
- One-click clipboard copy for each field
- "Open Application Page →" button
- Auto-prompt for outcome after 24 hours

### Step 6: Outcome Tracking + Feedback Loop
- Application status progression: submitted → viewed → interview → offer → accepted/rejected/ghosted
- Dashboard notifications: "Stripe viewed your application 2 days ago"
- Outcome stats: "Applied to 45, interviewed at 8 (18%), offered 2"
- Feed outcomes back to scoring (Phase 2.9): ground truth calibration
- Funnel visualization in Analytics tab (3.6)

### Step 7: Polish + Edge Cases
- Rate limiting: max 20 Easy Applies per hour (respect platform limits)
- Duplicate prevention: can't apply to same job twice
- Error recovery: if Fargate task crashes, show error + fallback to Mode 3
- Confirmation screenshots saved to S3 as proof
- Email summary: "You applied to 12 jobs today" with links

## Cost Estimate

| Component | Cost |
|-----------|------|
| Fargate Chrome session | ~$0.01-0.03 per 30-min session |
| AI answer generation | ~$0.001 per job (free tier providers) |
| Greenhouse/Ashby API | Free |
| S3 screenshots | Negligible |
| **Per application** | **~$0.005 (API) to $0.03 (browser)** |
| **100 applications/month** | **~$0.50-3.00** |

## Success Criteria

- **Easy Apply**: apply to a Greenhouse/Ashby job in **<10 seconds** (modal review + one click)
- **Batch Easy Apply**: apply to 10 Greenhouse jobs in **<2 minutes** (sequential confirm)
- **Remote Browser**: apply to a LinkedIn job in **<90 seconds** (browser session + review + submit)
- **Login persistence**: one login per platform per dashboard session (not per job)
- **95%+ standard fields** auto-filled correctly from user profile
- **AI custom answers** relevant and truthful (no fabrication)
- **Application status** automatically tracked in `applications` table
- **Zero installs** — everything inside naukribaba.netlify.app
- **"⚡ Easy Apply" badge** visible on all Greenhouse/Ashby jobs in dashboard
- **Outcome tracking** feeds back to scoring accuracy (Phase 2.9)
