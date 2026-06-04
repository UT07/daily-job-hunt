"""Unit tests for the Fargate browser_session.py frame envelope.

Specifically guards the wire-format contract between the Fargate cloud-browser
and the React FE for screenshot delivery, after the 2026-05-11 regression:

  PROBE Layer #9 (scripts/probe_smart_apply.py) revealed API Gateway WebSocket
  silently coerces every `post_to_connection` payload to a Text opcode, even
  when `Data=bytes`. The FE was checking `ev.data instanceof ArrayBuffer` and
  silently dropping every screenshot.

  Fix: bot wraps JPEG bytes in a JSON envelope {action:'frame', jpeg:'<base64>'}.
  These tests pin that envelope so regressions blow up before deploy.

The full `_screenshot_loop` async function isn't easily unit-testable without
Playwright + a real apigw stub, so we test the pure helper `_encode_frame_envelope`
that produces the on-wire payload. The probe (scripts/probe_smart_apply.py) is
the integration-level coverage.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# `browser/browser_session.py` reads required env vars at import time (SESSION_ID,
# USER_ID, JOB_ID, APPLY_URL, WEBSOCKET_URL, WS_TOKEN). Set sentinels BEFORE
# import so test collection doesn't blow up locally / in CI.
import os
os.environ.setdefault("SESSION_ID", "test-session")
os.environ.setdefault("USER_ID", "test-user")
os.environ.setdefault("JOB_ID", "test-job")
os.environ.setdefault("APPLY_URL", "https://example.com/apply")
os.environ.setdefault("WEBSOCKET_URL", "wss://test.example.com/prod")
os.environ.setdefault("WS_TOKEN", "test-token")

# `browser/` isn't a package — add it to sys.path so `import browser_session` works.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "browser"))

import browser_session  # noqa: E402  — sys.path mutation must precede


def test_encode_frame_envelope_is_valid_json_with_action_frame():
    """The envelope MUST parse as JSON with action='frame' — the FE's
    parseTextFrame() drops anything else, so an action-less or non-JSON
    payload would be silently dropped (same failure mode we just fixed).
    """
    envelope = browser_session._encode_frame_envelope(b"\xff\xd8\xff\xe0")
    parsed = json.loads(envelope)  # raises if not valid JSON

    assert parsed["action"] == "frame", "FE switches on `action` — must be 'frame'"


def test_encode_frame_envelope_jpeg_field_is_base64_of_input_bytes():
    """The `jpeg` field must be exactly base64(input). The FE concatenates
    it directly into `data:image/jpeg;base64,<jpeg>` — no decode step on the
    receiver, so any transformation (padding stripping, urlsafe variant,
    etc.) would render as a broken image.
    """
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01"
    envelope = browser_session._encode_frame_envelope(jpeg_bytes)
    parsed = json.loads(envelope)

    decoded = base64.b64decode(parsed["jpeg"])
    assert decoded == jpeg_bytes, "Round-trip must be lossless"


def test_encode_frame_envelope_jpeg_field_uses_ascii_safe_charset():
    """The envelope is serialized as JSON and shipped through API Gateway as a
    UTF-8 text frame. Any non-ASCII byte in the `jpeg` field would either
    fail UTF-8 encoding upstream or arrive corrupted. Standard base64
    (the `+/=` alphabet) is fully ASCII-safe.
    """
    jpeg_bytes = bytes(range(256))  # every possible byte value
    envelope = browser_session._encode_frame_envelope(jpeg_bytes)
    parsed = json.loads(envelope)

    # base64 alphabet: A-Z a-z 0-9 + / = — all ASCII
    assert all(ord(c) < 128 for c in parsed["jpeg"])


def test_encode_frame_envelope_handles_empty_payload():
    """Edge case: a zero-byte screenshot (would never happen in prod) must
    still produce valid JSON, not throw. Defensive guard against future
    bugs that might pass empty bytes accidentally."""
    envelope = browser_session._encode_frame_envelope(b"")
    parsed = json.loads(envelope)

    assert parsed == {"action": "frame", "jpeg": ""}


def test_encode_frame_envelope_size_is_4_over_3_input():
    """base64 encodes 3 input bytes → 4 output chars. For a 96 KB JPEG (the
    cap implied by SCREENSHOT_MAX_BYTES post-encode), the envelope is
    ~128 KB — exactly the API Gateway per-message limit. This test pins
    the size relationship so a future change to base64 variant
    (e.g., urlsafe with different padding) doesn't silently push us over.
    """
    payload = b"x" * 1500  # 1.5 KB raw → 2 KB base64
    envelope = browser_session._encode_frame_envelope(payload)
    parsed = json.loads(envelope)

    # base64 of 1500 bytes = 2000 chars (1500 / 3 * 4)
    assert len(parsed["jpeg"]) == 2000
