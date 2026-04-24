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
