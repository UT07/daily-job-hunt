"""Unit tests for shared/log.py — Phase B.6 structured logging helper.

These tests pin the JSON-line shape that CloudWatch Logs Insights queries
will rely on. If we change the field names (event, ts, level, logger),
update the queries in the runbook + dashboard widgets at the same time.
"""
from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    """Return (logger, stream) where the logger writes JSON lines to stream."""
    from shared.log import get_log

    log = get_log(name)
    # Reset its single handler to our capture stream so the assertions are
    # deterministic regardless of CloudWatch / pytest's own handlers.
    buf = io.StringIO()
    for h in list(log.handlers):
        log.removeHandler(h)
    handler = logging.StreamHandler(buf)
    from shared.log import _JsonLineFormatter
    handler.setFormatter(_JsonLineFormatter())
    log.addHandler(handler)
    log._naukribaba_configured = True  # type: ignore[attr-defined]
    return log, buf


class TestJsonLineFormat:
    def test_basic_log_emits_json_line(self):
        log, buf = _capture_logger("test.basic")
        log.info("hello")
        line = buf.getvalue().strip()
        obj = json.loads(line)
        assert obj["level"] == "INFO"
        assert obj["logger"] == "test.basic"
        assert obj["message"] == "hello"
        assert "ts" in obj

    def test_extra_fields_are_serialized(self):
        log, buf = _capture_logger("test.extra")
        log.info("event", extra={"job_hash": "abc", "duration_ms": 42})
        obj = json.loads(buf.getvalue().strip())
        assert obj["job_hash"] == "abc"
        assert obj["duration_ms"] == 42

    def test_non_serializable_falls_back_to_repr(self):
        log, buf = _capture_logger("test.nonjson")
        # set is not JSON-serializable; helper should repr() it instead of crashing
        log.info("event", extra={"weird": {1, 2, 3}})
        obj = json.loads(buf.getvalue().strip())
        assert obj["weird"].startswith("{") and obj["weird"].endswith("}")

    def test_exception_capture_under_exc_field(self):
        log, buf = _capture_logger("test.exc")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("caught")
        obj = json.loads(buf.getvalue().strip())
        assert "exc" in obj
        assert "RuntimeError: boom" in obj["exc"]


class TestLogEvent:
    def test_event_field_is_searchable(self):
        from shared.log import log_event
        log, buf = _capture_logger("test.event")
        log_event(log, "compile.start", job_hash="abc")
        obj = json.loads(buf.getvalue().strip())
        assert obj["event"] == "compile.start"
        assert obj["job_hash"] == "abc"
        # The message field also gets the event name so logs without
        # JSON parsing still show the searchable axis
        assert obj["message"] == "compile.start"

    def test_default_level_is_info(self):
        from shared.log import log_event
        log, buf = _capture_logger("test.level.default")
        log_event(log, "x")
        obj = json.loads(buf.getvalue().strip())
        assert obj["level"] == "INFO"

    def test_custom_level(self):
        from shared.log import log_event
        log, buf = _capture_logger("test.level.warn")
        log_event(log, "x", level=logging.WARNING)
        obj = json.loads(buf.getvalue().strip())
        assert obj["level"] == "WARNING"


class TestIdempotentConfiguration:
    def test_get_log_returns_same_logger(self):
        from shared.log import get_log
        a = get_log("test.idem")
        b = get_log("test.idem")
        assert a is b

    def test_repeated_get_log_does_not_duplicate_handlers(self):
        from shared.log import get_log
        log = get_log("test.handlers")
        first_count = len(log.handlers)
        get_log("test.handlers")
        get_log("test.handlers")
        assert len(log.handlers) == first_count
