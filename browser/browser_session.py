"""Cloud browser session — Fargate entrypoint for Chrome-based job application automation.

Launches headless Chrome via Playwright, connects to WebSocket API Gateway,
and runs 3 concurrent loops:
  1. screenshot_loop — streams JPEG frames to frontend via Management API
  2. command_loop — processes user actions (click, type, fill, submit)
  3. idle_monitor — enforces idle and session timeouts

Env vars: SESSION_ID, USER_ID, JOB_ID, APPLY_URL, PLATFORM, WEBSOCKET_URL,
          WS_TOKEN, S3_BUCKET, CAPSOLVER_API_KEY, AWS_REGION, WEBSOCKET_API_ID,
          SESSIONS_TABLE
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
    """Stream JPEG screenshots to frontend via API Gateway Management API.

    `apigw.post_to_connection` is sync boto3, so it's run via `asyncio.to_thread`
    to keep the event loop responsive at 5fps under network jitter.
    """
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
                await asyncio.to_thread(
                    apigw.post_to_connection,
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
    # Pass auth + session metadata via headers, NOT query params, so the token
    # never lands in API Gateway access logs / Fargate stdout / proxy logs.
    # The WsConnect Lambda (Plan 3) reads these from event["headers"].
    ws_full_url = f"{WS_URL}?session={SESSION_ID}&role=browser"
    ws_headers = {"Authorization": f"Bearer {WS_TOKEN}"}

    try:
        async with websockets.connect(ws_full_url, additional_headers=ws_headers) as ws:
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

            # return_exceptions=True prevents one loop crash from canceling the
            # other two, but it also means gather() never raises — the outer
            # try/except would never fire. Explicitly inspect each result so
            # crashes are logged instead of silently lost.
            loop_names = ("screenshot_loop", "command_loop", "idle_monitor")
            results = await asyncio.gather(
                _screenshot_loop(page, stop_event, frontend_conn_id),
                _command_loop(page, ws, stop_event, last_activity),
                _idle_monitor(stop_event, last_activity, session_start),
                return_exceptions=True,
            )
            for name, result in zip(loop_names, results):
                if isinstance(result, BaseException):
                    logger.error("Loop %s crashed: %r", name, result)

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
