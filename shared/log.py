"""Lightweight structured-logging helper for pipeline Lambdas (Phase B.6).

Why not use the `structlog` package? Two reasons:
1. Adding the dep would bloat the shared-deps layer for every Lambda.
2. CloudWatch already parses JSON log lines automatically, which is the
   only output format we care about — no need for renderers, processors,
   or contextvars binding.

This module is ~50 lines that gives us:
- `get_log(name)` — returns a logger that emits valid JSON lines.
- `log_event(logger, event, **fields)` — searchable event names + structured
  fields, mirrors structlog's `logger.info(event="x", k=v)` ergonomics.
- AWS Lambda request_id is auto-attached when available.

Usage:
    from shared.log import get_log, log_event
    log = get_log(__name__)

    def handler(event, context):
        log_event(log, "tailor.start", job_hash=event["job_hash"])
        ...
        log_event(log, "tailor.complete",
                  job_hash=event["job_hash"], duration_ms=elapsed)

CloudWatch Logs Insights then queries like:
    fields @timestamp, event, job_hash, duration_ms
    | filter event like /^tailor\\./
    | stats avg(duration_ms) by event
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


class _JsonLineFormatter(logging.Formatter):
    """One JSON object per log line. Keeps `level`, `event`, and any extra
    fields that were attached via the `extra=` kwarg."""

    _STD_FIELDS = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._STD_FIELDS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_log(name: str) -> logging.Logger:
    """Return a logger whose default handler emits JSON lines.

    Level is read from LOG_LEVEL env var (default INFO). Subsequent calls
    with the same name return the same logger without re-attaching handlers.
    """
    log = logging.getLogger(name)
    if getattr(log, "_naukribaba_configured", False):
        return log

    log.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    # Lambda's runtime adds its own handler that prepends a timestamp; we
    # remove existing handlers so we emit clean JSON only.
    for handler in list(log.handlers):
        log.removeHandler(handler)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_JsonLineFormatter())
    log.addHandler(h)
    log.propagate = False
    log._naukribaba_configured = True  # type: ignore[attr-defined]
    return log


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured log line with a searchable `event` name + fields.

    Example:
        log_event(log, "compile.failed", job_hash="abc", error_type="timeout")

    The `event` field is the primary axis for CloudWatch Logs Insights
    queries. Use namespaced names like `tailor.start`, `compile.failed`
    so a regex filter (`event like /^compile\\./`) matches a whole subsystem.
    """
    logger.log(level, event, extra={"event": event, **fields})
