# Auto-Apply Plan 2: Browser Session Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Fargate Chrome container that runs headless Chrome, streams screenshots to the frontend via API Gateway Management API, detects/fills application forms, solves CAPTCHAs, and persists login profiles to S3.

**Architecture:** A Python async entrypoint (`browser_session.py`) launches Playwright Chrome, connects to WebSocket API Gateway, and runs 3 concurrent loops (screenshot streaming, command handling, idle monitoring) coordinated by a shared `asyncio.Event`. Form detection uses injected JavaScript. CAPTCHA solving uses the CapSolver API. Chrome profiles are tar.gz compressed and persisted to S3 between sessions.

**Tech Stack:** Python 3.11, Playwright (async), websockets, boto3/aioboto3, httpx, asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-auto-apply-cloud-browser-design.md` (§6-10)

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `browser/Dockerfile` | Container image: Playwright + Chrome + Python deps |
| Create | `browser/requirements.txt` | Python dependencies |
| Create | `browser/profile_manager.py` | S3 profile save/load/cleanup |
| Create | `browser/js_constants.py` | Form detection + CAPTCHA detection JavaScript strings |
| Create | `browser/captcha_solver.py` | CapSolver API integration |
| Create | `browser/browser_session.py` | Main entrypoint: Chrome, WebSocket, 3-loop event loop |
| Create | `supabase/migrations/20260420_cloud_browser_schema.sql` | Add browser_session_id + form tracking columns to applications |

---

### Task 1: Dockerfile + requirements.txt

**Files:**
- Create: `browser/Dockerfile`
- Create: `browser/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```
playwright==1.52.0
websockets>=13.0,<14.0
boto3>=1.35.0
aioboto3>=13.0.0
httpx>=0.27.0
```

- [ ] **Step 2: Create the Dockerfile**

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium (skip Firefox/WebKit)
RUN playwright install chromium

# Copy application code
COPY *.py ./

# Entrypoint
CMD ["python", "browser_session.py"]
```

- [ ] **Step 3: Commit**

```bash
git add browser/Dockerfile browser/requirements.txt
git commit -m "feat(browser): add Dockerfile and requirements for Chrome container"
```

---

### Task 2: Profile Manager (S3 save/load)

**Files:**
- Create: `browser/profile_manager.py`

- [ ] **Step 1: Create profile_manager.py**

```python
"""Chrome profile persistence — save/load user profiles to/from S3 as tar.gz."""

import logging
import shutil
import tarfile
from io import BytesIO
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)

PROFILE_DIR = Path("/tmp/chrome-profile")

# Cache directories to strip before saving (100MB → 5-10MB)
CACHE_DIRS = [
    "Cache", "Code Cache", "GPUCache", "Service Worker",
    "DawnCache", "DawnGraphiteCache", "ShaderCache",
]


def load_profile(s3_bucket: str, user_id: str, platform: str) -> bool:
    """Download and extract Chrome profile from S3. Returns True if profile existed."""
    key = f"sessions/{user_id}/{platform}/profile.tar.gz"
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=s3_bucket, Key=key)
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=BytesIO(obj["Body"].read()), mode="r:gz") as tar:
            tar.extractall(PROFILE_DIR)
        logger.info(f"Loaded profile from s3://{s3_bucket}/{key}")
        return True
    except s3.exceptions.NoSuchKey:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("No existing profile found, starting fresh")
        return False
    except Exception as e:
        logger.warning(f"Failed to load profile: {e}")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return False


def save_profile(s3_bucket: str, user_id: str, platform: str) -> None:
    """Clean up Chrome profile and upload to S3 as tar.gz."""
    if not PROFILE_DIR.exists():
        logger.warning("Profile directory does not exist, skipping save")
        return

    _cleanup_profile()

    key = f"sessions/{user_id}/{platform}/profile.tar.gz"
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(PROFILE_DIR), arcname=".")
    buf.seek(0)

    s3 = boto3.client("s3")
    s3.put_object(Bucket=s3_bucket, Key=key, Body=buf.read())
    logger.info(f"Saved profile to s3://{s3_bucket}/{key}")


