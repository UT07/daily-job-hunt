# Auto-Apply — Cloud Browser Design Spec

**Date**: 2026-04-12
**Status**: Draft
**Phase**: Layer 4 → 3.4 Apply
**Supersedes**: 2026-04-11-auto-apply-mode-1-design.md (API POST approach abandoned — platform APIs require per-company auth keys)
**Approach**: Cloud Chrome on Fargate with persistent login profiles, streamed to the NaukriBaba web app via WebSocket

---

## 1. Problem

Job application forms live on 100+ different ATS platforms (Greenhouse, Ashby, LinkedIn, Workday, Lever, iCIMS, Taleo, etc.). Each has its own form structure, auth requirements, and CAPTCHA systems. No universal public API exists for submitting applications — every platform requires authenticated sessions.

The user has ~500 scored and tailored jobs in NaukriBaba. Applying to each manually takes 5-15 minutes. AI can generate all the answers, but the "last mile" — filling the actual web form and clicking Submit — requires a real browser with the user's authenticated session.

## 2. Solution

Embed a **live cloud browser** inside NaukriBaba's Apply tab. A real Chrome instance runs on AWS Fargate, streams its screen to the React frontend via WebSocket, and receives click/type events back. The AI pre-fills all form fields using NaukriBaba's tailored data. The user reviews the pre-filled form in the live stream, handles any CAPTCHAs, and clicks Submit.

**Key innovation**: Persistent login profiles stored on S3. The user logs into LinkedIn once via the cloud browser stream. That Chrome profile (cookies, localStorage, sessionStorage) is saved to S3. Next time, the profile is loaded automatically — no re-login for weeks until the session expires.

## 3. Design Principles

1. **All-in-one web app.** No Chrome extension, no CLI tool, no desktop app. Everything inside naukribaba.netlify.app.
2. **Human-in-the-loop always.** User sees exactly what the cloud browser sees. AI fills fields, user reviews, user clicks Submit. No silent auto-submit.
3. **Universal platform support.** Works on any website with a form — not dependent on platform-specific APIs.
4. **Persistent sessions.** Log into each platform once. Profile persists across sessions via S3.
5. **Graceful degradation.** If cloud browser fails (Fargate down, CDP blocked), fall back to Mode 3 (assisted copy-paste with AI answers).

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  NaukriBaba Web App (React, Netlify)                             │
│                                                                  │
│  ┌── Apply Tab ────────────────────────────────────────────────┐ │
│  │                                                             │ │
│  │  ┌── Cloud Browser Stream ──────┐ ┌── Answer Panel ──────┐ │ │
│  │  │                              │ │                       │ │ │
│  │  │  [Live JPEG stream of the    │ │ ✓ First Name: Utkarsh│ │ │
│  │  │   application form page]     │ │ ✓ Email: 254u...     │ │ │
│  │  │                              │ │ ✎ Why this role: ... │ │ │
│  │  │  User can:                   │ │ ✎ Salary: €70-90k   │ │ │
│  │  │  - Click (mouse forwarded)   │ │ ○ Work auth: ...     │ │ │
│  │  │  - Type (keyboard forwarded) │ │                       │ │ │
│  │  │  - Solve CAPTCHAs            │ │ [Edit] [Copy All]    │ │ │
│  │  │  - Scroll                    │ │                       │ │ │
│  │  └──────────────────────────────┘ └───────────────────────┘ │ │
│  │                                                             │ │
│  │  Status: Ready to submit (12/12 fields filled)              │ │
│  │  [⚡ Auto-Fill All]  [👁 Review]  [✓ Submit]  [⏭ Skip]     │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WebSocket (bidirectional)
                           │ ↓ screenshots (JPEG, 5-10fps, ~50KB each)
                           │ ↑ mouse/keyboard events + commands
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│  WebSocket API Gateway (AWS, SAM-managed)                        │
│                                                                  │
│  Routes:                                                         │
│  $connect    → ConnectHandler Lambda (JWT auth, DynamoDB write)  │
│  $disconnect → DisconnectHandler Lambda (cleanup)                │
│  screenshot  → Route to frontend connection (passthrough)        │
│  command     → Route to Fargate connection (passthrough)         │
│  fields      → Route to frontend (detected form fields)         │
│  status      → Route to frontend (session status updates)        │
│                                                                  │
│  Auth: JWT token passed as query param ?token=xxx                │
│  Two connections per session:                                    │
│    1. Frontend (React) ←→ API Gateway                            │
│    2. Fargate Chrome ←→ API Gateway                              │
│  API Gateway routes messages between them via connection IDs     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│  Fargate Chrome Task (ECS, persistent session)                   │
│                                                                  │
│  Docker image: Dockerfile.playwright (already exists)            │
│  Entrypoint: browser_session.py (NEW)                            │
│                                                                  │
│  Startup sequence:                                               │
│  1. Read session config from environment (user_id, job_id, etc.) │
│  2. Check S3 for saved profile: sessions/{user_id}/{platform}/   │
│     - If exists: download + extract to /tmp/chrome-profile/      │
│     - If not: create fresh profile directory                     │
│  3. Launch Chrome via Playwright's `launch_persistent_context`:   │
│     Playwright manages Chrome lifecycle internally.              │
│     Profile dir: /tmp/chrome-profile/ (loaded from S3 in step 2)│
│  4. (Playwright handles CDP connection internally — no separate  │
│     Chrome binary launch needed. launch_persistent_context does  │
│     both launch + connect atomically.)                           │
│  5. Connect to WebSocket API Gateway as the "browser" client     │
│  6. Navigate to job's apply_url                                  │
│  7. Start screenshot loop (JPEG encode → WS send, 5-10fps)      │
│  8. Listen for commands from frontend (click, type, fill, etc.)  │
│                                                                  │
│  Form filling flow:                                              │
│  a. Detect all form fields (DOM inspection via Playwright)       │
│  b. Send field list to frontend via WS "fields" message          │
│  c. Frontend shows fields in Answer Panel with AI answers        │
│  d. On "fill_all" command: Playwright fills each field           │
│  e. On "submit" command: Playwright clicks the submit button     │
│  f. Capture confirmation screenshot → S3                         │
│  g. Save updated Chrome profile to S3 (fresh cookies)            │
│  h. Send "applied" status to frontend                            │
│                                                                  │
│  CAPTCHA handling:                                               │
│  - Auto-detect via DOM inspection (hCaptcha, reCAPTCHA, etc.)    │
│  - Try CapSolver API first (automatic, ~$0.01 per solve)         │
│  - If CapSolver fails: user sees CAPTCHA in the stream,          │
│    solves it manually via click/type events                      │
│                                                                  │
│  Session lifecycle:                                              │
│  - Warm for 30 minutes (reused across multiple job applies)      │
│  - Idle timeout: 5 min no activity → save profile → terminate    │
│  - Explicit end: user clicks "End Session" → save + terminate    │
│  - Multiple applies: navigate to next URL, reuse same Chrome     │
│                                                                  │
│  Networking:                                                     │
│  - VPC: vpc-0bfb7f8052eb3968b (existing, from template.yaml)    │
│  - Subnets: MUST be public subnets with auto-assign public IP    │
│    (discover via: aws ec2 describe-subnets --filters             │
│     Name=vpc-id,Values=vpc-0bfb7f8052eb3968b)                   │
│  - Security group: ScraperSecurityGroup (existing, all outbound) │
│  - Fargate connects OUT to WebSocket API Gateway (no ALB needed) │
│  - Bright Data proxy optional (for anti-bot on LinkedIn/Workday) │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐  ┌─────────┐  ┌─────────┐
         │   S3   │  │ DynamoDB│  │Supabase │
         │        │  │         │  │         │
         │profiles│  │sessions │  │users    │
         │confirm │  │  table  │  │jobs     │
         │screenshts│ │         │  │applicat.│
         └────────┘  └─────────┘  └─────────┘
```

## 5. Infrastructure — SAM Template Additions

### 5.1 WebSocket API Gateway

```yaml
BrowserWebSocketApi:
  Type: AWS::ApiGatewayV2::Api
  Properties:
    Name: naukribaba-browser-ws
    ProtocolType: WEBSOCKET
    RouteSelectionExpression: "$request.body.action"

# Routes
ConnectRoute:
  Type: AWS::ApiGatewayV2::Route
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    RouteKey: $connect
    AuthorizationType: NONE  # JWT validated in Lambda
    Target: !Sub "integrations/${ConnectIntegration}"

DisconnectRoute:
  Type: AWS::ApiGatewayV2::Route
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    RouteKey: $disconnect
    Target: !Sub "integrations/${DisconnectIntegration}"

DefaultRoute:
  Type: AWS::ApiGatewayV2::Route
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    RouteKey: $default
    Target: !Sub "integrations/${DefaultIntegration}"

# Integrations — connect each route to its Lambda handler
ConnectIntegration:
  Type: AWS::ApiGatewayV2::Integration
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    IntegrationType: AWS_PROXY
    IntegrationUri: !Sub "arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${WsConnectFunction.Arn}/invocations"

