# Auto-Apply — Design Spec

**Date**: 2026-04-08
**Status**: Draft
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

## Implementation Phases

### Phase 1: Easy Apply MVP (2-3 days)
- "⚡ Easy Apply" badge on Greenhouse/Ashby job cards in dashboard
- Easy Apply modal component (compact review + submit)
- Backend: fetch Greenhouse/Ashby custom questions from job metadata
- Backend: AI answer generation endpoint
- Backend: submit application to Greenhouse/Ashby API
- `applications` table + status tracking
- Job status auto-updates to "Applied" on success
- Resume version selector (default: tailored for this job)

### Phase 2: Batch Easy Apply (1-2 days)
- "⚡ Easy Apply to N jobs" button in dashboard (S+A tier filter)
- Sequential modal: review → submit → next job
- Progress bar with pause/skip/stop
- Summary: "Applied to 8/10 jobs (2 skipped)"

### Phase 3: Remote Browser (3-5 days)
- Fargate task definition for Chrome + Playwright
- WebSocket bridge: screenshot stream + click forwarding
- Form field detection via Playwright
- AI pre-fill pipeline
- Session management (create/reuse/timeout)
- Apply tab embeds remote browser view
- Per-platform login persistence

### Phase 4: Assisted Manual + Outcome Tracking (2 days)
- Fallback mode: pre-generate all answers, copy-to-clipboard
- "Open Application Page" with answers ready
- Outcome tracking (interview/rejection/offer/ghosted)
- Feedback loop to scoring accuracy (2.9 integration)
- Dashboard: application funnel visualization

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