def _cleanup_profile() -> None:
    """Remove lock files and cache directories to reduce profile size."""
    # Delete Chrome lock files (prevent corruption on next load)
    for pattern in ["**/Singleton*", "**/*.lock"]:
        for lock_file in PROFILE_DIR.glob(pattern):
            lock_file.unlink(missing_ok=True)

    # Strip cache directories
    for cache_dir in CACHE_DIRS:
        cache_path = PROFILE_DIR / "Default" / cache_dir
        if cache_path.exists():
            shutil.rmtree(cache_path, ignore_errors=True)
```

- [ ] **Step 2: Commit**

```bash
git add browser/profile_manager.py
git commit -m "feat(browser): add S3 profile save/load for Chrome sessions"
```

---

### Task 3: JavaScript Constants (Form + CAPTCHA Detection)

**Files:**
- Create: `browser/js_constants.py`

- [ ] **Step 1: Create js_constants.py**

```python
"""JavaScript constants injected into the browser page for form and CAPTCHA detection."""

# Detects all visible form fields, labels, options, and the submit button.
# Handles: input, textarea, select, radio groups, hidden file inputs, iframe fields.
FORM_DETECTION_JS = """() => {
    const fields = [];
    const seen = new Set();

    // Main field selector
    const els = document.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select'
    );

    for (let i = 0; i < els.length; i++) {
        const el = els[i];
        const rect = el.getBoundingClientRect();

        // Skip invisible elements (except file inputs which are often hidden)
        if (el.offsetParent === null && el.type !== 'file') continue;
        if (rect.width === 0 && rect.height === 0 && el.type !== 'file') continue;

        const id = el.id || el.name || ('field_' + i);
        if (seen.has(id)) continue;
        seen.add(id);

        // Detect label
        let label = '';
        let labelEl = el.labels && el.labels[0];
        if (!labelEl) labelEl = el.closest('label');
        if (!labelEl) {
            const wrapper = el.closest('[class*=field], [class*=question], [class*=form-group]');
            if (wrapper) labelEl = wrapper.querySelector('label, [class*=label]');
        }
        if (labelEl) label = labelEl.textContent.trim();
        if (!label) label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
        label = label.substring(0, 200);

        const field = {
            id: id,
            name: el.name || '',
            label: label,
            type: el.type || el.tagName.toLowerCase(),
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            options: null,
            rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height)
            }
        };

        // Select options
        if (el.tagName === 'SELECT') {
            field.options = Array.from(el.options)
                .map(o => ({ label: o.textContent.trim(), value: o.value }))
                .filter(o => o.value);
        }

        // Radio group options
        if (el.type === 'radio') {
            const group = document.querySelectorAll('input[name="' + el.name + '"]');
            field.options = Array.from(group).map(r => ({
                label: (r.labels && r.labels[0] ? r.labels[0].textContent : r.value).trim(),
                value: r.value
            }));
        }

        fields.push(field);
    }

    // Capture hidden file inputs (Greenhouse uses these for drag-drop resume upload)
    const fileInputs = document.querySelectorAll('input[type=file]');
    for (const fi of fileInputs) {
        const id = fi.id || fi.name || 'file_input';
        if (!seen.has(id)) {
            seen.add(id);
            fields.push({
                id: id,
                name: fi.name || '',
                label: fi.getAttribute('aria-label') || 'File Upload',
                type: 'file',
                required: fi.required,
                value: '',
                options: null,
                rect: { x: 0, y: 0, w: 0, h: 0 }
            });
        }
    }

    // Find submit button
    const submitBtn = document.querySelector(
        'button[type=submit], input[type=submit], ' +
        'button:not([type])[class*=submit], button:not([type])[class*=apply]'
    ) || document.querySelector('button:not([type])');

    return {
        fields: fields,
        submit_button: submitBtn ? {
            text: submitBtn.textContent.trim(),
            selector: submitBtn.id ? '#' + submitBtn.id : null
        } : null,
        page_title: document.title,
        page_url: window.location.href
    };
}"""