DisconnectIntegration:
  Type: AWS::ApiGatewayV2::Integration
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    IntegrationType: AWS_PROXY
    IntegrationUri: !Sub "arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${WsDisconnectFunction.Arn}/invocations"

DefaultIntegration:
  Type: AWS::ApiGatewayV2::Integration
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    IntegrationType: AWS_PROXY
    IntegrationUri: !Sub "arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${WsRouteFunction.Arn}/invocations"

# Lambda permissions — allow API Gateway to invoke the WebSocket Lambdas
WsConnectPermission:
  Type: AWS::Lambda::Permission
  Properties:
    FunctionName: !Ref WsConnectFunction
    Action: lambda:InvokeFunction
    Principal: apigateway.amazonaws.com
    SourceArn: !Sub "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${BrowserWebSocketApi}/*/$connect"

WsDisconnectPermission:
  Type: AWS::Lambda::Permission
  Properties:
    FunctionName: !Ref WsDisconnectFunction
    Action: lambda:InvokeFunction
    Principal: apigateway.amazonaws.com
    SourceArn: !Sub "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${BrowserWebSocketApi}/*/$disconnect"

WsRoutePermission:
  Type: AWS::Lambda::Permission
  Properties:
    FunctionName: !Ref WsRouteFunction
    Action: lambda:InvokeFunction
    Principal: apigateway.amazonaws.com
    SourceArn: !Sub "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${BrowserWebSocketApi}/*/$default"

# Stage
BrowserWsStage:
  Type: AWS::ApiGatewayV2::Stage
  Properties:
    ApiId: !Ref BrowserWebSocketApi
    StageName: prod
    AutoDeploy: true
    DefaultRouteSettings:
      # Disable detailed access logging to prevent JWT leaking in query strings
      LoggingLevel: ERROR  # not INFO — INFO logs include query params
```

### 5.2 DynamoDB Session Table

```yaml
BrowserSessionsTable:
  Type: AWS::DynamoDB::Table
  Properties:
    TableName: naukribaba-browser-sessions
    BillingMode: PAY_PER_REQUEST
    AttributeDefinitions:
      - AttributeName: user_id
        AttributeType: S
      - AttributeName: session_id
        AttributeType: S
    KeySchema:
      - AttributeName: session_id
        KeyType: HASH
    GlobalSecondaryIndexes:
      - IndexName: user-sessions-index
        KeySchema:
          - AttributeName: user_id
            KeyType: HASH
        Projection:
          ProjectionType: ALL

    TimeToLiveSpecification:
      AttributeName: ttl
      Enabled: true
```

**Session row schema:**

```json
{
  "session_id": "uuid",
  "user_id": "uuid",
  "fargate_task_arn": "arn:aws:ecs:...",
  "ws_connection_frontend": "conn-id-abc",
  "ws_connection_browser": "conn-id-xyz",
  "platform": "greenhouse",
  "current_job_id": "uuid",
  "status": "starting|ready|filling|submitting|idle|ended",
  "created_at": "2026-04-12T10:00:00Z",
  "last_activity_at": "2026-04-12T10:05:00Z",
  "ttl": 1744546800
}
```

### 5.3 New Lambda Functions

| Function | Trigger | Purpose | Timeout |
|----------|---------|---------|---------|
| `naukribaba-ws-connect` | WebSocket $connect | Validate JWT, create DynamoDB session, return connection ID | 10s |
| `naukribaba-ws-disconnect` | WebSocket $disconnect | Update DynamoDB, trigger Fargate cleanup if both disconnected | 10s |
| `naukribaba-ws-route` | WebSocket $default | Route messages between frontend ↔ Fargate by reading DynamoDB session | 5s |
| `naukribaba-start-browser` | HTTP API (POST /api/apply/start-session) | Launch Fargate task, return session_id | 30s |
| `naukribaba-stop-browser` | HTTP API (POST /api/apply/stop-session) | Stop Fargate task, save profile to S3 | 15s |

### 5.4 Fargate Task Definition Update

The existing `PlaywrightTaskDef` in template.yaml needs updates:

```yaml
BrowserSessionTaskDef:
  Type: AWS::ECS::TaskDefinition
  Properties:
    Family: naukribaba-browser-session
    RequiresCompatibilities: [FARGATE]
    Cpu: '1024'       # 1 vCPU — Chrome needs real CPU for rendering + JS
    Memory: '3072'    # 3GB — matches existing PlaywrightTaskDef; Chrome uses ~1-1.5GB, headroom for Playwright + profile tar
    NetworkMode: awsvpc
    ExecutionRoleArn: !GetAtt FargateExecutionRole.Arn  # existing role from template.yaml
    TaskRoleArn: !GetAtt BrowserTaskRole.Arn
    ContainerDefinitions:
      - Name: browser
        Image: !Sub "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/naukribaba-browser:latest"
        Essential: true
        Environment:
          - Name: AWS_REGION
            Value: !Ref AWS::Region
          - Name: S3_BUCKET
            Value: utkarsh-job-hunt
          - Name: WEBSOCKET_URL
            Value: !Sub "wss://${BrowserWebSocketApi}.execute-api.${AWS::Region}.amazonaws.com/prod"
          - Name: CAPSOLVER_API_KEY
            Value: !Ref CapSolverApiKey
          - Name: SUPABASE_URL
            Value: !Ref SupabaseUrl
          - Name: SUPABASE_SERVICE_KEY
            Value: !Ref SupabaseServiceKey
        LogConfiguration:
          LogDriver: awslogs
          Options:
            awslogs-group: /ecs/naukribaba-browser
            awslogs-region: !Ref AWS::Region
            awslogs-stream-prefix: browser
```

### 5.5 IAM Role for Browser Task

```yaml
BrowserTaskRole:
  Type: AWS::IAM::Role
  Properties:
    AssumeRolePolicyDocument:
      Version: '2012-10-17'
      Statement:
        - Effect: Allow
          Principal:
            Service: ecs-tasks.amazonaws.com
          Action: sts:AssumeRole
    Policies:
      - PolicyName: BrowserSessionPolicy
        PolicyDocument:
          Version: '2012-10-17'
          Statement:
            - Effect: Allow
              Action:
                - s3:GetObject
                - s3:PutObject
              Resource:
                - "arn:aws:s3:::utkarsh-job-hunt/sessions/*"
                - "arn:aws:s3:::utkarsh-job-hunt/confirmations/*"
            - Effect: Allow
              Action:
                - execute-api:ManageConnections
              Resource:
                - !Sub "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${BrowserWebSocketApi}/*"
            - Effect: Allow
              Action:
                - dynamodb:GetItem
                - dynamodb:UpdateItem
              Resource:
                - !GetAtt BrowserSessionsTable.Arn
```

## 6. Data Model Changes

### 6.0 PREREQUISITE: Migration from previous spec must be applied first

**CRITICAL**: The `applications` table and the `jobs.apply_platform` / `apply_board_token` / `apply_posting_id` columns do NOT exist yet. The migration `20260411_auto_apply_setup.sql` from the previous spec (§6) must be applied to Supabase before ANY auto-apply code can run. This migration:

1. Creates the `applications` table with all columns, RLS, partial unique indexes, trigger
2. Adds `first_name`, `last_name`, `default_referral_source`, `salary_expectation_notes`, `notice_period_text` to `users`
3. Adds `apply_platform`, `apply_board_token`, `apply_posting_id`, `easy_apply_eligible` to `jobs`
4. Backfills user profile data and first/last name split

The full SQL is in `docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md` §6. Apply it as Step 1 of implementation, before writing any code.

### 6.1 Changes to `applications` table

On top of the base `applications` table from the prerequisite migration, expand the `submission_method` values and add new columns:

```sql
-- Update CHECK constraint to include new methods
ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_submission_method_check;
ALTER TABLE applications ADD CONSTRAINT applications_submission_method_check
  CHECK (submission_method IN (
    'greenhouse_api', 'ashby_api',
    'cloud_browser',       -- NEW: submitted via Fargate Chrome
    'assisted_manual',     -- NEW: user copy-pasted with AI answers
    'remote_browser'       -- legacy alias
  ));
```

New columns on `applications`:

```sql
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS browser_session_id TEXT,           -- DynamoDB session reference
  ADD COLUMN IF NOT EXISTS confirmation_screenshot_s3_key TEXT, -- proof of submission
  ADD COLUMN IF NOT EXISTS form_fields_detected INT,          -- how many fields the AI detected
  ADD COLUMN IF NOT EXISTS form_fields_filled INT;            -- how many were auto-filled
```

### 6.2 Everything else stays

- `users` columns (first_name, last_name, etc.) — from previous spec
- `jobs` columns (apply_platform, etc.) — from previous spec
- `applications` core schema — from previous spec
- `application_timeline` — unchanged
- RLS policies — unchanged
- Partial unique indexes — unchanged

## 7. WebSocket Protocol

### 7.1 Message Types — Frontend → Fargate

```typescript
// Mouse click at coordinates (relative to viewport)
{ action: "click", x: 450, y: 320, button: "left" }

