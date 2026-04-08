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

## Two Submission Paths

### Path 1: API Submission (Greenhouse/Ashby)
- No browser session needed
- Backend POSTs directly to Greenhouse/Ashby application API
- Fields: name, email, phone, resume (PDF from S3), cover letter, custom answers
- Custom questions answered by AI, shown in Apply tab for review
- User confirms → backend submits → status updates
- Fast (~2 seconds per application)

### Path 2: Remote Browser (LinkedIn, Workday, Custom Forms)
- Fargate Chrome navigates to apply URL
- Playwright detects form fields (labels, types, options)
- AI generates answers for each field
- Playwright pre-fills all fields + uploads resume PDF
- Browser view streamed to Apply tab via WebSocket
- User reviews, solves CAPTCHA if needed, clicks Submit
- Screenshot captured as proof of submission
- Slower (~30-60 seconds per application) but works universally

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

### Batch Apply
Because sessions persist, batch apply is possible:
- User selects 5 S-tier Greenhouse jobs → "Apply to All"
- Backend submits all 5 via API in sequence
- Each shows in Apply tab for 3-second review before auto-proceeding
- User can pause/cancel at any time

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

### Answer Review UI (Apply Tab)
```
┌─────────────────────────────────────────────────┐
│  Apply to: Senior SRE @ Stripe                   │
│  Via: Greenhouse API (direct submission)          │
│                                                  │
│  ┌─ Standard Fields ──────────────────────────┐  │
│  │  Name: Utkarsh Singh              [edit]   │  │
│  │  Email: 254utkarsh@gmail.com      [edit]   │  │
│  │  Phone: +353 892515620            [edit]   │  │
│  │  Resume: Utkarsh_Singh_SRE_Stripe.pdf  ▼  │  │
│  │  Cover Letter: [Preview] [Edit]            │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ┌─ Custom Questions (AI-generated) ──────────┐  │
│  │                                            │  │
│  │  Q: Are you authorized to work in Ireland? │  │
│  │  A: [Yes ▼]                       [edit]   │  │
│  │                                            │  │
│  │  Q: Years of experience with Kubernetes?   │  │
│  │  A: [3 years]                     [edit]   │  │
│  │                                            │  │
│  │  Q: Why do you want to work at Stripe?     │  │
│  │  A: [I'm drawn to Stripe's mission of...] │  │
│  │     [edit]                                 │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  [Cancel]            [✓ Confirm & Submit]        │
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

### Phase 1: Greenhouse/Ashby API (1-2 days)
- Apply tab in JobWorkspace
- Form extraction from Greenhouse/Ashby job metadata
- AI answer generation for custom questions
- Review UI with edit capability
- Direct API submission
- Status tracking in `applications` table

### Phase 2: Remote Browser MVP (3-5 days)
- Fargate task definition for Chrome + Playwright
- WebSocket bridge: screenshot stream + click forwarding
- Form field detection via Playwright
- AI pre-fill pipeline
- Session management (create/reuse/timeout)
- Apply tab embeds remote browser view

### Phase 3: Batch Apply + Outcome Tracking (2-3 days)
- Multi-select jobs → "Apply to All"
- Sequential processing with per-job review
- Outcome tracking (interview/rejection/offer)
- Feedback loop to scoring (2.9 integration)

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

- User can apply to a Greenhouse/Ashby job in <30 seconds (review + confirm)
- User can apply to a LinkedIn job in <90 seconds (browser session + review + submit)
- Platform login persists across all jobs for that platform within a session
- 95%+ of standard fields auto-filled correctly
- AI custom answers are relevant and truthful (no fabrication)
- Application status automatically tracked
- User never needs to install anything — everything in the web app