# Detects CAPTCHA type and sitekey on the current page.
# Checks: hCaptcha, reCAPTCHA v2/v3, Cloudflare Turnstile.
CAPTCHA_DETECTION_JS = """() => {
    const r = {};

    // hCaptcha (check first — some sites use both)
    const hc = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
    if (hc) {
        r.type = 'hcaptcha';
        r.sitekey = hc.dataset.sitekey || hc.dataset.hcaptchaSitekey || '';
        return r;
    }

    // reCAPTCHA v2
    const rc2 = document.querySelector('.g-recaptcha, [data-sitekey]');
    if (rc2) {
        r.type = 'recaptcha_v2';
        r.sitekey = rc2.dataset.sitekey || '';
        return r;
    }

    // reCAPTCHA v3 (invisible)
    const rcScript = document.querySelector('script[src*="recaptcha/api.js?render="]');
    if (rcScript) {
        const match = rcScript.src.match(/render=([^&]+)/);
        if (match) {
            r.type = 'recaptcha_v3';
            r.sitekey = match[1];
            return r;
        }
    }

    // Cloudflare Turnstile
    const cf = document.querySelector('.cf-turnstile');
    if (cf) {
        r.type = 'turnstile';
        r.sitekey = cf.dataset.sitekey || '';
        return r;
    }

    return r;
}"""


# Detects if the page is a login page (for platforms requiring manual auth).
LOGIN_DETECTION_JS = """() => {
    const url = window.location.href.toLowerCase();
    const html = document.body ? document.body.innerText.toLowerCase() : '';
    const loginKeywords = ['sign in', 'log in', 'login', 'sso', 'authenticate'];
    const hasPasswordField = !!document.querySelector('input[type=password]');
    const hasLoginKeyword = loginKeywords.some(k => html.includes(k) || url.includes(k));
    return {
        login_required: hasPasswordField && hasLoginKeyword,
        url: window.location.href
    };
}"""
```

- [ ] **Step 2: Commit**

```bash
git add browser/js_constants.py
git commit -m "feat(browser): add form detection, CAPTCHA detection, login detection JavaScript"
```

---

### Task 4: CAPTCHA Solver

**Files:**
- Create: `browser/captcha_solver.py`

- [ ] **Step 1: Create captcha_solver.py**

```python
"""CAPTCHA detection and solving via CapSolver API."""

import asyncio
import json
import logging

import httpx

logger = logging.getLogger(__name__)

CAPSOLVER_URL = "https://api.capsolver.com"

TASK_TYPES = {
    "hcaptcha": "HCaptchaTaskProxyLess",
    "recaptcha_v2": "ReCaptchaV2TaskProxyLess",
    "recaptcha_v3": "ReCaptchaV3TaskProxyLess",
    "turnstile": "AntiTurnstileTaskProxyLess",
}