// Keyboard input
{ action: "type", text: "Utkarsh Singh" }
{ action: "key", key: "Enter" }
{ action: "key", key: "Tab" }
{ action: "key", key: "v", modifiers: ["Control"] }  // Ctrl+V (paste)
{ action: "key", key: "a", modifiers: ["Control"] }  // Ctrl+A (select all)
{ action: "key", key: "Backspace" }                   // delete

// Commands
{ action: "navigate", url: "https://job-boards.greenhouse.io/..." }
{ action: "fill_all", answers: { "field_id": "value", ... } }
{ action: "fill_field", field_id: "question_57340088", value: "545540727" }
{ action: "submit" }  // click the detected submit button
{ action: "scroll", deltaY: 300 }

// Session control
{ action: "next_job", job_id: "uuid", apply_url: "https://..." }
{ action: "end_session" }
```

### 7.2 Message Types — Fargate → Frontend

**NOTE:** Screenshots are NOT sent as WebSocket messages. They are delivered as raw
binary via the API Gateway Management API (see §7.3). The frontend receives them
as `ArrayBuffer` on `ws.onmessage`. All messages below are JSON text frames.

```typescript
// Detected form fields (sent after page load + field detection)
{ action: "fields", fields: [
    { id: "first_name", label: "First Name", type: "text", required: true,
      value: "", rect: { x: 100, y: 200, w: 300, h: 30 } },
    { id: "question_57340088", label: "Work Authorization", type: "select",
      required: true, options: [
        { label: "I am authorised...", value: "545540727" },
        { label: "Need sponsorship", value: "545540728" }
      ], rect: { x: 100, y: 400, w: 300, h: 30 } },
    ...
  ]
}

// Status updates
{ action: "status", status: "ready" }             // Chrome loaded, page navigated
{ action: "status", status: "login_required" }     // platform login page detected
{ action: "status", status: "filling" }            // AI is filling fields
{ action: "status", status: "filled", count: 12 }  // all fields filled
{ action: "status", status: "captcha_detected", type: "hcaptcha" }
{ action: "status", status: "captcha_solved" }
{ action: "status", status: "submitted" }          // form submitted successfully
{ action: "status", status: "error", message: "..." }

// Field fill confirmation (per-field, so frontend can update Answer Panel)
{ action: "field_filled", field_id: "first_name", success: true }
{ action: "field_filled", field_id: "question_57340088", success: false, error: "option not found" }
```

### 7.3 Screenshot Streaming Protocol

**CRITICAL**: AWS API Gateway WebSocket has a **32KB binary frame limit** and **128KB text frame limit**. Raw JPEG screenshots at 1280x800 are 50-80KB — they CANNOT be sent as binary WebSocket frames through the API Gateway routes.

**Solution**: Fargate sends screenshots directly to the frontend using the **API Gateway Management API** (`POST @connections/{connectionId}`). This has a **128KB payload limit** — sufficient for most frames. No Lambda routing needed.

```
Fargate → POST https://{api-id}.execute-api.{region}.amazonaws.com/prod/@connections/{frontend_conn_id}
         Body: raw JPEG bytes (binary)
         Limit: 128KB per call
```

- Format: JPEG, resolution 1280x800
- Quality: **adaptive** — start at 75%, if frame > 120KB auto-reduce to 60%, if still > 120KB reduce to 45%
- Frame rate: 5fps idle, 10fps during active interaction
- Bandwidth: ~40-60KB per frame × 5fps = ~200-300KB/s baseline
- Frontend renders in a `<canvas>` element via `onmessage` binary handler
- Fargate reads the frontend's `connection_id` from DynamoDB on session start
- **No Lambda invocation per frame** — Fargate calls the Management API directly (IAM `execute-api:ManageConnections` permission already granted in §5.5)
- Text messages (commands, field data, status) still go through WebSocket routes (< 32KB, always fit)

### 7.4 Connection Lifecycle

```
1. User clicks "Apply" on a job in NaukriBaba
2. Frontend calls POST /api/apply/start-session { job_id }
3. Lambda:
   a. Creates DynamoDB session row (status: starting)
   b. Launches Fargate task with env vars (session_id, user_id, job_id, etc.)
   c. Returns { session_id, ws_url }
4. Frontend connects to WebSocket: wss://...?token=JWT&session=SESSION_ID
5. $connect Lambda validates JWT, stores frontend connection_id in DynamoDB
6. Fargate task starts, connects to same WebSocket as "browser" client
7. $connect Lambda links browser connection_id to the session in DynamoDB
8. Fargate navigates to apply_url
9. Screenshot stream begins → frontend renders in <canvas>
10. Fargate detects form fields → sends "fields" message
11. Frontend shows Answer Panel with AI-generated answers
12. User clicks "Auto-Fill All" → command sent → Fargate fills fields
13. User reviews in the live stream
14. User clicks "Submit" → command sent → Fargate clicks real submit button
15. Fargate captures confirmation screenshot → S3
16. Fargate sends "submitted" status
17. Frontend writes applications row via POST /api/apply/record
18. User clicks "Next Job" or "End Session"
```

## 8. browser_session.py — Fargate Entrypoint

This is the core new file. ~500-700 lines of Python.

### 8.1 Startup

```python
"""Fargate entrypoint for the cloud browser session.

Launches Chrome, connects to WebSocket API Gateway, streams screenshots,
receives commands, fills forms, and handles CAPTCHA solving.
"""
import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import tarfile
import time
from io import BytesIO
from pathlib import Path

import boto3
import websockets
from playwright.async_api import async_playwright

logger = logging.getLogger("browser_session")

# Config from environment
SESSION_ID = os.environ["SESSION_ID"]
USER_ID = os.environ["USER_ID"]
JOB_ID = os.environ["JOB_ID"]
APPLY_URL = os.environ["APPLY_URL"]
PLATFORM = os.environ.get("PLATFORM", "unknown")
WS_URL = os.environ["WEBSOCKET_URL"]
WS_TOKEN = os.environ["WS_TOKEN"]
S3_BUCKET = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")
CAPSOLVER_KEY = os.environ.get("CAPSOLVER_API_KEY", "")

PROFILE_DIR = Path("/tmp/chrome-profile")
SCREENSHOT_QUALITY = 75
SCREENSHOT_FPS = 5
IDLE_TIMEOUT = 300  # 5 min idle → save + exit
SESSION_TIMEOUT = 1800  # 30 min max session
VIEWPORT = {"width": 1280, "height": 800}
```

### 8.2 Profile Persistence

```python
s3 = boto3.client("s3")


def load_profile():
    """Download saved Chrome profile from S3 if it exists."""
    key = f"sessions/{USER_ID}/{PLATFORM}/profile.tar.gz"
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        with tarfile.open(fileobj=BytesIO(obj["Body"].read()), mode="r:gz") as tar:
            tar.extractall(PROFILE_DIR)
        logger.info(f"Loaded saved profile from s3://{S3_BUCKET}/{key}")
        return True
    except s3.exceptions.NoSuchKey:
        logger.info("No saved profile found, starting fresh")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return False
    except Exception as e:
        logger.warning(f"Failed to load profile: {e}")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return False


def save_profile():
    """Save Chrome profile to S3 for session persistence."""
    key = f"sessions/{USER_ID}/{PLATFORM}/profile.tar.gz"
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(PROFILE_DIR, arcname=".")
    buf.seek(0)
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=buf.read())
    logger.info(f"Saved profile to s3://{S3_BUCKET}/{key}")
```

### 8.3 Form Detection

```python
FORM_DETECTION_JS = """
() => {
    const fields = [];
    // Find all visible form inputs, textareas, selects
    const elements = document.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]):not([type=button]), ' +
        'textarea, select'
    );
    
    for (const el of elements) {
        // Skip invisible elements EXCEPT file inputs (often hidden, triggered by JS/drag-drop)
        if (el.offsetParent === null && el.type !== 'file') continue;
        
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        
        // Find the label
        let label = '';
        const labelEl = el.labels?.[0] || 
                        el.closest('label') ||
                        el.closest('[class*=field], [class*=question]')?.querySelector('label, [class*=label]');
        if (labelEl) label = labelEl.textContent.trim();
        if (!label) label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
        
        // Get options for selects
        let options = null;
        if (el.tagName === 'SELECT') {
            options = Array.from(el.options).map(o => ({
                label: o.textContent.trim(),
                value: o.value
            })).filter(o => o.value);
        }
        
        // Detect radio groups
        if (el.type === 'radio') {
            const name = el.name;
            const group = document.querySelectorAll(`input[name="${name}"]`);
            options = Array.from(group).map(r => ({
                label: (r.labels?.[0]?.textContent || r.value).trim(),
                value: r.value
            }));
        }
        
        fields.push({
            id: el.id || el.name || `field_${fields.length}`,
            name: el.name,
            label: label.substring(0, 200),
            type: el.type || el.tagName.toLowerCase(),
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value,
            options: options,
            rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height)
            }
        });
    }
    
    // Detect HIDDEN file upload inputs (visible ones already captured above).
    // Greenhouse uses hidden file inputs triggered by drag-and-drop zones.
    const seenIds = new Set(fields.map(f => f.id));
    const fileInputs = document.querySelectorAll('input[type=file]');
    for (const el of fileInputs) {
        const fid = el.id || el.name || 'file_upload';
        if (seenIds.has(fid)) continue;  // already captured in main loop
        const label = el.labels?.[0]?.textContent?.trim() || el.name || 'File Upload';
        fields.push({
            id: fid,
            name: el.name,
            label: label,
            type: 'file',
            required: el.required,
            accept: el.accept,
            rect: { x: 0, y: 0, w: 0, h: 0 }
        });
    }
    
    // Detect the submit button
    const submitBtn = document.querySelector(
        'button[type=submit], input[type=submit], ' +
        'button:not([type])[class*=submit], ' +
        'button:not([type])[class*=apply]'
    ) || document.querySelector('button:not([type])');
    
    return {
        fields: fields,
        submit_button: submitBtn ? {
            text: submitBtn.textContent.trim(),
            selector: submitBtn.id ? '#' + submitBtn.id : null
        } : null,
        page_title: document.title,
        page_url: window.location.href,
        has_iframes: document.querySelectorAll('iframe').length
    };
}
"""

