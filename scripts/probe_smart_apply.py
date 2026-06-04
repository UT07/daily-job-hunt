"""Smart Apply WebSocket chain probe.

Exercises every layer of the cloud-browser session WS path against PROD without
involving the React app:

    Layer #4  FE WS handshake (role=frontend, subprotocol auth)
    Layer #5  ws_connect writes ws_connection_frontend to DDB
    Layer #6  Bot WS handshake (role=browser, subprotocol auth)
    Layer #7  ws_connect writes ws_connection_browser to DDB
    Layer #8  Bot can post_to_connection (FE conn id) -- delivers a binary frame
    Layer #9  FE receives that frame intact

The probe creates a synthetic DDB session row, mints two ws_tokens
(role=frontend + role=browser) directly, connects both, and pumps a single test
frame from the bot to the FE. Each layer is reported green/red in the final
summary. Cleans up the DDB row regardless of outcome.

Run:
    SUPABASE_JWT_SECRET=$(aws lambda get-function-configuration \
        --function-name job-hunt-api-JobHuntApi-3FQ5DZK21ajk \
        --region eu-west-1 --query 'Environment.Variables.SUPABASE_JWT_SECRET' \
        --output text) \\
    python scripts/probe_smart_apply.py

Exit codes:
    0   All probed layers green
    1   At least one layer red (details in stdout)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone

# Make `shared` importable so we reuse the EXACT ws_auth helper that prod uses.
# This guarantees the probe and prod sign tokens identically.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3
import websockets  # type: ignore[import-not-found]
from shared.ws_auth import issue_ws_token

# Prod config — pinned here, NOT read from env, so the probe always hits prod.
WS_API_ID = "s7qfhkj7ja"
AWS_REGION = "eu-west-1"
WS_STAGE = "prod"
SESSIONS_TABLE = "naukribaba-browser-sessions"
WS_URL = f"wss://{WS_API_ID}.execute-api.{AWS_REGION}.amazonaws.com/{WS_STAGE}"
MGMT_URL = f"https://{WS_API_ID}.execute-api.{AWS_REGION}.amazonaws.com/{WS_STAGE}"

# Fake user_id — must be a valid UUID since DDB row schema requires it. We use
# a probe-specific UUID so it's easy to spot in logs and never collides with
# real users.
PROBE_USER_ID = "00000000-0000-0000-0000-000000000001"


# ─── ANSI helpers ─────────────────────────────────────────────────────────────
GREEN, RED, YELLOW, RESET = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[0m"


def ok(layer: str, msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} Layer {layer}: {msg}")


def fail(layer: str, msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} Layer {layer}: {msg}")


def info(msg: str) -> None:
    print(f"  {YELLOW}[INFO]{RESET} {msg}")


# ─── DDB helpers ──────────────────────────────────────────────────────────────
def ddb_table():
    return boto3.resource("dynamodb", region_name=AWS_REGION).Table(SESSIONS_TABLE)


def create_probe_session(session_id: str) -> None:
    """Insert a minimal DDB session row that satisfies ws_connect's checks."""
    now = int(time.time())
    ddb_table().put_item(
        Item={
            "session_id": session_id,
            "user_id": PROBE_USER_ID,
            "current_job_id": "probe-job",
            "platform": "probe",
            "fargate_task_arn": "probe-no-task",
            "status": "starting",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_activity_at": now,
            "ttl": now + 600,  # 10 min — probe completes in <30s
        }
    )


def delete_probe_session(session_id: str) -> None:
    with suppress(Exception):
        ddb_table().delete_item(Key={"session_id": session_id})


def read_session(session_id: str) -> dict:
    return ddb_table().get_item(Key={"session_id": session_id}).get("Item", {}) or {}


# ─── Per-layer probes ─────────────────────────────────────────────────────────
async def connect_with_subprotocol(token: str, session_id: str, role: str):
    """Connect to the WS using the EXACT same subprotocol pattern as the
    React FE (useBrowserSession.js:47). Returns the open ws or raises."""
    url = f"{WS_URL}?session={session_id}&role={role}"
    subprotocol = f"naukribaba-auth.{token}"
    # websockets lib accepts subprotocols via `subprotocols` kwarg.
    return await websockets.connect(url, subprotocols=[subprotocol])