async def solve_captcha(page, captcha_info: dict, ws, capsolver_key: str) -> bool:
    """Detect and solve CAPTCHA using CapSolver. Returns True if solved."""
    captcha_type = captcha_info.get("type")
    sitekey = captcha_info.get("sitekey")
    page_url = page.url

    if not captcha_type or not sitekey:
        return False

    task_type = TASK_TYPES.get(captcha_type)
    if not task_type:
        logger.warning(f"Unknown CAPTCHA type: {captcha_type}")
        return False

    if not capsolver_key:
        await ws.send(json.dumps({
            "action": "status",
            "status": "captcha_detected",
            "type": captcha_type,
            "message": "CAPTCHA detected but no solver configured. Please solve manually.",
        }))
        return False

    # Notify frontend
    await ws.send(json.dumps({
        "action": "status",
        "status": "captcha_detected",
        "type": captcha_type,
        "message": f"Solving {captcha_type} automatically...",
    }))

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            # Create task
            create_resp = await client.post(f"{CAPSOLVER_URL}/createTask", json={
                "clientKey": capsolver_key,
                "task": {
                    "type": task_type,
                    "websiteURL": page_url,
                    "websiteKey": sitekey,
                },
            })
            create_data = create_resp.json()
            task_id = create_data.get("taskId")
            if not task_id:
                logger.error(f"CapSolver createTask failed: {create_data}")
                return False

            # Poll for result (max 60 seconds)
            for _ in range(30):
                await asyncio.sleep(2)
                result_resp = await client.post(f"{CAPSOLVER_URL}/getTaskResult", json={
                    "clientKey": capsolver_key,
                    "taskId": task_id,
                })
                result = result_resp.json()
                if result.get("status") == "ready":
                    token = (
                        result["solution"].get("gRecaptchaResponse")
                        or result["solution"].get("token")
                        or ""
                    )
                    if token:
                        await _inject_token(page, captcha_type, token)
                        await ws.send(json.dumps({
                            "action": "status",
                            "status": "captcha_solved",
                        }))
                        logger.info(f"CAPTCHA solved: {captcha_type}")
                        return True

            logger.warning("CapSolver timed out")
            return False

    except Exception as e:
        logger.error(f"CAPTCHA solving failed: {e}")
        return False


async def _inject_token(page, captcha_type: str, token: str) -> None:
    """Inject solved CAPTCHA token into the page. Token is passed as parameter to prevent injection."""
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

- [ ] **Step 2: Commit**

```bash
git add browser/captcha_solver.py
git commit -m "feat(browser): add CAPTCHA detection and CapSolver integration"
```

---

### Task 5: browser_session.py — Main Entrypoint

**Files:**
- Create: `browser/browser_session.py`

This is the largest task — the main async entrypoint that orchestrates Chrome, WebSocket, and the 3 concurrent loops.

- [ ] **Step 1: Create browser_session.py**