# NOTE: FORM_DETECTION_JS only scans the main document. Workday, iCIMS, and
# some Greenhouse embeds put the application form inside an iframe.
# The caller MUST also scan all iframes:
#
# async def _detect_and_send_fields(page, ws):
#     """Detect fields in main frame + all iframes, merge results."""
#     main_result = await page.evaluate(FORM_DETECTION_JS)
#     all_fields = main_result.get("fields", [])
#     submit_button = main_result.get("submit_button")
#
#     # Scan iframes for forms
#     for frame in page.frames:
#         if frame == page.main_frame:
#             continue
#         try:
#             frame_result = await frame.evaluate(FORM_DETECTION_JS)
#             frame_fields = frame_result.get("fields", [])
#             if frame_fields:
#                 # Tag each field with its frame URL for targeted filling
#                 for f in frame_fields:
#                     f["frame_url"] = frame.url
#                 all_fields.extend(frame_fields)
#                 if not submit_button and frame_result.get("submit_button"):
#                     submit_button = frame_result["submit_button"]
#                     submit_button["frame_url"] = frame.url
#         except Exception:
#             pass  # frame may be cross-origin or detached
#
#     await ws.send(json.dumps({
#         "action": "fields",
#         "fields": all_fields,
#         "submit_button": submit_button,
#         "page_title": main_result.get("page_title", ""),
#         "page_url": main_result.get("page_url", ""),
#     }))
#
# When filling fields in an iframe, use:
#   frame = next(f for f in page.frames if f.url == field["frame_url"])
#   el = await frame.query_selector(f"#{field['id']}")