async def probe_handshake(role: str, session_id: str, results: dict) -> object | None:
    """Probes layer #4 (FE) or #6 (browser) — handshake completes."""
    token = issue_ws_token(user_id=PROBE_USER_ID, session_id=session_id, role=role)
    label = f"#{4 if role == 'frontend' else 6}"
    try:
        ws = await asyncio.wait_for(
            connect_with_subprotocol(token, session_id, role), timeout=10
        )
        ok(label, f"WS handshake completed for role={role}")
        results[f"handshake_{role}"] = True
        return ws
    except websockets.InvalidStatus as e:  # noqa: F841
        fail(label, f"WS handshake rejected for role={role}: status={e.response.status_code}")
    except websockets.InvalidHandshake as e:
        fail(label, f"WS handshake INVALID for role={role} (likely subprotocol mismatch): {e}")
    except asyncio.TimeoutError:
        fail(label, f"WS handshake TIMEOUT for role={role} (10s)")
    except Exception as e:
        fail(label, f"WS handshake UNEXPECTED error for role={role}: {type(e).__name__}: {e}")
    results[f"handshake_{role}"] = False
    return None


def probe_ddb_connection_set(role: str, session_id: str, results: dict, wait_seconds: float = 5.0) -> str | None:
    """Probes layer #5/#7 — ws_connect wrote ws_connection_{role} to DDB."""
    label = f"#{5 if role == 'frontend' else 7}"
    field = f"ws_connection_{role}"
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        item = read_session(session_id)
        if field in item and item[field]:
            ok(label, f"DDB.{field}={item[field][:16]}... (registered)")
            results[f"ddb_{role}"] = True
            return item[field]
        time.sleep(0.5)
    fail(label, f"DDB.{field} STILL ABSENT after {wait_seconds}s — ws_connect did not register")
    results[f"ddb_{role}"] = False
    return None


def probe_bot_to_frontend_delivery(fe_conn_id: str, results: dict) -> None:
    """Probes layer #8 — apigw.post_to_connection delivers the JSON envelope.

    The bot now wraps JPEG bytes in {action:'frame', jpeg:'<base64>'} (see
    browser/browser_session.py::_encode_frame_envelope). We simulate that
    same wire payload here using a synthetic JPEG-magic byte sequence so the
    round-trip test exercises the exact production code path.
    """
    import base64 as _b64

    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=MGMT_URL, region_name=AWS_REGION)
    # Synthetic JPEG-magic header so we can validate intact byte recovery
    # after the FE's base64 decode would run. (The probe doesn't decode; it
    # checks the envelope shape — the FE's behavior is exercised by
    # web/src/hooks/__tests__/useBrowserSession.test.jsx.)
    jpeg_magic = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
    synthetic_jpeg = jpeg_magic + b"PROBE_FRAME_" + uuid.uuid4().bytes  # 41 bytes
    envelope = json.dumps({
        "action": "frame",
        "jpeg": _b64.b64encode(synthetic_jpeg).decode("ascii"),
    })

    try:
        apigw.post_to_connection(ConnectionId=fe_conn_id, Data=envelope)
        ok("#8", f"post_to_connection delivered {len(envelope)}-byte JSON envelope (synthetic JPEG inside)")
        results["delivery"] = True
        results["expected_envelope"] = envelope
        results["expected_jpeg_bytes"] = synthetic_jpeg
    except apigw.exceptions.GoneException:
        fail("#8", f"GoneException — FE conn_id {fe_conn_id[:16]}... was unregistered before delivery")
        results["delivery"] = False
    except Exception as e:
        fail("#8", f"post_to_connection failed: {type(e).__name__}: {e}")
        results["delivery"] = False