```python
"""Cloud browser session — Fargate entrypoint for Chrome-based job application automation.

Launches headless Chrome via Playwright, connects to WebSocket API Gateway,
and runs 3 concurrent loops:
  1. screenshot_loop — streams JPEG frames to frontend via Management API
  2. command_loop — processes user actions (click, type, fill, submit)
  3. idle_monitor — enforces idle and session timeouts

Env vars: SESSION_ID, USER_ID, JOB_ID, APPLY_URL, PLATFORM, WEBSOCKET_URL,
          WS_TOKEN, S3_BUCKET, CAPSOLVER_API_KEY, AWS_REGION
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import boto3
import websockets
from playwright.async_api import async_playwright

from captcha_solver import solve_captcha
from js_constants import CAPTCHA_DETECTION_JS, FORM_DETECTION_JS, LOGIN_DETECTION_JS
from profile_manager import PROFILE_DIR, load_profile, save_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────
SESSION_ID = os.environ["SESSION_ID"]
USER_ID = os.environ["USER_ID"]
JOB_ID = os.environ["JOB_ID"]
APPLY_URL = os.environ["APPLY_URL"]
PLATFORM = os.environ.get("PLATFORM", "unknown")
WS_URL = os.environ["WEBSOCKET_URL"]
WS_TOKEN = os.environ["WS_TOKEN"]
S3_BUCKET = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")
CAPSOLVER_KEY = os.environ.get("CAPSOLVER_API_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_FPS = 5
SCREENSHOT_QUALITY_START = 75
SCREENSHOT_MAX_BYTES = 120_000
IDLE_TIMEOUT = 300       # 5 min idle → shutdown
SESSION_TIMEOUT = 1800   # 30 min max session


# ─── DynamoDB helpers ────────────────────────────────────────────
_ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
_sessions_table = _ddb.Table(os.environ.get("SESSIONS_TABLE", "naukribaba-browser-sessions"))


def _get_frontend_connection_id() -> str | None:
    """Read frontend WebSocket connection ID from DynamoDB."""
    try:
        resp = _sessions_table.get_item(Key={"session_id": SESSION_ID})
        return resp.get("Item", {}).get("ws_connection_frontend")
    except Exception as e:
        logger.warning(f"Failed to get frontend connection ID: {e}")
        return None


def _update_session_status(status: str) -> None:
    """Update session status in DynamoDB."""
    try:
        _sessions_table.update_item(
            Key={"session_id": SESSION_ID},
            UpdateExpression="SET #s = :s, last_activity_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":t": int(time.time())},
        )
    except Exception as e:
        logger.warning(f"Failed to update session status: {e}")


# ─── Form detection + filling ────────────────────────────────────
async def _detect_and_send_fields(page, ws) -> None:
    """Detect form fields in main frame + all iframes and send to frontend."""
    # Main frame
    main_result = await page.evaluate(FORM_DETECTION_JS)
    all_fields = main_result.get("fields", [])
    submit_button = main_result.get("submit_button")

    # Scan iframes
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
            pass  # Cross-origin or detached frame

    await ws.send(json.dumps({
        "action": "fields",
        "fields": all_fields,
        "submit_button": submit_button,
        "page_title": main_result.get("page_title", ""),
        "page_url": main_result.get("page_url", ""),
    }))
    logger.info(f"Detected {len(all_fields)} fields")


async def _fill_single_field(page, field_id: str, value: str, frame_url: str | None = None) -> None:
    """Fill a single form field by ID, optionally in an iframe."""
    target = page
    if frame_url:
        target = next((f for f in page.frames if f.url == frame_url), page)

    el = await target.query_selector(f"#{field_id}")
    if not el:
        el = await target.query_selector(f"[name='{field_id}']")
    if not el:
        raise ValueError(f"Field not found: {field_id}")

    tag = await el.evaluate("el => el.tagName.toLowerCase()")
    input_type = await el.evaluate("el => el.type || ''")

    if tag == "select":
        await el.select_option(value=value)
    elif input_type == "radio":
        radio = await target.query_selector(f"input[name='{field_id}'][value='{value}']")
        if radio:
            await radio.click()
    elif input_type == "checkbox":
        checked = await el.is_checked()
        if (value.lower() in ("true", "yes", "1")) != checked:
            await el.click()
    elif input_type == "file":
        # File upload — value is the local path
        await el.set_input_files(value)
    else:
        await el.click()
        await el.fill("")  # Clear first
        await el.fill(value)


async def _handle_fill_all(page, ws, answers: dict) -> None:
    """Fill all fields from the answers dict."""
    _update_session_status("filling")
    await ws.send(json.dumps({"action": "status", "status": "filling"}))

    filled_count = 0
    for field_id, value in answers.items():
        if field_id.startswith("_"):
            continue  # Skip metadata keys like _resume_s3_key

        try:
            frame_url = answers.get(f"_{field_id}_frame_url")
            await _fill_single_field(page, field_id, str(value), frame_url)
            await ws.send(json.dumps({
                "action": "field_filled", "field_id": field_id, "success": True,
            }))
            filled_count += 1
        except Exception as e:
            logger.warning(f"Failed to fill {field_id}: {e}")
            await ws.send(json.dumps({
                "action": "field_filled", "field_id": field_id,
                "success": False, "error": str(e)[:100],
            }))

    await ws.send(json.dumps({
        "action": "status", "status": "filled", "count": filled_count,
    }))


async def _handle_upload_resume(page, resume_s3_key: str) -> None:
    """Download resume PDF from S3 and upload via file input."""
    local_path = Path("/tmp/resume.pdf")
    s3 = boto3.client("s3")
    s3.download_file(S3_BUCKET, resume_s3_key, str(local_path))

    file_input = await page.query_selector("input[type=file]")
    if file_input:
        await file_input.set_input_files(str(local_path))
        logger.info(f"Uploaded resume from s3://{S3_BUCKET}/{resume_s3_key}")
    else:
        logger.warning("No file input found for resume upload")


async def _handle_submit(page, ws) -> None:
    """Click submit button, handle CAPTCHA if present."""
    await ws.send(json.dumps({"action": "status", "status": "submitting"}))
    _update_session_status("submitting")

    # Check for CAPTCHA
    captcha = await page.evaluate(CAPTCHA_DETECTION_JS)
    if captcha.get("type"):
        solved = await solve_captcha(page, captcha, ws, CAPSOLVER_KEY)
        if not solved:
            await ws.send(json.dumps({
                "action": "status", "status": "captcha_detected",
                "type": captcha["type"],
                "message": "Please solve the CAPTCHA manually, then click Submit again.",
            }))
            return

    # Find and click submit
    submit_selectors = [
        "button[type=submit]", "input[type=submit]",
        "button[class*=submit]", "button[class*=apply]",
        "button:not([type])",
    ]
    for selector in submit_selectors:
        btn = await page.query_selector(selector)
        if btn and await btn.is_visible():
            await btn.click()
            break

    # Wait for page response
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    await ws.send(json.dumps({"action": "status", "status": "submitted"}))
    _update_session_status("idle")
    logger.info("Form submitted")


# ─── Concurrent loops ────────────────────────────────────────────
async def _screenshot_loop(page, stop_event: asyncio.Event, frontend_conn_id: str) -> None:
    """Stream JPEG screenshots to frontend via API Gateway Management API."""
    mgmt_url = f"https://{os.environ.get('WEBSOCKET_API_ID', '')}.execute-api.{AWS_REGION}.amazonaws.com/prod"
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=mgmt_url, region_name=AWS_REGION)
    quality = SCREENSHOT_QUALITY_START

    while not stop_event.is_set():
        try:
            if not frontend_conn_id:
                frontend_conn_id = _get_frontend_connection_id() or ""
                if not frontend_conn_id:
                    await asyncio.sleep(1)
                    continue

            screenshot = await page.screenshot(type="jpeg", quality=quality)

            # Adaptive quality reduction
            if len(screenshot) > SCREENSHOT_MAX_BYTES and quality > 60:
                quality = 60
                screenshot = await page.screenshot(type="jpeg", quality=quality)
            if len(screenshot) > SCREENSHOT_MAX_BYTES and quality > 45:
                quality = 45
                screenshot = await page.screenshot(type="jpeg", quality=quality)

            if len(screenshot) <= 128_000:
                apigw.post_to_connection(
                    ConnectionId=frontend_conn_id,
                    Data=screenshot,
                )

            # Reset quality for next frame
            quality = SCREENSHOT_QUALITY_START

        except apigw.exceptions.GoneException:
            logger.info("Frontend disconnected (GoneException)")
            frontend_conn_id = ""
        except Exception as e:
            if "GoneException" in str(type(e)):
                frontend_conn_id = ""
            else:
                logger.warning(f"Screenshot error: {e}")

        await asyncio.sleep(1.0 / SCREENSHOT_FPS)


async def _command_loop(page, ws, stop_event: asyncio.Event, last_activity: list) -> None:
    """Process incoming WebSocket commands from frontend."""
    try:
        async for message in ws:
            if stop_event.is_set():
                break
            if not isinstance(message, str):
                continue

            last_activity[0] = time.time()
            cmd = json.loads(message)
            action = cmd.get("action")

            if action == "click":
                await page.mouse.click(cmd["x"], cmd["y"], button=cmd.get("button", "left"))

            elif action == "type":
                await page.keyboard.type(cmd["text"])

            elif action == "key":
                key = cmd["key"]
                modifiers = cmd.get("modifiers", [])
                for mod in modifiers:
                    await page.keyboard.down(mod)
                await page.keyboard.press(key)
                for mod in reversed(modifiers):
                    await page.keyboard.up(mod)

            elif action == "scroll":
                await page.mouse.wheel(0, cmd.get("deltaY", 300))

            elif action == "navigate":
                await page.goto(cmd["url"], wait_until="networkidle")
                await _detect_and_send_fields(page, ws)

            elif action == "detect_fields":
                await _detect_and_send_fields(page, ws)

            elif action == "fill_all":
                await _handle_fill_all(page, ws, cmd.get("answers", {}))

            elif action == "fill_field":
                try:
                    await _fill_single_field(page, cmd["field_id"], cmd["value"])
                    await ws.send(json.dumps({
                        "action": "field_filled", "field_id": cmd["field_id"], "success": True,
                    }))
                except Exception as e:
                    await ws.send(json.dumps({
                        "action": "field_filled", "field_id": cmd["field_id"],
                        "success": False, "error": str(e)[:100],
                    }))

            elif action == "upload_resume":
                s3_key = cmd.get("resume_s3_key", "")
                if s3_key:
                    await _handle_upload_resume(page, s3_key)

            elif action == "submit":
                await _handle_submit(page, ws)
                await asyncio.sleep(2)
                await _detect_and_send_fields(page, ws)

            elif action == "next_job":
                _sessions_table.update_item(
                    Key={"session_id": SESSION_ID},
                    UpdateExpression="SET current_job_id = :jid",
                    ExpressionAttributeValues={":jid": cmd.get("job_id", "")},
                )
                await page.goto(cmd["apply_url"], wait_until="networkidle")
                await _detect_and_send_fields(page, ws)

            elif action == "end_session":
                logger.info("End session requested by user")
                stop_event.set()
                break

    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket closed by frontend")
        stop_event.set()


async def _idle_monitor(stop_event: asyncio.Event, last_activity: list, session_start: float) -> None:
    """Enforce idle and session timeouts."""
    while not stop_event.is_set():
        await asyncio.sleep(30)
        elapsed = time.time() - last_activity[0]
        total = time.time() - session_start
        if elapsed > IDLE_TIMEOUT:
            logger.info("Idle timeout reached")
            stop_event.set()
        if total > SESSION_TIMEOUT:
            logger.info("Max session timeout reached")
            stop_event.set()


# ─── Main entrypoint ─────────────────────────────────────────────
async def main():
    logger.info(f"Starting browser session: {SESSION_ID} for job {JOB_ID}")
    _update_session_status("starting")

    # 1. Load Chrome profile from S3
    profile_loaded = load_profile(S3_BUCKET, USER_ID, PLATFORM)
    logger.info(f"Profile loaded: {profile_loaded}")

    # 2. Launch Chrome
    pw = await async_playwright().start()
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=True,
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

    # 3. Connect WebSocket
    ws_full_url = f"{WS_URL}?token={WS_TOKEN}&session={SESSION_ID}&role=browser"

    try:
        async with websockets.connect(ws_full_url) as ws:
            # 4. Navigate to apply URL
            _update_session_status("navigating")
            await ws.send(json.dumps({"action": "status", "status": "navigating"}))
            await page.goto(APPLY_URL, wait_until="networkidle", timeout=30000)

            # 5. Wait for form elements
            try:
                await page.wait_for_selector(
                    "form, input:not([type=hidden]), textarea, [class*=field], [class*=question]",
                    timeout=10000,
                )
            except Exception:
                pass  # Non-standard page layout

            # 6. Check for login page
            login_info = await page.evaluate(LOGIN_DETECTION_JS)
            if login_info.get("login_required"):
                await ws.send(json.dumps({
                    "action": "status",
                    "status": "login_required",
                    "platform": PLATFORM,
                }))
                # Wait up to 2 min for user to log in (screenshot loop shows them the page)
                for _ in range(120):
                    await asyncio.sleep(1)
                    login_info = await page.evaluate(LOGIN_DETECTION_JS)
                    if not login_info.get("login_required"):
                        break

            # 7. Detect and send form fields
            _update_session_status("ready")
            await ws.send(json.dumps({"action": "status", "status": "ready"}))
            await _detect_and_send_fields(page, ws)

            # 8. Get frontend connection ID
            frontend_conn_id = _get_frontend_connection_id() or ""

            # 9. Run 3 concurrent loops
            stop_event = asyncio.Event()
            last_activity = [time.time()]
            session_start = time.time()

            try:
                await asyncio.gather(
                    _screenshot_loop(page, stop_event, frontend_conn_id),
                    _command_loop(page, ws, stop_event, last_activity),
                    _idle_monitor(stop_event, last_activity, session_start),
                    return_exceptions=True,
                )
            except Exception as e:
                logger.error(f"Session error: {e}")

    except Exception as e:
        logger.error(f"Failed to connect or run session: {e}")

    # 10. Cleanup
    logger.info("Shutting down browser session")
    _update_session_status("ended")
    await browser.close()
    await pw.stop()

    # 11. Save profile to S3
    save_profile(S3_BUCKET, USER_ID, PLATFORM)
    logger.info("Browser session complete")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
git add browser/browser_session.py
git commit -m "feat(browser): add main browser session entrypoint with 3-loop architecture"
```