"""

LOGIN_DETECTION_JS = """
() => {
    const url = window.location.href.toLowerCase();
    const body = document.body?.textContent?.toLowerCase() || '';
    
    // Common login page indicators
    if (url.includes('/login') || url.includes('/signin') || url.includes('/auth') ||
        url.includes('/sso') || url.includes('accounts.google.com') ||
        url.includes('login.microsoftonline.com')) {
        return { login_required: true, platform: 'generic' };
    }
    
    // LinkedIn specific
    if (url.includes('linkedin.com/login') || url.includes('linkedin.com/checkpoint')) {
        return { login_required: true, platform: 'linkedin' };
    }
    
    // Check for login form elements
    const hasPasswordField = !!document.querySelector('input[type=password]');
    const hasLoginForm = !!document.querySelector('form[action*=login], form[action*=signin], form#login');
    if (hasPasswordField && hasLoginForm) {
        return { login_required: true, platform: 'generic' };
    }
    
    return { login_required: false };
}
"""
```

### 8.4 CAPTCHA Detection and Solving

```python
CAPTCHA_DETECTION_JS = """
() => {
    const r = {};
    const url = window.location.href;
    
    // hCaptcha (check FIRST — also has data-sitekey)
    const hc = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
    if (hc) {
        r.type = 'hcaptcha';
        r.sitekey = hc.dataset.sitekey || hc.dataset.hcaptchaSitekey;
        return r;
    }
    if (document.querySelector('script[src*="hcaptcha.com"], iframe[src*="hcaptcha.com"]')) {
        const el = document.querySelector('[data-sitekey]');
        if (el) { r.type = 'hcaptcha'; r.sitekey = el.dataset.sitekey; return r; }
    }
    
    // reCAPTCHA v2
    const rc2 = document.querySelector('.g-recaptcha, [data-sitekey]');
    if (rc2 && !r.type) {
        r.type = 'recaptcha_v2';
        r.sitekey = rc2.dataset.sitekey;
        return r;
    }
    
    // reCAPTCHA v3 (invisible)
    if (document.querySelector('script[src*="recaptcha/api.js?render="]')) {
        const src = document.querySelector('script[src*="recaptcha/api.js"]').src;
        const match = src.match(/render=([^&]+)/);
        if (match) { r.type = 'recaptcha_v3'; r.sitekey = match[1]; return r; }
    }
    
    // Cloudflare Turnstile
    const cf = document.querySelector('.cf-turnstile, [data-sitekey]');
    if (cf && window.turnstile) {
        r.type = 'turnstile';
        r.sitekey = cf.dataset.sitekey;
        return r;
    }
    
    return r;
}
"""


async def solve_captcha(page, captcha_info, ws):
    """Attempt to solve a CAPTCHA using CapSolver API.
    
    Falls back to manual user solving if CapSolver fails.
    """
    import httpx
    
    if not CAPSOLVER_KEY or not captcha_info.get("sitekey"):
        await ws.send(json.dumps({
            "action": "status",
            "status": "captcha_detected",
            "type": captcha_info.get("type", "unknown"),
            "message": "Please solve the CAPTCHA manually"
        }))
        return False
    
    captcha_type = captcha_info["type"]
    sitekey = captcha_info["sitekey"]
    page_url = page.url
    
    # Map to CapSolver task types
    task_types = {
        "hcaptcha": "HCaptchaTaskProxyLess",
        "recaptcha_v2": "ReCaptchaV2TaskProxyLess",
        "recaptcha_v3": "ReCaptchaV3TaskProxyLess",
        "turnstile": "AntiTurnstileTaskProxyLess",
    }
    
    task_type = task_types.get(captcha_type)
    if not task_type:
        return False
    
    await ws.send(json.dumps({
        "action": "status",
        "status": "captcha_detected",
        "type": captcha_type,
        "message": f"Solving {captcha_type} automatically..."
    }))
    
    async with httpx.AsyncClient() as client:
        # Create task
        create_resp = await client.post("https://api.capsolver.com/createTask", json={
            "clientKey": CAPSOLVER_KEY,
            "task": {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": sitekey,
            }
        })
        task_data = create_resp.json()
        if task_data.get("errorId", 0) > 0:
            logger.warning(f"CapSolver create error: {task_data}")
            return False
        
        task_id = task_data["taskId"]
        
        # Poll for result (max 60s)
        for _ in range(30):
            await asyncio.sleep(2)
            result_resp = await client.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": CAPSOLVER_KEY,
                "taskId": task_id,
            })
            result = result_resp.json()
            if result.get("status") == "ready":
                token = result["solution"].get("gRecaptchaResponse") or result["solution"].get("token")
                if token:
                    # Inject the token
                    await _inject_captcha_token(page, captcha_type, token)
                    await ws.send(json.dumps({"action": "status", "status": "captcha_solved"}))
                    return True
            elif result.get("errorId", 0) > 0:
                logger.warning(f"CapSolver solve error: {result}")
                break
    
    # Failed — ask user to solve manually
    await ws.send(json.dumps({
        "action": "status",
        "status": "captcha_detected",
        "type": captcha_type,
        "message": "Auto-solve failed. Please solve manually."
    }))
    return False


async def _inject_captcha_token(page, captcha_type, token):
    """Inject a solved CAPTCHA token into the page.
    
    SECURITY: Uses Playwright's parameterized evaluate() to avoid JS injection.
    The token comes from CapSolver API and could theoretically contain malicious
    strings — never use f-string interpolation into evaluate().
    """
    if captcha_type in ("recaptcha_v2", "recaptcha_v3"):
        await page.evaluate("""(token) => {
            const el1 = document.querySelector('#g-recaptcha-response');
            const el2 = document.querySelector('[name=g-recaptcha-response]');
            if (el1) el1.value = token;
            if (el2) el2.value = token;
        }""", token)
    elif captcha_type == "hcaptcha":
        await page.evaluate("""(token) => {
            const el1 = document.querySelector('[name=h-captcha-response]');
            const el2 = document.querySelector('[name=g-recaptcha-response]');
            if (el1) el1.value = token;
            if (el2) el2.value = token;
        }""", token)
    elif captcha_type == "turnstile":
        await page.evaluate("""(token) => {
            const input = document.querySelector('[name=cf-turnstile-response]');
            if (input) input.value = token;
        }""", token)
```

### 8.5 Main Event Loop

```python
async def main():
    """Main browser session event loop."""
    # 1. Load profile
    profile_loaded = load_profile()
    
    # 2. Launch Chrome
    pw = await async_playwright().start()
    # CRITICAL: Fargate has no display server. headless=False would crash.
    # Use headless="new" — Chromium's new headless mode renders identically to
    # headed Chrome but requires no display. Anti-detection checks pass because
    # the rendering engine is the same as real Chrome.
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=True,  # new headless — screenshots work, no display needed
        viewport=VIEWPORT,
        args=[
            "--no-first-run",
            "--disable-default-apps",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--hide-scrollbars",
        ],
        ignore_default_args=["--enable-automation"],
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()
    
    # 3. Connect to WebSocket
    ws_full_url = f"{WS_URL}?token={WS_TOKEN}&session={SESSION_ID}&role=browser"
    async with websockets.connect(ws_full_url) as ws:
        # 4. Navigate to apply URL
        await ws.send(json.dumps({"action": "status", "status": "navigating"}))
        await page.goto(APPLY_URL, wait_until="networkidle", timeout=30000)
        
        # SPA forms (Greenhouse Remix, Workday React) render async after navigation.
        # Wait for form elements to appear before running detection.
        try:
            await page.wait_for_selector(
                "form, input:not([type=hidden]), textarea, [class*=field], [class*=question]",
                timeout=10000,
            )
        except Exception:
            logger.warning("No form elements found within 10s — page may need login or is non-standard")
        
        # 5. Check for login page
        login_info = await page.evaluate(LOGIN_DETECTION_JS)
        if login_info.get("login_required"):
            await ws.send(json.dumps({
                "action": "status",
                "status": "login_required",
                "platform": login_info.get("platform", PLATFORM),
                "message": "Please log in using the browser stream above",
            }))
            # Wait for user to log in — poll until login page is gone
            # Screenshot loop runs concurrently so user sees the login form
            for _ in range(120):  # 2 min max wait
                await asyncio.sleep(1)
                login_check = await page.evaluate(LOGIN_DETECTION_JS)
                if not login_check.get("login_required"):
                    break
            else:
                await ws.send(json.dumps({
                    "action": "status", "status": "error",
                    "message": "Login timed out after 2 minutes",
                }))
            # Re-wait for form elements after login redirect
            try:
                await page.wait_for_selector(
                    "form, input:not([type=hidden]), textarea",
                    timeout=10000,
                )
            except Exception:
                pass
        
        await ws.send(json.dumps({"action": "status", "status": "ready"}))
        
        # 6b. Auto-detect fields on initial page load
        # (populates the Answer Panel immediately without user clicking "Detect")
        await _detect_and_send_fields(page, ws)
        
        # 6. Start concurrent tasks with a shared stop signal
        # CRITICAL: asyncio.gather(return_exceptions=True) does NOT cancel siblings
        # when one coroutine returns. Use an Event so all loops check for shutdown.
        stop_event = asyncio.Event()
        last_activity = time.time()
        session_start = time.time()

        # Management API client for sending screenshots directly (bypasses 32KB WS limit)
        import httpx as _httpx
        mgmt_url = WS_URL.replace("wss://", "https://").rstrip("/")
        # Read frontend connection_id from DynamoDB
        ddb = boto3.resource("dynamodb")
        session_row = ddb.Table("naukribaba-browser-sessions").get_item(
            Key={"session_id": SESSION_ID}
        ).get("Item", {})
        frontend_conn_id = session_row.get("ws_connection_frontend")
        if not frontend_conn_id:
            # Frontend hasn't connected yet — wait up to 30s for it
            for _ in range(15):
                await asyncio.sleep(2)
                session_row = ddb.Table("naukribaba-browser-sessions").get_item(
                    Key={"session_id": SESSION_ID}
                ).get("Item", {})
                frontend_conn_id = session_row.get("ws_connection_frontend")
                if frontend_conn_id:
                    break
            if not frontend_conn_id:
                logger.error("Frontend never connected — screenshot streaming disabled")
                await ws.send(json.dumps({
                    "action": "status", "status": "error",
                    "message": "Frontend WebSocket not connected. Refresh the page.",
                }))
        
        # API Gateway Management API client for sending screenshots.
        # CRITICAL: Must use boto3 (handles SigV4 signing automatically).
        # Plain httpx without AWS auth headers → 403 on every call.
        import aioboto3  # async boto3 for non-blocking calls in the event loop
        aioboto3_session = aioboto3.Session()
        
        async def screenshot_loop():
            """Stream screenshots via API Gateway Management API (not WS frames).
            
            Uses boto3's post_to_connection which handles SigV4 signing.
            Raw httpx would get 403 — the Management API is an AWS service endpoint.
            """
            async with aioboto3_session.client(
                "apigatewaymanagementapi",
                endpoint_url=mgmt_url,
                region_name=os.environ.get("AWS_REGION", "eu-west-1"),
            ) as apigw:
                while not stop_event.is_set():
                    try:
                        quality = SCREENSHOT_QUALITY
                        screenshot = await page.screenshot(type="jpeg", quality=quality)
                        # Two-step quality reduction as specified:
                        # 75% → 60% → 45% if frame exceeds 120KB
                        if len(screenshot) > 120_000:
                            screenshot = await page.screenshot(type="jpeg", quality=60)
                        if len(screenshot) > 120_000:
                            screenshot = await page.screenshot(type="jpeg", quality=45)
                        if frontend_conn_id and len(screenshot) <= 128_000:
                            await apigw.post_to_connection(
                                ConnectionId=frontend_conn_id,
                                Data=screenshot,
                            )
                    except Exception as e:
                        logger.warning(f"Screenshot error: {e}")
                    await asyncio.sleep(1.0 / SCREENSHOT_FPS)
        
        async def command_loop():
            """Listen for commands from the frontend via WebSocket."""
            nonlocal last_activity
            try:
                async for message in ws:
                    if stop_event.is_set():
                        break
                    last_activity = time.time()
                    
                    if isinstance(message, str):
                        cmd = json.loads(message)
                        action = cmd.get("action")
                        
                        if action == "click":
                            await page.mouse.click(cmd["x"], cmd["y"])
                        
                        elif action == "type":
                            await page.keyboard.type(cmd["text"], delay=50)
                        
                        elif action == "key":
                            # Support modifier keys: Ctrl+V, Ctrl+A, Backspace, etc.
                            key = cmd["key"]
                            modifiers = cmd.get("modifiers", [])  # ["Control", "Shift", etc.]
                            for mod in modifiers:
                                await page.keyboard.down(mod)
                            await page.keyboard.press(key)
                            for mod in reversed(modifiers):
                                await page.keyboard.up(mod)
                        
                        elif action == "scroll":
                            await page.mouse.wheel(0, cmd.get("deltaY", 100))
                        
                        elif action == "navigate":
                            await page.goto(cmd["url"], wait_until="networkidle")
                            # Re-detect fields after navigation (multi-step forms)
                            await _detect_and_send_fields(page, ws)
                        
                        elif action == "fill_all":
                            await fill_all_fields(page, cmd.get("answers", {}), ws)
                        
                        elif action == "fill_field":
                            await fill_single_field(page, cmd["field_id"], cmd["value"])
                            await ws.send(json.dumps({
                                "action": "field_filled",
                                "field_id": cmd["field_id"],
                                "success": True
                            }))
                        
                        elif action == "detect_fields":
                            await _detect_and_send_fields(page, ws)
                        
                        elif action == "submit":
                            await handle_submit(page, ws)
                            # Re-detect after submit (multi-step: may load next page)
                            await asyncio.sleep(2)  # wait for navigation
                            await _detect_and_send_fields(page, ws)
                        
                        elif action == "upload_resume":
                            await upload_resume(page, cmd.get("s3_key"))
                        
                        elif action == "next_job":
                        # Update DynamoDB with the new job_id for session tracking
                        new_job_id = cmd.get("job_id")
                        if new_job_id:
                            ddb.Table("naukribaba-browser-sessions").update_item(
                                Key={"session_id": SESSION_ID},
                                UpdateExpression="SET current_job_id = :jid",
                                ExpressionAttributeValues={":jid": new_job_id},
                            )
                            await page.goto(cmd["apply_url"], wait_until="networkidle")
                            await _detect_and_send_fields(page, ws)
                            await ws.send(json.dumps({"action": "status", "status": "ready"}))
                        
                        elif action == "end_session":
                            stop_event.set()
                            break
            except websockets.exceptions.ConnectionClosed:
                logger.info("WebSocket closed by frontend")
                stop_event.set()
        
        async def idle_monitor():
            """Terminate session after idle or max timeout."""
            nonlocal last_activity
            while not stop_event.is_set():
                await asyncio.sleep(30)
                elapsed = time.time() - last_activity
                total = time.time() - session_start
                if elapsed > IDLE_TIMEOUT:
                    logger.info("Idle timeout reached")
                    stop_event.set()
                    break
                if total > SESSION_TIMEOUT:
                    logger.info("Max session timeout reached")
                    stop_event.set()
                    break
        
        # Run all three concurrently — any can trigger stop_event to end the others.
        # return_exceptions=True prevents one crash from cancelling siblings via
        # CancelledError. Instead, all three run until stop_event is set.
        try:
            await asyncio.gather(
                screenshot_loop(),
                command_loop(),
                idle_monitor(),
                return_exceptions=True,
            )
        except Exception as e:
            logger.error(f"Session error: {e}")
        
        # 7. Cleanup — CRITICAL: close browser BEFORE saving profile
        # Otherwise profile contains SingletonLock files that break next session
        await browser.close()
        await pw.stop()
        
        # Delete lock files that Chrome leaves behind
        for lock_file in PROFILE_DIR.glob("**/Singleton*"):
            lock_file.unlink(missing_ok=True)
        for lock_file in PROFILE_DIR.glob("**/*.lock"):
            lock_file.unlink(missing_ok=True)
        
        # Strip cache directories to reduce profile size (100MB → 5-10MB)
        for cache_dir in ["Cache", "Code Cache", "GPUCache", "Service Worker",
                          "DawnCache", "DawnGraphiteCache", "ShaderCache"]:
            cache_path = PROFILE_DIR / "Default" / cache_dir
            if cache_path.exists():
                shutil.rmtree(cache_path, ignore_errors=True)
        
        save_profile()
    
    logger.info("Browser session ended")


async def _detect_and_send_fields(page, ws):
    """Detect fields in main frame + all iframes, merge, send to frontend."""
    main_result = await page.evaluate(FORM_DETECTION_JS)
    all_fields = main_result.get("fields", [])
    submit_button = main_result.get("submit_button")

    # Scan iframes for forms (Workday, iCIMS, some Greenhouse embeds)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_result = await frame.evaluate(FORM_DETECTION_JS)
            frame_fields = frame_result.get("fields", [])
            if frame_fields:
                for f in frame_fields:
                    f["frame_url"] = frame.url
                all_fields.extend(frame_fields)
                if not submit_button and frame_result.get("submit_button"):
                    submit_button = frame_result["submit_button"]
                    submit_button["frame_url"] = frame.url
        except Exception:
            pass  # frame may be cross-origin or detached

    await ws.send(json.dumps({
        "action": "fields",
        "fields": all_fields,
        "submit_button": submit_button,
        "page_title": main_result.get("page_title", ""),
        "page_url": main_result.get("page_url", ""),
    }))


async def fill_all_fields(page, answers, ws):
    """Fill all detected form fields with the provided answers."""
    await ws.send(json.dumps({"action": "status", "status": "filling"}))
    
    fields_data = await page.evaluate(FORM_DETECTION_JS)
    filled = 0
    
    for field in fields_data.get("fields", []):
        field_id = field.get("id") or field.get("name")
        if not field_id or field_id not in answers:
            continue
        
        value = answers[field_id]
        try:
            await fill_single_field(page, field_id, value, field_type=field.get("type"))
            filled += 1
            await ws.send(json.dumps({
                "action": "field_filled",
                "field_id": field_id,
                "success": True
            }))
        except Exception as e:
            logger.warning(f"Failed to fill {field_id}: {e}")
            await ws.send(json.dumps({
                "action": "field_filled",
                "field_id": field_id,
                "success": False,
                "error": str(e)[:100]
            }))
    
    # Auto-upload resume if a file input was detected
    for field in fields_data.get("fields", []):
        if field.get("type") == "file" and "resume" in (field.get("label") or "").lower():
            resume_s3_key = answers.get("_resume_s3_key")  # passed from frontend
            if resume_s3_key:
                try:
                    await upload_resume(page, resume_s3_key)
                    filled += 1
                    await ws.send(json.dumps({
                        "action": "field_filled",
                        "field_id": field["id"],
                        "success": True
                    }))
                except Exception as e:
                    logger.warning(f"Resume upload failed: {e}")
    
    await ws.send(json.dumps({
        "action": "status",
        "status": "filled",
        "count": filled
    }))


async def fill_single_field(page, field_id, value, field_type=None, frame=None):
    """Fill a single form field.
    
    IMPORTANT for select fields: Greenhouse and other ATS platforms use
    numeric value IDs (e.g., 545540727) not label text. The Answer Panel
    stores {label, value} pairs from the detected options. The `value`
    parameter here should be the OPTION VALUE (numeric ID), not the label.
    The AI picks labels; the frontend maps label → value before sending
    the fill command.
    """
    target = frame or page
    selector = f"#{field_id}" if not field_id.startswith("#") else field_id
    
    # Try by id first, then by name
    el = await target.query_selector(selector)
    if not el:
        el = await target.query_selector(f"[name='{field_id}']")
    if not el:
        raise ValueError(f"Field not found: {field_id}")
    
    tag = await el.evaluate("el => el.tagName.toLowerCase()")
    
    if tag == "select":
        # value should be the option's value attribute (numeric ID), not label text
        await el.select_option(value=str(value))
    elif tag == "textarea":
        await el.fill("")
        await el.fill(value)
    elif field_type == "radio":
        # Click the radio with matching value
        radio = await page.query_selector(f"input[name='{field_id}'][value='{value}']")
        if radio:
            await radio.click()
    elif field_type == "checkbox":
        checked = await el.is_checked()
        if (value in ("true", True)) != checked:
            await el.click()
    elif field_type == "file":
        # File uploads handled separately via upload_resume()
        pass
    else:
        await el.fill("")
        await el.fill(str(value))


async def upload_resume(page, s3_key):
    """Download resume from S3 and upload via file input."""
    if not s3_key:
        return
    
    local_path = Path("/tmp/resume.pdf")
    s3.download_file(S3_BUCKET, s3_key, str(local_path))
    
    file_input = await page.query_selector("input[type=file]")
    if file_input:
        await file_input.set_input_files(str(local_path))
        logger.info(f"Uploaded resume from {s3_key}")


async def handle_submit(page, ws):
    """Click the submit button and capture confirmation."""
    await ws.send(json.dumps({"action": "status", "status": "submitting"}))
    
    # Check for CAPTCHA before submit
    captcha = await page.evaluate(CAPTCHA_DETECTION_JS)
    if captcha.get("type"):
        solved = await solve_captcha(page, captcha, ws)
        if not solved:
            await ws.send(json.dumps({
                "action": "status",
                "status": "captcha_detected",
                "message": "Solve the CAPTCHA manually, then click Submit again"
            }))
            return
    
    # Find and click submit button
    submit_selectors = [
        "button[type=submit]",
        "input[type=submit]",
        "button[class*=submit]",
        "button[class*=apply]",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Send')",
    ]
    
    for selector in submit_selectors:
        btn = await page.query_selector(selector)
        if btn and await btn.is_visible():
            await btn.click()
            break
    
    # Wait for navigation or confirmation
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    
    # Capture confirmation screenshot
    screenshot = await page.screenshot(type="jpeg", quality=90)
    confirm_key = f"confirmations/{USER_ID}/{SESSION_ID}.jpg"
    s3.put_object(Bucket=S3_BUCKET, Key=confirm_key, Body=screenshot)
    
    # Write pending_record to DynamoDB as a safety net.
    # If the frontend crashes before calling POST /api/apply/record,
    # a cleanup Lambda can read this flag and write the applications row.
    ddb = boto3.resource("dynamodb")
    ddb.Table("naukribaba-browser-sessions").update_item(
        Key={"session_id": SESSION_ID},
        UpdateExpression="SET pending_record = :pr, confirmation_key = :ck",
        ExpressionAttributeValues={
            ":pr": True,
            ":ck": confirm_key,
        },
    )
    
    await ws.send(json.dumps({
        "action": "status",
        "status": "submitted",
        "confirmation_screenshot_key": confirm_key
    }))


if __name__ == "__main__":
    asyncio.run(main())
```

## 9. Backend Endpoints

### 9.1 Endpoints that STAY from the previous spec

- `GET /api/apply/eligibility/{job_id}` — unchanged
- `GET /api/apply/preview/{job_id}` — unchanged (still generates AI answers)
- Profile completeness check — unchanged
- Rate limiting — unchanged
- `applications` table writes — unchanged

### 9.2 New endpoint: `POST /api/apply/start-session`

```python
class StartSessionRequest(BaseModel):
    job_id: str

class StartSessionResponse(BaseModel):
    session_id: str
    ws_url: str
    status: str  # "starting"

@app.post("/api/apply/start-session", response_model=StartSessionResponse)
def start_browser_session(
    req: StartSessionRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Launch a Fargate Chrome task for applying to a job."""
    # 1. Get job data
    job = load_job(req.job_id, user.id)
    if not job:
        raise HTTPException(404)
    
    # 1b. CHECK PROFILE COMPLETENESS BEFORE LAUNCHING FARGATE
    # This prevents wasting a $0.02 Fargate session on an incomplete profile.
    user_row = _db.get_user(user.id) or {}
    missing = _check_profile_completeness(user_row)
    if missing:
        raise HTTPException(412, f"profile_incomplete:{','.join(missing)}")
    
    # 2. Check for existing active session (reuse if warm)
    # Query DynamoDB for user's active sessions
    # If found and status != 'ended': return existing session
    
    # 3. Create new session
    session_id = str(uuid.uuid4())
    
    # 4. Generate short-lived JWT for WebSocket auth
    # The WS token is a standard Supabase JWT with an additional 'session' claim,
    # short TTL (60 seconds — only needs to survive the WS handshake), and 
    # audience='ws' to distinguish from regular API tokens.
    import time as _time
    ws_payload = {
        "sub": user.id,
        "session": session_id,
        "aud": "ws",
        "role": "authenticated",
        "iss": "supabase",
        "iat": int(_time.time()),
        "exp": int(_time.time()) + 60,  # 60s TTL — only for WS upgrade
    }
    ws_token = jwt.encode(ws_payload, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")
    
    # 5. Launch Fargate task
    ecs = boto3.client("ecs")
    # Subnet IDs are stored as a SAM parameter (discovered once during setup via:
    #   aws ec2 describe-subnets --filters Name=vpc-id,Values=vpc-0bfb7f8052eb3968b
    # and added to samconfig.toml as BrowserSubnetIds=subnet-xxx,subnet-yyy)
    subnet_ids = os.environ.get("BROWSER_SUBNET_IDS", "").split(",")
    sg_id = os.environ.get("BROWSER_SG_ID", "")  # ScraperSecurityGroup ID
    
    task = ecs.run_task(
        cluster="naukribaba-scrapers",  # existing ECS cluster from template.yaml
        taskDefinition="naukribaba-browser-session",
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED",  # required for internet access
            }
        },
        overrides={
            "containerOverrides": [{
                "name": "browser",
                "environment": [
                    {"name": "SESSION_ID", "value": session_id},
                    {"name": "USER_ID", "value": user.id},
                    {"name": "JOB_ID", "value": req.job_id},
                    {"name": "APPLY_URL", "value": job.get("apply_url", "")},
                    {"name": "PLATFORM", "value": job.get("apply_platform", "unknown")},
                    {"name": "WS_TOKEN", "value": ws_token},
                ]
            }]
        },
    )
    
    # 6. Write DynamoDB session
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("naukribaba-browser-sessions")
    table.put_item(Item={
        "session_id": session_id,
        "user_id": user.id,
        "fargate_task_arn": task["tasks"][0]["taskArn"],
        "status": "starting",
        "platform": job.get("apply_platform", "unknown"),
        "current_job_id": req.job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ttl": int(time.time()) + 1800,  # 30 min session timeout
    })
    
    ws_url = os.environ.get("BROWSER_WS_URL", "wss://xxx.execute-api.eu-west-1.amazonaws.com/prod")
    
    return StartSessionResponse(
        session_id=session_id,
        ws_url=ws_url,
        status="starting",
    )