async def probe_frontend_receive(
    ws_fe, expected_envelope: str, expected_jpeg: bytes, results: dict, timeout_s: float = 5.0
) -> None:
    """Probes layer #9 — FE receives the envelope, parses JSON, recovers JPEG.

    The contract is:
      1. Wire arrives as Text frame (str in Python)
      2. Parses as JSON with action='frame'
      3. base64-decodes the `jpeg` field exactly matching the input bytes
    """
    import base64 as _b64

    try:
        msg = await asyncio.wait_for(ws_fe.recv(), timeout=timeout_s)
    except asyncio.TimeoutError:
        fail("#9", f"FE did NOT receive any frame within {timeout_s}s")
        results["receive"] = False
        return
    except Exception as e:
        fail("#9", f"FE receive raised: {type(e).__name__}: {e}")
        results["receive"] = False
        return

    info(f"diagnostic: type(msg)={type(msg).__name__} len={len(msg)}")

    if not isinstance(msg, str):
        # If we ever DO get bytes here, that means API Gateway transport changed
        # to support binary opcodes — would be good news but breaks our envelope
        # assumption. Surface it loudly.
        fail("#9", f"UNEXPECTED: got bytes not str. APIGW transport may have changed; reconsider envelope.")
        results["receive"] = False
        return

    if msg != expected_envelope:
        fail("#9", f"FE got TEXT frame but content mismatch. Got: {msg[:80]!r}")
        results["receive"] = False
        return

    # Round-trip through the FE's actual decode path
    try:
        parsed = json.loads(msg)
    except json.JSONDecodeError as e:
        fail("#9", f"FE got TEXT frame but it's not valid JSON: {e}")
        results["receive"] = False
        return

    if parsed.get("action") != "frame":
        fail("#9", f"FE got JSON but action!={parsed.get('action')!r} (expected 'frame')")
        results["receive"] = False
        return

    try:
        decoded = _b64.b64decode(parsed["jpeg"])
    except Exception as e:
        fail("#9", f"FE got envelope but base64 decode failed: {e}")
        results["receive"] = False
        return

    if decoded != expected_jpeg:
        fail("#9", f"FE got envelope but JPEG bytes mismatch (len: got={len(decoded)} expected={len(expected_jpeg)})")
        results["receive"] = False
        return

    ok("#9", f"FE round-tripped JSON envelope and recovered {len(decoded)} JPEG bytes intact")
    results["receive"] = True


# ─── Orchestration ────────────────────────────────────────────────────────────
async def main() -> int:
    if not os.environ.get("SUPABASE_JWT_SECRET"):
        print(f"{RED}ERROR{RESET}: SUPABASE_JWT_SECRET env var not set. See module docstring.")
        return 2

    session_id = f"probe-{uuid.uuid4()}"
    print(f"\n{'─' * 70}")
    print(f"Smart Apply WS Chain Probe — session={session_id}")
    print(f"{'─' * 70}\n")

    results: dict = {}
    ws_fe = None
    ws_bot = None
    try:
        # ─── Setup ─────────────────────────────────────────────────────────────
        create_probe_session(session_id)
        info(f"Created synthetic DDB session row (user={PROBE_USER_ID})")

        # ─── FE side: layers #4, #5 ────────────────────────────────────────────
        ws_fe = await probe_handshake("frontend", session_id, results)
        if not ws_fe:
            return 1

        # tiny grace period to let ws_connect's DDB write commit
        await asyncio.sleep(1.0)
        fe_conn_id = probe_ddb_connection_set("frontend", session_id, results)
        if not fe_conn_id:
            return 1

        # ─── Bot side: layers #6, #7 ───────────────────────────────────────────
        ws_bot = await probe_handshake("browser", session_id, results)
        if not ws_bot:
            return 1

        await asyncio.sleep(1.0)
        probe_ddb_connection_set("browser", session_id, results)

        # ─── Delivery path: layers #8, #9 ──────────────────────────────────────
        probe_bot_to_frontend_delivery(fe_conn_id, results)
        if results.get("delivery"):
            await probe_frontend_receive(
                ws_fe,
                results["expected_envelope"],
                results["expected_jpeg_bytes"],
                results,
            )

        # ─── Final summary ─────────────────────────────────────────────────────
        print(f"\n{'─' * 70}")
        all_green = all(
            results.get(k, False) for k in
            ("handshake_frontend", "ddb_frontend", "handshake_browser",
             "ddb_browser", "delivery", "receive")
        )
        if all_green:
            print(f"{GREEN}ALL PROBED LAYERS GREEN.{RESET} Smart Apply WS chain is intact end-to-end.")
            print("If users still see 'Connecting...', bug is in layers #1-3 (start-session) or #10 (FE render).")
        else:
            print(f"{RED}AT LEAST ONE LAYER RED.{RESET} See [FAIL] lines above.")
        print(f"{'─' * 70}\n")
        return 0 if all_green else 1

    finally:
        if ws_fe:
            await ws_fe.close()
        if ws_bot:
            await ws_bot.close()
        delete_probe_session(session_id)
        info(f"Cleanup: deleted DDB row for {session_id}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