---

### Task 6: Database Migration — Cloud Browser Schema Additions

**Files:**
- Create: `supabase/migrations/20260420_cloud_browser_schema.sql`

This adds the cloud browser-specific columns to the `applications` table (deferred from Plan 1) and expands the `submission_method` CHECK constraint.

- [ ] **Step 1: Create the migration**

```sql
-- Cloud browser schema additions for applications table
-- Expands submission_method to include 'cloud_browser',
-- adds browser session tracking and form field diagnostics

BEGIN;

-- 1. Drop and recreate CHECK constraint to add 'cloud_browser'
ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_submission_method_check;
ALTER TABLE applications ADD CONSTRAINT applications_submission_method_check
  CHECK (submission_method IN (
    'greenhouse_api', 'ashby_api', 'remote_browser', 'assisted_manual', 'cloud_browser'
  ));

-- 2. Add browser session tracking column
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS browser_session_id UUID;

-- 3. Add form field diagnostics
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS form_fields_detected INT,
  ADD COLUMN IF NOT EXISTS form_fields_filled INT;

COMMIT;
```

- [ ] **Step 2: Run the migration**

```bash
npx supabase db query --linked "$(cat supabase/migrations/20260420_cloud_browser_schema.sql)"
```

Verify:
```bash
npx supabase db query --linked "SELECT column_name FROM information_schema.columns WHERE table_name = 'applications' AND column_name IN ('browser_session_id', 'form_fields_detected', 'form_fields_filled');"
```

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260420_cloud_browser_schema.sql
git commit -m "feat(db): add cloud_browser submission method + session tracking columns"
```

---

## Summary

| Task | What | Files | Size |
|------|------|-------|------|
| 1 | Dockerfile + requirements.txt | 2 new files | Small |
| 2 | Profile manager (S3 save/load) | 1 new file | Small |
| 3 | JavaScript constants (form + CAPTCHA detection) | 1 new file | Medium |
| 4 | CAPTCHA solver (CapSolver API) | 1 new file | Small |
| 5 | browser_session.py (main entrypoint) | 1 new file (largest) | Large |
| 6 | DB migration (cloud browser columns) | 1 new SQL file | Small |

**Dependencies:** Tasks 1-4 are independent. Task 5 imports from Tasks 2-4 (profile_manager, js_constants, captcha_solver). Task 6 is independent.

**Execution order:** Tasks 1-4 and 6 can run in parallel. Task 5 after 2-4.

**After this plan:**
- Build + push Docker image: `docker build -t naukribaba-browser browser/ && docker tag naukribaba-browser:latest <account>.dkr.ecr.eu-west-1.amazonaws.com/naukribaba-browser:latest && docker push ...`
- Plan 3 implements the WebSocket Lambda handlers (real routing, DynamoDB CRUD) and the backend endpoints (eligibility, preview, submit)
- Plan 4 builds the frontend (BrowserStream component, AnswerPanel, Apply tab)