```

### 9.3 New endpoint: `POST /api/apply/record`

Called by the frontend AFTER the cloud browser submits successfully. Writes the `applications` row.

```python
class RecordApplicationRequest(BaseModel):
    session_id: str
    job_id: str
    confirmation_screenshot_key: Optional[str] = None
    form_fields_detected: int = 0
    form_fields_filled: int = 0

@app.post("/api/apply/record")
def record_application(
    req: RecordApplicationRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Record a successful cloud browser application."""
    job = load_job(req.job_id, user.id)
    if not job:
        raise HTTPException(404)
    
    app_row = {
        "user_id": user.id,
        "job_id": req.job_id,
        "job_hash": job.get("job_hash", ""),
        "canonical_hash": job.get("canonical_hash"),
        "submission_method": "cloud_browser",
        "platform": job.get("apply_platform", "unknown"),
        "posting_id": job.get("apply_posting_id"),
        "board_token": job.get("apply_board_token"),
        "resume_s3_key": job.get("resume_s3_key", ""),
        "resume_version": job.get("resume_version", 1),
        "status": "submitted",
        "browser_session_id": req.session_id,
        "confirmation_screenshot_s3_key": req.confirmation_screenshot_key,
        "form_fields_detected": req.form_fields_detected,
        "form_fields_filled": req.form_fields_filled,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": False,
    }
    
    _db.client.table("applications").insert(app_row).execute()
    
    # Mirror to jobs.application_status
    canonical = job.get("canonical_hash")
    if canonical:
        _db.client.table("jobs").update(
            {"application_status": "Applied"}
        ).eq("user_id", user.id).eq("canonical_hash", canonical).execute()
    
    # Timeline event
    _db.client.table("application_timeline").insert({
        "user_id": user.id,
        "job_id": req.job_id,
        "status": "Applied",
        "notes": f"Cloud browser via {job.get('apply_platform', 'unknown')}",
    }).execute()
    
    return {"status": "recorded"}
```

### 9.4 New endpoint: `POST /api/apply/stop-session`

```python
@app.post("/api/apply/stop-session")
def stop_browser_session(
    session_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Stop a cloud browser session and save the profile."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("naukribaba-browser-sessions")
    session = table.get_item(Key={"session_id": session_id}).get("Item")
    
    if not session or session["user_id"] != user.id:
        raise HTTPException(404)
    
    # GRACEFUL SHUTDOWN: Send end_session via WS first (30s grace for profile save).
    # The browser_session.py event loop saves profile then exits.
    # Only force-stop if the task doesnt exit within 30s.
    # NOTE: The current Lambda timeout (15s) is too short for a 30s grace
    # period. Either increase Lambda timeout to 45s or make stop-session async.
    # Stop the Fargate task (force — TODO: implement graceful WS signal in P3)
    ecs = boto3.client("ecs")
    ecs.stop_task(
        cluster="naukribaba-scrapers"  # existing ECS cluster from template.yaml,
        task=session["fargate_task_arn"],
        reason="User ended session",
    )
    
    # Update DynamoDB
    table.update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "ended"},
    )
    
    return {"status": "stopped"}
```

## 10. Frontend — Apply Tab

### 10.1 New React Components

```
web/src/
├── components/
│   ├── apply/
│   │   ├── BrowserStream.jsx      (WebSocket canvas viewer)
│   │   ├── AnswerPanel.jsx         (AI answers sidebar)
│   │   ├── SessionControls.jsx     (Auto-Fill, Submit, Skip, End)
│   │   ├── SessionStatus.jsx       (status bar: starting/ready/filling/etc.)
│   │   └── EasyApplyForm.jsx       (existing — used as Mode 3 fallback)
│   ├── EasyApplyBadge.jsx          (existing — now triggers cloud browser)
│   └── EasyApplyModal.jsx          (existing wrapper)
```

### 10.2 BrowserStream.jsx — the live viewer

```jsx
// Core concept: WebSocket receives binary JPEG frames,
// renders them on a <canvas>. Captures mouse/keyboard events,
// sends them back over the WebSocket.

export default function BrowserStream({ wsUrl, sessionId, token, onFieldsDetected }) {
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  
  useEffect(() => {
    const ws = new WebSocket(`${wsUrl}?token=${token}&session=${sessionId}&role=frontend`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    
    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame = screenshot JPEG
        const blob = new Blob([event.data], { type: "image/jpeg" });
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          const ctx = canvasRef.current?.getContext("2d");
          if (ctx) ctx.drawImage(img, 0, 0);
          URL.revokeObjectURL(url);
        };
        img.src = url;
      } else {
        // Text frame = JSON message
        const msg = JSON.parse(event.data);
        if (msg.action === "fields") onFieldsDetected?.(msg);
        // ... handle other message types
      }
    };
    
    return () => ws.close();
  }, [wsUrl, sessionId, token]);
  
  // Mouse event handler — map canvas coordinates to viewport
  function handleClick(e) {
    const rect = canvasRef.current.getBoundingClientRect();
    const scaleX = 1280 / rect.width;
    const scaleY = 800 / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    wsRef.current?.send(JSON.stringify({ action: "click", x, y, button: "left" }));
  }
  
  // Keyboard handler
  function handleKeyDown(e) {
    e.preventDefault();
    if (e.key.length === 1) {
      wsRef.current?.send(JSON.stringify({ action: "type", text: e.key }));
    } else {
      wsRef.current?.send(JSON.stringify({ action: "key", key: e.key }));
    }
  }
  
  return (
    <canvas
      ref={canvasRef}
      width={1280} height={800}
      className="w-full border-2 border-black cursor-pointer"
      style={{ imageRendering: "auto" }}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      tabIndex={0}
    />
  );
}
```

### 10.2b Data flow: How AI answers reach the Answer Panel

The frontend makes TWO parallel calls when the user clicks "Apply":

1. `POST /api/apply/start-session` → launches Fargate, returns `session_id` + `ws_url`
2. `GET /api/apply/preview/{job_id}` → returns AI-generated answers for all expected fields

The preview response includes `custom_questions[].ai_answer` (same endpoint from the Mode 1 spec — it generates answers using the user's profile + tailored resume + job description).

When the Fargate session sends a `{action: "fields", fields: [...]}` message (detected form fields), the frontend **merges** the detected fields with the AI answers from the preview response by matching on `field.label` or `field.id`. The Answer Panel shows the merged result: each detected field with its AI-suggested answer pre-filled.

If a detected field has no matching AI answer (new question not in the preview), it shows as empty and the user fills it manually.

```jsx
// In the Apply tab component:
const [preview, setPreview] = useState(null);
const [detectedFields, setDetectedFields] = useState([]);

useEffect(() => {
  // Fetch AI answers in parallel with session start
  getApplyPreview(jobId).then(setPreview);
}, [jobId]);

function onFieldsDetected(fieldsMsg) {
  setDetectedFields(fieldsMsg.fields);
  // Merge AI answers from preview with detected fields
  // Match by label similarity (fuzzy) since field IDs differ between
  // our preview's question_ids and the actual DOM field IDs
}
```

### 10.3 AnswerPanel.jsx — AI answers sidebar

Shows the detected fields with AI-generated answers. User can edit before auto-fill.

```jsx
export default function AnswerPanel({ fields, aiAnswers, onAnswerChange, onFillAll }) {
  if (!fields?.length) return <p className="text-sm text-stone-500">Waiting for form detection...</p>;
  
  return (
    <div className="space-y-2 overflow-y-auto max-h-[600px]">
      <div className="flex justify-between items-center mb-3">
        <p className="text-[10px] font-bold text-stone-500 uppercase tracking-wider">
          {fields.length} Fields Detected
        </p>
        <button onClick={onFillAll}
          className="text-xs font-bold bg-yellow border-2 border-black px-2 py-1 hover:bg-yellow-dark">
          ⚡ Fill All
        </button>
      </div>
      
      {fields.map((field) => (
        <div key={field.id} className="border border-stone-200 p-2 rounded-sm">
          <label className="text-[10px] font-bold text-stone-500 block mb-1">
            {field.label} {field.required && <span className="text-red-600">*</span>}
          </label>
          {field.type === 'select' && field.options ? (
            <select
              value={aiAnswers[field.id] || ''}
              onChange={(e) => onAnswerChange(field.id, e.target.value)}
              className="w-full text-xs border border-black px-1 py-0.5"
            >
              <option value="">— select —</option>
              {field.options.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={aiAnswers[field.id] || ''}
              onChange={(e) => onAnswerChange(field.id, e.target.value)}
              className="w-full text-xs border border-black px-1 py-0.5"
            />
          )}
        </div>
      ))}
    </div>
  );
}
```

## 10.4 Error Recovery — Chrome crash / WebSocket disconnect

If Chrome crashes or Fargate terminates unexpectedly:

1. WebSocket connection drops → frontend's `ws.onclose` fires
2. Frontend shows **"Session lost"** banner with explanation
3. **"Restart Session"** button starts a new Fargate task
4. Old DynamoDB session row expires via TTL (30 min)
5. Profile may NOT have been saved (crash before cleanup)
   - Next session starts fresh if no saved profile exists
   - User may need to re-login to the platform

If the user closes their browser tab:
1. WebSocket disconnects → $disconnect Lambda fires
2. Fargate continues running for `IDLE_TIMEOUT` (5 min)
3. If user reopens within 5 min, frontend reconnects to same session
4. If idle timeout elapses → profile saved → Fargate terminates

---

## 11. Mode 3 Fallback (Assisted Manual)

If the cloud browser fails (Fargate unavailable, CDP blocked, etc.), the Apply tab falls back to Mode 3:

1. All AI-generated answers from the preview endpoint are displayed
2. "Copy All" button copies all answers to clipboard as formatted text
3. "Open Application Page" opens the external URL in a new tab
4. User pastes answers manually
5. User returns and clicks "Mark as Applied"

This reuses the `EasyApplyForm.jsx` from the previous spec. No cloud browser needed. Zero infrastructure cost.

The frontend detects the fallback condition:
- `POST /api/apply/start-session` returns 503 (Fargate unavailable) → show Mode 3
- WebSocket connection fails → show Mode 3
- User preference: "I prefer copy-paste mode" toggle in settings

## 12. Security

### 12.1 Session isolation
- Each Fargate task runs in its own container with its own Chrome profile
- S3 profiles are scoped to `sessions/{user_id}/` — RLS-like path scoping
- WebSocket auth validates JWT on $connect — no unauthenticated access
- DynamoDB sessions are keyed by session_id, verified against user_id

### 12.2 Cookie handling
- Chrome profiles contain session cookies for job platforms (LinkedIn, etc.)
- Cookies are stored in S3 under the user's path, encrypted at rest (S3 SSE)
- Cookies are NEVER sent to the frontend — they stay on Fargate
- When the Fargate task ends, the profile is saved to S3 and the container is destroyed

### 12.3 Credential handling
- User types their credentials INTO THE STREAM — they flow from React → WebSocket → Fargate → Chrome
- Credentials are NOT stored by NaukriBaba — Chrome handles them natively
- Password manager autofill works in the cloud Chrome (from saved profile)
- 2FA codes: user sees the 2FA prompt in the stream, enters the code manually

### 12.4 Anti-detection
- Use real Chrome (not Chromium) with `--disable-blink-features=AutomationControlled`
- Remove `navigator.webdriver` flag
- Human-like typing delays (50ms per keystroke)
- Random mouse movements before clicks
- Real viewport size (1280x800, not a headless default)
- Real user-agent string
- Bright Data proxy option for sites with IP-based blocking

## 13. Cost Analysis

**Corrected estimates** (accounting for Management API calls, data transfer, and real message counts):

| Component | Per application | 100 apps/month | Notes |
|-----------|----------------|-----------------|-------|
| Fargate (1vCPU, 3GB, 5 min) | $0.018 | $1.80 | Matches existing PlaywrightTaskDef sizing |
| API GW Management API (~1500 POST calls for screenshots) | $0.005 | $0.50 | $3.50/million API calls |
| API GW WebSocket (text messages ~100) | $0.0001 | $0.01 | Commands + field data only |
| Data transfer (75MB screenshots per session) | $0.007 | $0.70 | $0.09/GB |
| CapSolver (1 CAPTCHA avg) | $0.01 | $1.00 | Some jobs have 0, some 2 |
| S3 (profile 10MB + screenshot) | $0.001 | $0.10 | Profiles cached, not re-uploaded every time |
| DynamoDB (session CRUD) | Free tier | $0.00 | |
| Lambda (WS connect/disconnect + start/stop) | Free tier | $0.00 | ~4 invocations per session |
| **Total** | **~$0.04** | **~$4.11** | |

At 50 applications/month (more realistic), total is ~$2. Still much cheaper than any SaaS alternative ($29-40/month for Simplify/rtrvr.ai).

## 14. Implementation Plan (High Level)

| Phase | What | Sessions |
|-------|------|----------|
| **P0** | Migration + Phase 0 cleanup (from previous spec) | 0.5 |
| **P1** | Infrastructure: WebSocket API Gateway + DynamoDB + Fargate task def in SAM | 1 |
| **P2** | `browser_session.py`: Chrome launch, profile persistence, screenshot streaming | 1 |
| **P3** | WebSocket Lambdas: connect, disconnect, route | 0.5 |
| **P4** | Form detection + field filling + CAPTCHA solving | 1 |
| **P5** | Frontend: BrowserStream, AnswerPanel, session management | 1 |
| **P6** | Backend: start-session, stop-session, record-application endpoints | 0.5 |
| **P7** | Integration: wire preview AI answers → answer panel → fill commands | 0.5 |
| **P8** | Mode 3 fallback (copy-paste) | 0.5 |
| **P9** | Testing: E2E dry-run against Greenhouse + LinkedIn | 1 |
| **P10** | Deploy + first real application | 0.5 |
| **Total** | | **~7 sessions** |

## 15. Success Criteria

- [ ] Cloud Chrome session starts in <45 seconds from button click (Fargate cold start is 30-60s; warm reuse is <10s)
- [ ] Screenshot stream renders smoothly at 5fps in the Apply tab
- [ ] Mouse/keyboard events forwarded with <200ms latency
- [ ] AI pre-fills 90%+ of form fields correctly
- [ ] CapSolver handles hCaptcha and reCAPTCHA automatically
- [ ] Profile persistence: log into LinkedIn once, stay logged in across sessions
- [ ] One successful real application submitted via cloud browser
- [ ] Mode 3 fallback works when Fargate is unavailable
- [ ] All existing tests still pass (714+)
- [ ] Cost per application < $0.05

## 16. What This Enables (Future)

Once the cloud browser infrastructure exists, it unlocks:

- **Batch Apply**: "Apply to 10 S-tier jobs" — sequential auto-fill with quick review per job
- **Application monitoring**: Periodic cloud browser checks on platform candidate portals — "has my application been viewed?"
- **Interview scheduling**: Cloud browser navigates to scheduling links, picks available slots
- **Multi-platform support**: Any website with a form — not just job applications
- **Scraping enhancement**: Use authenticated sessions for platforms that block anonymous scraping (LinkedIn, Glassdoor)

---

*This spec supersedes the API-POST approach (2026-04-11) after discovering that both Greenhouse and Ashby require per-company API keys for submission. The cloud browser approach is platform-agnostic and future-proof.*
