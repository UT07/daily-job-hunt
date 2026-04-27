# Phase 4 — Observability (structlog + X-Ray + EMF + Dashboards)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire structured JSON logging (structlog), distributed tracing (AWS X-Ray), custom business metrics (Embedded Metric Format via AWS Lambda Powertools), and a CloudWatch dashboard into FastAPI + 25 Lambda handlers so every request has a `request_id`, every business event becomes a queryable metric, and one URL answers "is the system healthy right now?".

**Architecture:** All Python emits JSON to stdout; CloudWatch Logs ingests as-is. structlog drives FastAPI; AWS Lambda Powertools `Logger` drives Lambda handlers (it ships with EMF + X-Ray ergonomics). One shared module per surface — `config/observability.py` for FastAPI, `lambdas/pipeline/utils/logging.py` for Lambdas — defines the standard fields (`request_id`, `user_id`, `function_name`, `cold_start`, `duration_ms`, `level`, `event`) and exports a singleton `logger`, `tracer`, and `metrics`. `template.yaml` flips `Tracing: Active` globally and adds an `AWS::CloudWatch::Dashboard` resource. Alarms (created in Phase 2) are upgraded from generic Lambda `Errors > 0` to composite EMF-backed alarms (e.g. `apply_failed_rate > 20%`). A queries cookbook in `monitoring/queries.md` lists the Logs Insights / X-Ray queries the operator will use day-to-day.

**Tech Stack:** structlog 24+, aws-lambda-powertools 2.40+ (Tracer + Metrics + Logger), aws-xray-sdk 2.14+, CloudWatch Logs Insights, CloudWatch Dashboards, Embedded Metric Format (EMF), pytest, AWS SAM/CloudFormation.

**Spec:** [Deployment Safety + Observability Roadmap](./2026-04-27-deployment-safety-roadmap.md) (Phase 4 section, lines 257–296).

**Out of scope (deferred to other phases):**
- Sentry breadcrumb enrichment from structlog binding context — Phase 5 (note hooks here, do not pre-build).
- Smoke-test driven assertions on EMF metrics — Phase 6.
- Alarm-trip → SNS email — that wire is owned by Phase 2; we replace the alarm body, not the SNS plumbing.

**Cross-phase coordination:**
- Phase 4 is parallel-safe with Phases 1, 2, 3. If Phase 3 (staging) has merged, the `Stage` parameter (`staging` | `prod`) MUST be honored in metric namespace (`Naukribaba/${Stage}`) and dashboard name (`naukribaba-${Stage}`). Tasks 8 and 10 below detect this via `Fn::Sub`/`!Ref Stage` regardless of Phase 3 state — if `Stage` parameter doesn't yet exist, the dashboard still works with a literal "prod".
- Phase 2 alarms (`monitoring/alarms.yaml`) are upgraded in Task 11. Coordinate carefully: never delete a stock alarm without the EMF-backed replacement landing in the same commit. Task 11's commit is structured to leave a working alarm at every checkpoint.
- Phase 5 (Sentry) will subscribe to structlog's binding context for breadcrumbs; structure of `_BASE_PROCESSORS` in Task 2 leaves an explicit hook comment for that future work.
- Phase 6 (smoke tests) will assert non-zero `apply_attempted` / `pipeline_run_completed` counts in EMF after each deploy. Task 6's metric names are the contract.

---

## File Structure

```
config/                                            (CREATE this directory)
  __init__.py                                      (CREATE) empty package marker
  observability.py                                 (CREATE) structlog processors + Powertools Tracer/Metrics for FastAPI
requirements.txt                                   (MODIFY) add structlog, aws-xray-sdk, aws-lambda-powertools[tracer,metrics]
app.py                                             (MODIFY @ lines 41/85 + bulk ~30 logger.* call sites) structlog + middleware
lambdas/pipeline/utils/
  logging.py                                       (CREATE) Powertools Logger/Tracer/Metrics + EMF helpers shared across all pipeline + browser lambdas
lambdas/pipeline/*.py                              (MODIFY ~22 files) swap `import logging` → `from utils.logging import logger, tracer, metrics`; emit metrics at decision points
lambdas/browser/*.py                               (MODIFY 3 files) same migration
lambdas/scrapers/*.py                              (MODIFY ~11 files, light pass) same migration; metrics emitted only at top-of-handler success/failure
template.yaml                                      (MODIFY) add `Tracing: Active` to Globals.Function; add CloudWatchDashboard resource
monitoring/                                        (CREATE this directory if Phase 2 hasn't already)
  dashboard.json                                   (CREATE) 10-widget CloudWatch dashboard JSON, parameterized via SAM Fn::Sub
  alarms.yaml                                      (MODIFY — Phase 2 created it) upgrade to composite EMF-backed alarms for high-traffic Lambdas
  queries.md                                       (CREATE) Logs Insights + X-Ray query cookbook
docs/superpowers/specs/
  2026-04-27-observability-decision.md             (CREATE) ADR: structlog vs loguru, EMF vs PutMetricData, X-Ray vs OTel
tests/unit/
  test_observability.py                            (CREATE) 5 cases — JSON shape, request_id binding, metrics EMF shape, cold_start propagation, scrubber for None
```

**Note on `lambdas/scrapers/*.py`:** the roadmap calls out "all `lambdas/browser/*.py` and `lambdas/pipeline/*.py`". Pre-flight `ls` shows 11 scraper files in `lambdas/scrapers/` that also do `import logging; logger = logging.getLogger()`. They are bulk-edited in Task 9 with the same pattern but only emit one metric per handler invocation (`scraper_jobs_returned{source}`) — no decision-point metrics needed because their bodies are linear scraping flows.

---

## Estimated Time

| Task | Minutes | Notes |
|---|---|---|
| 0. Pre-flight verify (Phase 2 alarms file exists?) | 5 | Read-only |
| 1. Add deps to requirements.txt + layer rebuild check | 15 | |
| 2. `config/observability.py` (TDD) | 60 | 3 test cases drive the design |
| 3. `lambdas/pipeline/utils/logging.py` (TDD) | 45 | 2 test cases |
| 4. `app.py` — structlog + request_id middleware (TDD) | 90 | 1 test, then bulk-edit ~30 logger.* calls |
| 5. Pilot Lambda migration: `lambdas/pipeline/score_batch.py` | 30 | Reference implementation for Task 6 |
| 6. Bulk migrate remaining 21 pipeline Lambdas | 22 × 5 min = 110 | Mechanical |
| 7. Bulk migrate 3 browser Lambdas | 3 × 5 min = 15 | |
| 8. Light pass on 11 scraper Lambdas | 11 × 4 min = 45 | One metric each |
| 9. `template.yaml` — Tracing: Active + dashboard resource | 30 | |
| 10. `monitoring/dashboard.json` — 10 widgets | 75 | |
| 11. `monitoring/alarms.yaml` — upgrade to EMF-backed | 45 | |
| 12. `monitoring/queries.md` — query cookbook | 25 | |
| 13. ADR doc | 20 | |
| 14. Deploy + validation (Logs Insights + X-Ray + dashboard) | 45 | |
| **Total** | | **~10 hours of focused work, fits in 1.5 days** |

---

## Task 0: Pre-flight — confirm Phase 2 boundaries

**Why:** Tasks 11 (alarms upgrade) and 9 (template.yaml additions) need to know whether Phase 2 has already landed `monitoring/alarms.yaml`. If yes, we modify it in place. If no, we still need to coordinate so Phase 2's stock alarms aren't accidentally clobbered by a later parallel branch.

**Files (read-only):**
- Read: `monitoring/alarms.yaml` (may not exist)
- Read: `template.yaml` (check for existing `DeploymentPreference` or `Stage` parameter)

- [ ] **Step 1: Check Phase 2 status**

```bash
ls -la /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/monitoring/ 2>&1 || echo "monitoring/ does not exist yet"
grep -E "DeploymentPreference|Tracing|AutoPublishAlias|Parameters:.*Stage" /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml | head -20
```

Three possible outcomes:

| Output | Phase 2 / Phase 3 state | Action for this plan |
|---|---|---|
| `monitoring/` missing, no `DeploymentPreference` | Phases 2 & 3 not yet merged | Tasks 9, 11 CREATE `monitoring/alarms.yaml` from scratch; metric namespace literal `Naukribaba/prod`; dashboard name literal `naukribaba-prod` |
| `monitoring/alarms.yaml` exists, `DeploymentPreference` lines visible | Phase 2 merged | Task 11 MODIFIES the existing file; reuse Phase 2's alarm-template structure |
| `Parameters: Stage` line visible in template.yaml | Phase 3 merged | Tasks 9, 10 use `!Sub "Naukribaba/${Stage}"` and `!Sub "naukribaba-${Stage}"` |

- [ ] **Step 2: Record the outcome in your local notes**

No commit. Just write down which of the three outcomes you have so Tasks 9, 10, 11 reach for the right snippet.

---

## Task 1: Add observability deps + verify layer build picks them up

**Files:**
- Modify: `requirements.txt`
- Modify (sanity check only): `layer/requirements.txt` (likely the same file referenced from layer build)

- [ ] **Step 1: Read current `requirements.txt`**

The current file ends at line 44 with the commented-out anthropic line. Find the `# Optional: Anthropic SDK` block.

- [ ] **Step 2: Append observability block to `requirements.txt`**

Add the following block right before the `# Optional: Anthropic SDK` comment (so observability deps are mandatory, not optional):

```
# Observability (Phase 4 — 2026-04-27)
# - structlog: structured JSON logging for FastAPI process
# - aws-lambda-powertools: canonical AWS-supported wrapper for Lambda Logger,
#   Tracer (X-Ray), and Metrics (EMF). Cheaper than hand-rolling EMF + xray-sdk
#   bindings; chosen over loguru because Powertools is already AWS-best-practice
#   for Lambda + JSON. ADR: docs/superpowers/specs/2026-04-27-observability-decision.md
# - aws-xray-sdk: needed by Powertools Tracer for boto3/requests instrumentation
structlog>=24.0.0
aws-xray-sdk>=2.14.0
aws-lambda-powertools[tracer,metrics]>=2.40.0
```

- [ ] **Step 3: Confirm the Lambda layer picks them up**

The Lambda layer is built from `layer/requirements.txt`. Check whether that file exists and whether it duplicates the root `requirements.txt`:

```bash
ls /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/layer/ 2>&1
cat /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/layer/requirements.txt 2>&1 || echo "no separate layer/requirements.txt"
```

If `layer/requirements.txt` exists separately, append the same three lines (`structlog>=24.0.0`, `aws-xray-sdk>=2.14.0`, `aws-lambda-powertools[tracer,metrics]>=2.40.0`) to it as well — Lambdas import from `/opt/python/`, which is built from this file.

- [ ] **Step 4: Verify pip can resolve the new deps locally**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
source .venv/bin/activate
pip install structlog>=24.0.0 aws-xray-sdk>=2.14.0 'aws-lambda-powertools[tracer,metrics]>=2.40.0'
python -c "import structlog, aws_xray_sdk, aws_lambda_powertools; print('ok', structlog.__version__, aws_lambda_powertools.__version__)"
```

Expected: `ok 24.x.x 2.x.x`. No errors.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt layer/requirements.txt
git commit -m "chore(deps): add structlog + aws-lambda-powertools + aws-xray-sdk

Phase 4 (observability) requires:
- structlog: JSON logging for FastAPI process
- aws-lambda-powertools[tracer,metrics]: AWS-canonical Lambda Logger + EMF + X-Ray
- aws-xray-sdk: required by Powertools Tracer for boto3/requests auto-instrumentation

Both root requirements.txt and layer/requirements.txt updated so FastAPI
container and Lambda layer pick up the deps. Powertools chosen over hand-rolled
EMF per ADR docs/superpowers/specs/2026-04-27-observability-decision.md."
```

---

## Task 2: `config/observability.py` — structlog setup for FastAPI (TDD)

**Why:** FastAPI runs in a Lambda container today (Mangum) but the structlog setup is generic — would also work in any uvicorn process. Powertools Logger could also be used here, but structlog gives us first-class context-var binding (request_id) which is what FastAPI middleware needs. Keep this module ~80 LOC.

**Files:**
- Create: `config/__init__.py` (empty)
- Create: `config/observability.py`
- Create: `tests/unit/test_observability.py` (3 cases for FastAPI half; Lambda half added in Task 3)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_observability.py`:

```python
"""Tests for the observability layer (structlog + Powertools).

These tests assert the SHAPE of emitted log lines, not the side effects of
emitting (i.e. they capture stdout, parse JSON, and check expected keys).
That way the same suite can run in CI without a real CloudWatch/X-Ray.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import uuid

import pytest


# --- Phase 4 / Task 2: FastAPI structlog ---


def _capture_log_line(emit_fn) -> dict:
    """Run `emit_fn()` while redirecting stdout to a buffer; return parsed JSON dict."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        emit_fn()
    finally:
        sys.stdout = saved
    raw = buf.getvalue().strip()
    assert raw, "no log line emitted"
    # structlog can emit one or more lines — use the last
    return json.loads(raw.splitlines()[-1])


def test_fastapi_logger_emits_iso_timestamp_and_event_keys():
    """Baseline: every structlog call yields a JSON dict with timestamp, level, event."""
    from config.observability import get_fastapi_logger

    logger = get_fastapi_logger()
    parsed = _capture_log_line(lambda: logger.info("request_started", path="/healthz"))

    assert "timestamp" in parsed and parsed["timestamp"].endswith("Z")
    assert parsed["level"] == "info"
    assert parsed["event"] == "request_started"
    assert parsed["path"] == "/healthz"


def test_fastapi_request_id_binding():
    """When middleware binds request_id, every subsequent log line on the same
    context-var carries it without explicit kwargs."""
    from config.observability import bind_request_context, clear_request_context, get_fastapi_logger

    logger = get_fastapi_logger()
    rid = "req-" + uuid.uuid4().hex[:8]
    bind_request_context(request_id=rid, user_id="u-42")
    try:
        parsed = _capture_log_line(lambda: logger.info("apply_attempted"))
        assert parsed["request_id"] == rid
        assert parsed["user_id"] == "u-42"
        assert parsed["event"] == "apply_attempted"
    finally:
        clear_request_context()


def test_fastapi_logger_does_not_leak_context_across_clear():
    """After clear_request_context(), request_id must NOT appear in subsequent lines."""
    from config.observability import bind_request_context, clear_request_context, get_fastapi_logger

    logger = get_fastapi_logger()
    bind_request_context(request_id="should-disappear", user_id="u-1")
    clear_request_context()

    parsed = _capture_log_line(lambda: logger.info("orphan"))
    assert "request_id" not in parsed
    assert "user_id" not in parsed
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
source .venv/bin/activate
pytest tests/unit/test_observability.py -v
```

Expected: 3 ERRORS — `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 3: Create `config/__init__.py`**

Create empty file:

```python
"""Top-level config package — observability + (future) feature_flags + sentry_config."""
```

- [ ] **Step 4: Implement `config/observability.py`**

Create `config/observability.py`:

```python
"""Observability configuration for the FastAPI process.

Owned by Phase 4 of the deployment-safety roadmap. See the roadmap and the
ADR (docs/superpowers/specs/2026-04-27-observability-decision.md) for the
"why" of every dep choice.

Public API:
- ``get_fastapi_logger()`` — singleton structlog BoundLogger.
- ``bind_request_context(**kwargs)`` — bind kwargs into the per-request context-var.
- ``clear_request_context()`` — clear all context-vars (call in middleware finally).
- ``tracer`` — Powertools Tracer instance (X-Ray bindings auto-loaded).
- ``metrics`` — Powertools Metrics instance scoped to namespace ``Naukribaba/${STAGE}``.

Standard log fields (always present when middleware has bound them):
  timestamp, level, event, request_id, user_id, path, method, status_code, duration_ms.

PHASE-5 HOOK: Sentry (Phase 5) will tap structlog's context-var via
  ``structlog.contextvars.get_contextvars()`` to enrich breadcrumbs. Do NOT
  bypass ``bind_request_context()`` — that's the contract Sentry will read.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from aws_lambda_powertools import Metrics, Tracer

# ---------------------------------------------------------------------------
# Stage / environment
# ---------------------------------------------------------------------------

# Stage is set by template.yaml (Phase 3). Default to "prod" so this module
# works the same way locally and in CI before Phase 3 lands.
STAGE = os.environ.get("STAGE", "prod")
SERVICE = "naukribaba-api"
METRICS_NAMESPACE = f"Naukribaba/{STAGE}"

# ---------------------------------------------------------------------------
# Powertools singletons (Tracer + Metrics)
# ---------------------------------------------------------------------------

# Tracer is a no-op when X-Ray daemon is not running (e.g. local dev / CI).
# In Lambda with `Tracing: Active`, X-Ray captures spans automatically.
tracer = Tracer(service=SERVICE)

# Metrics writes EMF JSON blobs to stdout, which CloudWatch parses into
# CloudWatch metrics under METRICS_NAMESPACE. Free (no PutMetricData call).
metrics = Metrics(namespace=METRICS_NAMESPACE, service=SERVICE)


# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

def _add_service_field(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject a stable ``service`` field on every line (Phase 5 / dashboards)."""
    event_dict.setdefault("service", SERVICE)
    event_dict.setdefault("stage", STAGE)
    return event_dict


_BASE_PROCESSORS = [
    structlog.contextvars.merge_contextvars,                 # request_id, user_id, …
    structlog.processors.add_log_level,                      # → key "level"
    structlog.processors.TimeStamper(fmt="iso", utc=True),   # → key "timestamp" (ISO Z)
    _add_service_field,                                      # service / stage tags
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.JSONRenderer(),                     # final: dict → JSON string
]


def _configure_once() -> None:
    """Idempotent — calling more than once is a no-op."""
    if structlog.is_configured():
        return
    structlog.configure(
        processors=_BASE_PROCESSORS,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


_configure_once()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_fastapi_logger() -> Any:
    """Return the singleton structlog BoundLogger for FastAPI handlers."""
    return structlog.get_logger()


def bind_request_context(**kwargs: Any) -> None:
    """Bind keys onto the per-request context-var.

    Called once per request from FastAPI middleware with at least
    ``request_id`` and ``user_id`` (when authenticated). Subsequent
    ``logger.info(...)`` calls in the same async task pick these up
    automatically — no need to thread them through every signature.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Wipe the per-request context-var. Always call from middleware ``finally``."""
    structlog.contextvars.clear_contextvars()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_observability.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add config/__init__.py config/observability.py tests/unit/test_observability.py
git commit -m "feat(observability): structlog + Powertools setup for FastAPI

Phase 4 / roadmap §'Observability'. config/observability.py exports:
- get_fastapi_logger() singleton with JSON renderer + ISO timestamps
- bind_request_context() / clear_request_context() context-var helpers
  (consumed by middleware in app.py — added in Task 4)
- tracer (Powertools/X-Ray) and metrics (Powertools/EMF) singletons,
  metrics namespace 'Naukribaba/\${STAGE}' so Phase 3 staging splits cleanly.

ADR docs/superpowers/specs/2026-04-27-observability-decision.md justifies
structlog over loguru and Powertools[tracer,metrics] over hand-rolled EMF.

3 unit tests pass."
```

---

## Task 3: `lambdas/pipeline/utils/logging.py` — Powertools setup for Lambdas (TDD)

**Why:** FastAPI uses structlog (rich context-vars per async request). Lambdas are simpler — one event per cold/warm invocation. Powertools `Logger` is the AWS-canonical wrapper there: it already emits structured JSON to stdout, auto-injects `cold_start`, `function_name`, `xray_trace_id`, and integrates with Tracer/Metrics. We standardize on it for *every* Lambda (pipeline, browser, scrapers) to avoid two flavors of "structured logging" in the same codebase.

**Files:**
- Create: `lambdas/pipeline/utils/logging.py`
- Modify: `tests/unit/test_observability.py` (add 2 cases)

- [ ] **Step 1: Append failing Lambda tests to `tests/unit/test_observability.py`**

Append (do not replace) to the bottom of `tests/unit/test_observability.py`:

```python
# --- Phase 4 / Task 3: Lambda Powertools wrapper ---


def test_lambda_logger_emits_required_fields():
    """The shared Lambda logger emits JSON with the standard envelope.

    Required keys (per roadmap §Phase 4 architecture): timestamp, level,
    message, service, function_name, cold_start.
    """
    from lambdas.pipeline.utils.logging import logger as lambda_logger

    parsed = _capture_log_line(lambda: lambda_logger.info("apply_attempted", extra={"user_id": "u-42", "job_id": "j-7"}))

    assert "timestamp" in parsed
    assert parsed["level"].lower() == "info"
    assert parsed["service"] == "naukribaba-pipeline"
    # message is the canonical Powertools key for the human string
    assert parsed["message"] == "apply_attempted"
    # extras get flattened into the JSON line
    assert parsed.get("user_id") == "u-42"
    assert parsed.get("job_id") == "j-7"


def test_lambda_metrics_emit_emf_shape():
    """metrics.add_metric + flush emits EMF JSON CloudWatch can parse.

    EMF shape contract:
      {
        "_aws": { "Timestamp": ..., "CloudWatchMetrics": [ {Namespace, Dimensions, Metrics:[{Name, Unit}]} ] },
        "<metric_name>": <value>,
        ...
      }
    """
    import json

    from aws_lambda_powertools.metrics import MetricUnit

    from lambdas.pipeline.utils.logging import metrics as lambda_metrics

    # Capture EMF JSON line on flush
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        lambda_metrics.add_metric(name="apply_attempted", unit=MetricUnit.Count, value=1)
        lambda_metrics.add_dimension(name="ats", value="greenhouse")
        # flush_metrics is the explicit drain (in Lambda this is auto-called by @logger.inject_lambda_context)
        lambda_metrics.flush_metrics()
    finally:
        sys.stdout = saved

    raw = buf.getvalue().strip().splitlines()
    assert raw, "metrics.flush did not write to stdout"
    parsed = json.loads(raw[-1])
    assert "_aws" in parsed
    cw = parsed["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"].startswith("Naukribaba/")
    assert any(m["Name"] == "apply_attempted" for m in cw["Metrics"])
    assert parsed["apply_attempted"] == [1] or parsed["apply_attempted"] == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/unit/test_observability.py::test_lambda_logger_emits_required_fields tests/unit/test_observability.py::test_lambda_metrics_emit_emf_shape -v
```

Expected: 2 ERRORS — `ModuleNotFoundError: No module named 'lambdas.pipeline.utils.logging'`.

- [ ] **Step 3: Create the shared Lambda logging module**

Create `lambdas/pipeline/utils/logging.py`:

```python
"""Shared Powertools Logger / Tracer / Metrics for ALL Lambdas.

Phase 4 of the deployment-safety roadmap. Every Lambda — pipeline, browser,
scrapers — does:

    from utils.logging import logger, tracer, metrics

(The pipeline lambdas resolve `utils.logging` because lambdas/pipeline/ is on
their PYTHONPATH; browser + scraper lambdas resolve it because the layer
bundles `lambdas/pipeline/utils/` at /opt/python/utils/. See Task 5 step 4.)

Standard fields injected on every line (Powertools handles cold_start,
function_name, function_request_id automatically when @logger.inject_lambda_context
is used on the handler):

  timestamp, level, message, service, stage, function_name, function_request_id,
  cold_start, xray_trace_id, level

Custom enrichment keys (passed via ``extra={...}`` or as kwargs in handler code):

  user_id, job_id, ats, provider, duration_ms, event

Metrics are emitted via Powertools EMF — one CloudWatch metric per
`metrics.add_metric(...)` call, namespaced under ``Naukribaba/${STAGE}``.

PHASE-5 HOOK: Sentry will use Powertools' Logger.add_keys() on init to merge
its release/environment tags. Hooks left in place; do not pre-build.
"""
from __future__ import annotations

import os

from aws_lambda_powertools import Logger, Metrics, Tracer

STAGE = os.environ.get("STAGE", "prod")
SERVICE = "naukribaba-pipeline"
METRICS_NAMESPACE = f"Naukribaba/{STAGE}"

# Logger emits JSON to stdout. Powertools auto-detects the Lambda runtime and
# injects cold_start / function_name / xray_trace_id when the handler is
# decorated with @logger.inject_lambda_context.
logger = Logger(service=SERVICE)

# Tracer is a no-op locally; in Lambda with Tracing: Active it captures
# X-Ray spans for boto3 / requests / supabase auto-instrumentation.
tracer = Tracer(service=SERVICE)

# Metrics writes EMF JSON to stdout; CloudWatch parses it without
# PutMetricData calls. Always namespaced by Stage.
metrics = Metrics(namespace=METRICS_NAMESPACE, service=SERVICE)


__all__ = ["logger", "tracer", "metrics", "STAGE", "SERVICE", "METRICS_NAMESPACE"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_observability.py -v
```

Expected: 5 PASS (3 from Task 2 + 2 from Task 3).

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/utils/logging.py tests/unit/test_observability.py
git commit -m "feat(observability): shared Powertools Logger/Tracer/Metrics for Lambdas

Phase 4 / roadmap §'Observability'. Single import surface
'from utils.logging import logger, tracer, metrics' for all 36 Lambda
handlers (pipeline + browser + scrapers).

- Logger emits JSON to stdout with cold_start, function_name, xray_trace_id
  auto-injected when @logger.inject_lambda_context decorates the handler.
- Tracer wraps boto3/requests for X-Ray spans (no-op locally).
- Metrics writes EMF to stdout under Naukribaba/\${STAGE} namespace.

2 unit tests pass."
```

---

## Task 4: `app.py` — structlog + request_id middleware (TDD + bulk edit)

**Why:** This task does two things: (a) install the request_id middleware so every FastAPI request gets a correlation ID; (b) bulk-replace 30 stdlib `logger.<level>(...)` call sites with structured equivalents. It's the largest single edit in the plan; we follow TDD on the middleware (1 new test) and treat the call-site rewrites as a mechanical bulk-edit with a single representative diff.

**Files:**
- Modify: `app.py` (lines 41 imports, 85 logger init, ~30 call sites between line 100 and line 2665)
- Modify: `tests/unit/test_observability.py` (add middleware test; 1 case)

- [ ] **Step 1: Append the middleware test**

Append to `tests/unit/test_observability.py`:

```python
# --- Phase 4 / Task 4: FastAPI request_id middleware ---


def test_request_id_middleware_binds_from_apigw_context():
    """A request that hits FastAPI must end up with a bound request_id and
    user_id (when X-User-Id header is present)."""
    import importlib
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Re-import in a clean state so middleware registers fresh
    if "config.observability" in sys.modules:
        importlib.reload(sys.modules["config.observability"])
    from config.observability import bind_request_context, clear_request_context, get_fastapi_logger

    captured: list[dict] = []

    app = FastAPI()

    @app.middleware("http")
    async def request_id_mw(request, call_next):
        # Mirrors the real middleware in app.py
        rid = request.headers.get("x-amzn-requestid") or request.headers.get("x-request-id") or "test-rid"
        uid = request.headers.get("x-user-id")
        bind_request_context(request_id=rid, user_id=uid, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        return response

    @app.get("/probe")
    def probe():
        captured.append(dict())  # tested via stdout capture above; here just ensure the route ran
        get_fastapi_logger().info("probe_handled")
        return {"ok": True}

    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        client = TestClient(app)
        r = client.get("/probe", headers={"x-amzn-requestid": "abc123", "x-user-id": "u-9"})
        assert r.status_code == 200
    finally:
        sys.stdout = saved

    line = buf.getvalue().strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["request_id"] == "abc123"
    assert parsed["user_id"] == "u-9"
    assert parsed["path"] == "/probe"
    assert parsed["method"] == "GET"
```

Run: `pytest tests/unit/test_observability.py::test_request_id_middleware_binds_from_apigw_context -v`. Expect FAIL — there is no middleware in `app.py` yet.

- [ ] **Step 2: Replace stdlib logging in `app.py` with structlog**

Find at line 41:

```python
import logging
```

Replace with:

```python
# logging stays as a fallback for libraries that hard-import it; structlog is
# the canonical app logger. See config/observability.py.
import logging  # noqa: F401  (kept so transitive callers aren't broken)
import structlog
```

Find at line 85:

```python
logger = logging.getLogger(__name__)
```

Replace with:

```python
from config.observability import (
    bind_request_context,
    clear_request_context,
    get_fastapi_logger,
)

logger = get_fastapi_logger()
```

- [ ] **Step 3: Add the request_id middleware**

`app.py` already constructs the FastAPI instance via `FastAPI(lifespan=lifespan)` near line 120. Search for the line that adds `CORSMiddleware` and add the request_id middleware *before* it (so it wraps every request including CORS-pre-flighted ones). The new middleware:

```python
@app.middleware("http")
async def _bind_request_id_middleware(request, call_next):
    """Bind request_id + user_id + path + method to structlog context-vars
    for the duration of the request, then clear in finally.

    request_id source order: API Gateway request id → X-Request-Id → uuid fallback.
    user_id source: AuthUser dependency runs after this middleware, so we cannot
    read it here from the dep injection — instead, AuthUser sets a header on
    the request scope and the middleware re-reads it after call_next() if needed.
    For now we read the optional X-User-Id header that the frontend forwards.
    """
    import uuid as _uuid
    rid = (
        request.headers.get("x-amzn-requestid")
        or request.headers.get("x-request-id")
        or _uuid.uuid4().hex
    )
    uid = request.headers.get("x-user-id")
    bind_request_context(
        request_id=rid,
        user_id=uid,
        path=request.url.path,
        method=request.method,
    )
    logger.info("request_started")
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed")
        raise
    finally:
        # status_code only meaningful when call_next returned cleanly; we log
        # request_completed before clear_request_context so the bound vars are
        # still visible.
        try:
            logger.info(
                "request_completed",
                status_code=getattr(locals().get("response"), "status_code", None),
            )
        finally:
            clear_request_context()
    return response
```

Place this directly under `app = FastAPI(...)` and *above* `app.add_middleware(CORSMiddleware, ...)`. (FastAPI applies middleware in reverse-registration order; we want CORS to run *outermost* so OPTIONS pre-flights short-circuit before our binding work, hence registering the request-id middleware first when using `@app.middleware("http")`.)

- [ ] **Step 4: Bulk-edit the ~30 stdlib-style log calls to structured form**

The 30 `logger.<level>(...)` lines in `app.py` (enumerated in the pre-flight grep — lines 105, 110, 114, 173, 368, 399, 470, 473, 480, 546, 549, 567, 578, 656, 667, 698, 992, 1011, 1070, 1752, 1864, 2014, 2085, 2099, 2170, 2231, 2287, 2289, 2351, 2617, 2665) follow stdlib f-string / %-formatting style. Convert them to structlog kwarg style. Pattern:

| Old (stdlib) | New (structlog) |
|---|---|
| `logger.info("API started — %d resumes loaded, AI client ready", len(_resumes))` | `logger.info("api_started", resumes_loaded=len(_resumes))` |
| `logger.error("AI client init failed: %s\n%s", e, traceback.format_exc())` | `logger.exception("ai_client_init_failed", error=str(e))` |
| `logger.warning("Resume file not found: %s", tex_path)` | `logger.warning("resume_file_not_found", tex_path=str(tex_path))` |
| `logger.error("Scoring failed: %s", e)` | `logger.exception("scoring_failed", error=str(e))` |
| `logger.warning(f"Failed to start re-tailor for {job['job_hash']}: {e}")` | `logger.warning("retailor_start_failed", job_hash=job["job_hash"], error=str(e))` |
| `logger.info("Enqueued task %s (%s) to SQS", task_id, task_type)` | `logger.info("task_enqueued", task_id=task_id, task_type=task_type)` |

**Conversion rules (apply mechanically to every site):**
1. The first arg becomes a snake_case event-name string. Drop quote-and-format styling; use the imperative past tense (`apply_attempted`, `tailor_failed`, `cache_hit`).
2. Every printf placeholder / f-string interpolation becomes a kwarg with a stable name (`error=str(e)`, `user_id=user_id`, `job_hash=job["job_hash"]`).
3. `logger.error(..., exc_info=True)` and stack-trace formatting become `logger.exception(...)` — structlog's `format_exc_info` processor includes the exception block automatically.
4. NEVER include raw `e` (Exception object); always `str(e)`. Powertools/structlog can serialize exceptions, but JSONRenderer chokes on circular refs — `str(e)` is safe.
5. NEVER pass PII (full email body, full resume text, full JD) as a kwarg — log a hash or first-N-chars instead.

**Representative diff** at line 105:

```diff
-    except Exception as e:
-        import traceback
-        logger.error("AI client init failed: %s\n%s", e, traceback.format_exc())
+    except Exception as e:
+        logger.exception("ai_client_init_failed", error=str(e))
         _ai_client = None
```

Apply the same rule to every line in the grep output. Treat this as **one bulk edit, one commit** — do not split into 30 commits. After all 30 sites are converted, the only remaining `logger.` calls in `app.py` should be structlog calls.

- [ ] **Step 5: Run the existing test suite + the new middleware test**

```bash
pytest tests/unit/test_observability.py tests/unit/test_apply_endpoints.py tests/contract/ -v --tb=short
```

Expected: all green. (The contract suite imports `app.py`; if any rewrite mistakenly drops a kwarg the import will fail and you'll see it here.)

- [ ] **Step 6: Spot-check by hitting `/api/health` locally**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
source .venv/bin/activate
uvicorn app:app --reload --port 8000 &
sleep 3
curl -sS -H "X-Request-Id: test-rid-001" -H "X-User-Id: u-test" http://localhost:8000/api/health
# kill the uvicorn process
pkill -f "uvicorn app:app" || true
```

Expected stdout from uvicorn: at least three JSON lines (one for `request_started`, one app-level `info`, one for `request_completed`) with `request_id="test-rid-001"` and `user_id="u-test"`. If you see plain text or stdlib formatting, the rewrite missed a site — grep for `logger.\(info\|warning\|error\|debug\|exception\)(` again and finish the edits.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/unit/test_observability.py
git commit -m "feat(app): structlog + request_id middleware in FastAPI

Phase 4 / roadmap §'Observability' — convert app.py from stdlib logging
to structlog (config.observability) and add a request-id binding
middleware that captures (request_id, user_id, path, method) once per
request from API Gateway / X-Request-Id headers.

Bulk-edit: ~30 logger.{info,warning,error,exception} call sites converted
from printf / f-string style to structured kwargs (event=snake_case,
error=str(e), …) per the Task 4 conversion rules.

4 unit tests (3 from Task 2 + 1 middleware test) pass; contract suite
unaffected (only formatting changed, not behavior)."
```

---

## Task 5: Pilot Lambda migration — `lambdas/pipeline/score_batch.py`

**Why:** Before bulk-editing 22 pipeline Lambdas, we get one canonical example right. score_batch is a great pilot: it has 9 logger calls, real decision points (`apply_attempted` is *not* one of them — that lives in ws_route — but `job_scored` and `job_skipped` are), and it's already imported in tests. The pattern from this task is what Task 6 mass-applies.

**Files:**
- Modify: `lambdas/pipeline/score_batch.py`

- [ ] **Step 1: Read the current handler skeleton**

Lines 1-13 currently:

```python
import json
import logging
import random
import statistics
import uuid
from datetime import datetime


from ai_helper import ai_complete_cached, get_supabase
from shared.apply_platform import classify_apply_platform

logger = logging.getLogger()
logger.setLevel(logging.INFO)
```

- [ ] **Step 2: Swap the import + logger block**

Replace lines 2-13 with:

```python
import logging  # noqa: F401  (kept for any transitive callers)
import random
import statistics
import time
import uuid
from datetime import datetime

from ai_helper import ai_complete_cached, get_supabase
from shared.apply_platform import classify_apply_platform
from utils.logging import logger, metrics, tracer

from aws_lambda_powertools.metrics import MetricUnit
```

- [ ] **Step 3: Wrap the handler with Powertools decorators**

Find the existing `def handler(event, context):` (around line 95) and immediately above it add:

```python
@logger.inject_lambda_context(log_event=False, correlation_id_path="user_id")
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event, context):
```

Three decorators do three things:
- `@logger.inject_lambda_context`: auto-adds `cold_start`, `function_name`, `function_request_id`, `xray_trace_id`. `correlation_id_path="user_id"` extracts `event["user_id"]` and binds it as the correlation id (so every line in this invocation carries `correlation_id`).
- `@tracer.capture_lambda_handler`: opens an X-Ray segment for the entire handler; sub-segments around boto3 / requests are auto-captured.
- `@metrics.log_metrics(capture_cold_start_metric=True)`: flushes any added metrics on return + emits a `ColdStart` metric on cold invocations.

`log_event=False` because the event contains `user_id` (PII) and we don't want it dumped verbatim into CloudWatch.

- [ ] **Step 4: Rewrite the 9 logger calls + add metric emission**

Old:

```python
logger.warning(f"[score_batch] No resume found for user {user_id}")
```

New:

```python
logger.warning("no_resume_for_user", user_id=user_id)
metrics.add_metric(name="score_batch_no_resume", unit=MetricUnit.Count, value=1)
```

Apply the same rule to all 9 lines (117, 124, 132, 184, 187, plus 4 others later in the file). For the success path inside the for-loop, after `db.table("jobs").insert(job_record).execute()` succeeds, emit:

```python
metrics.add_metric(name="job_scored", unit=MetricUnit.Count, value=1)
metrics.add_dimension(name="tier", value=score_to_tier(match_score))
logger.info(
    "job_scored",
    job_hash=job["job_hash"],
    user_id=user_id,
    match_score=match_score,
    tier=score_to_tier(match_score),
    source=job["source"],
)
```

For the skip path:

```python
metrics.add_metric(name="job_skipped", unit=MetricUnit.Count, value=1)
metrics.add_dimension(name="reason", value=skip_status)
```

At the end of the handler, before returning, emit one summary line:

```python
logger.info(
    "score_batch_completed",
    user_id=user_id,
    matched=len(matched_items),
    skipped=skipped_count,
    total=len(jobs),
)
```

- [ ] **Step 5: Run the existing score_batch tests**

```bash
pytest tests/unit/test_score_batch.py -v
```

Expected: all pass. Powertools accepts a missing `context` in unit tests gracefully when the decorator's `log_event` is False; the existing tests should not need updates. If any test fails because it asserted `caplog` text in the old `logger.warning` style, update those assertions to match the structured form (`record.message == "no_resume_for_user"`, `record.user_id == "..."`).

- [ ] **Step 6: Verify the layer bundles `lambdas/pipeline/utils/`**

The browser Lambdas (`lambdas/browser/ws_*.py`) and scraper Lambdas (`lambdas/scrapers/scrape_*.py`) live in different packages from `lambdas/pipeline/utils/`. They will import `from utils.logging import ...` only if `utils/` is on their PYTHONPATH. Two options:

**Option A (recommended): copy `utils/` into the layer.** Add to `layer/build.sh` after the existing `cp -r ../shared python/shared` line:

```bash
# 3. Bundle the pipeline utils (Powertools logger/tracer/metrics) so every Lambda
# (browser, scrapers, pipeline alike) can do `from utils.logging import logger`.
mkdir -p python/utils
cp -r ../lambdas/pipeline/utils/* python/utils/
echo "utils/ files in layer:"
ls python/utils/
```

This mirrors the `shared/` precedent set by the Apr 26 layer-build fix (PR #10 ancestor commit).

**Option B (lightweight):** symlink `utils/` per Lambda directory in the SAM build step. Rejected — introduces drift; option A is one-time and bulletproof.

Apply Option A.

- [ ] **Step 7: Commit**

```bash
git add lambdas/pipeline/score_batch.py layer/build.sh
git commit -m "feat(observability): pilot Powertools migration on score_batch + layer wiring

score_batch.py now uses utils.logging (Powertools Logger/Tracer/Metrics) with
@logger.inject_lambda_context + @tracer.capture_lambda_handler +
@metrics.log_metrics. Emits job_scored / job_skipped / score_batch_no_resume
EMF metrics under Naukribaba/\${STAGE}.

layer/build.sh bundles lambdas/pipeline/utils/ at /opt/python/utils/ so every
Lambda (browser, scrapers, pipeline) can do 'from utils.logging import logger'.
Mirrors the shared/ precedent from PR #10 (Apr 26 layer-build fix).

This is the reference template for the bulk migration in Tasks 6, 7, 8."
```

---

## Task 6: Bulk migrate the remaining 21 pipeline Lambdas

**Why:** Mechanical edit, 21 files, same pattern as Task 5. We do *one* commit per file group (split below) so a regression in one is easy to bisect — but use a single shared diff template.

**Files (per pre-flight grep, 22 minus the score_batch pilot = 21):**
- `lambdas/pipeline/aggregate_scores.py`
- `lambdas/pipeline/ai_helper.py`
- `lambdas/pipeline/check_expiry.py`
- `lambdas/pipeline/chunk_hashes.py`
- `lambdas/pipeline/compile_latex.py`
- `lambdas/pipeline/find_contacts.py`
- `lambdas/pipeline/generate_cover_letter.py`
- `lambdas/pipeline/load_config.py`
- `lambdas/pipeline/merge_dedup.py`
- `lambdas/pipeline/notify_error.py`
- `lambdas/pipeline/post_score.py`
- `lambdas/pipeline/save_job.py`
- `lambdas/pipeline/save_metrics.py`
- `lambdas/pipeline/self_improve.py`
- `lambdas/pipeline/self_improver.py`
- `lambdas/pipeline/send_email.py`
- `lambdas/pipeline/send_followup_reminders.py`
- `lambdas/pipeline/send_stale_nudges.py`
- `lambdas/pipeline/tailor_resume.py`

(`__init__.py` is empty; no edit. `parse_sections.py` and `__init__.py` may not have a logger — skip if grep didn't find `import logging` in them.)

- [ ] **Step 1: Define the bulk-edit template (mental model)**

For every file:

**Find:**

```python
import logging
...
logger = logging.getLogger()  # or logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # if present
```

**Replace with:**

```python
import logging  # noqa: F401
from utils.logging import logger, metrics, tracer
from aws_lambda_powertools.metrics import MetricUnit
```

**Find handler:**

```python
def handler(event, context):
```

**Replace with:**

```python
@logger.inject_lambda_context(log_event=False, correlation_id_path="user_id")
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event, context):
```

(If the function name isn't `handler`, use the name SAM points at via `Handler:` in `template.yaml`.)

**Convert each `logger.<level>(...)` call** to structured-kwarg style per the rules in Task 4 Step 4.

**Add metric emission** at decision points:

| Lambda | Decision point | Metric |
|---|---|---|
| `tailor_resume.py` | success path | `resume_tailored` (Count, dim: `tier=`) |
| `tailor_resume.py` | failure path | `resume_tailor_failed` (Count, dim: `reason=`) |
| `compile_latex.py` | success | `latex_compiled` (Count) |
| `compile_latex.py` | failure | `latex_compile_failed` (Count, dim: `error_type=`) |
| `generate_cover_letter.py` | success | `cover_letter_generated` (Count) |
| `find_contacts.py` | success | `contacts_found` (Count, dim: `count=N` as Value not Dim — emit one metric with Value=count) |
| `merge_dedup.py` | end | `pipeline_dedup_merged` (Count, Value=merged_count) |
| `aggregate_scores.py` | end | `pipeline_aggregate_completed` (Count) |
| `save_job.py` | success / failure | `job_saved` / `job_save_failed` (Count) |
| `save_metrics.py` | end | `pipeline_run_completed` (Count) — **this is the contract Phase 6 smoke-tests assert against** |
| `send_email.py` | success / failure | `email_sent` / `email_send_failed` (Count) |
| `notify_error.py` | invocation | `pipeline_error_notified` (Count) |
| `post_score.py` | success | `score_posted` (Count) |
| `chunk_hashes.py` | end | `hashes_chunked` (Count, Value=chunk_count) |
| `check_expiry.py` | end | `expiry_checked` (Count) |
| `load_config.py` | end | `config_loaded` (Count) |
| `self_improve.py`, `self_improver.py` | end | `self_improve_completed` (Count) |
| `send_followup_reminders.py` | end | `followups_sent` (Count, Value=N) |
| `send_stale_nudges.py` | end | `stale_nudges_sent` (Count, Value=N) |
| `find_contacts.py`, `ai_helper.py` | provider failover | `ai_provider_failed` (Count, dim: `provider=groq|deepseek|openrouter|claude`) — see Task 6 Step 3 below |

- [ ] **Step 2: Apply the template to each file in alphabetical order**

For each file in the list, perform Steps 1–4 from Task 5 (swap imports, decorate handler, rewrite log calls, add metrics). Run the matching unit test after each file:

```bash
pytest tests/unit/test_<lambda_name>.py -v
```

(Test file names: `test_check_expiry.py`, `test_compile_latex.py`, `test_merge_dedup.py`, `test_save_job.py`, `test_score_batch.py`, `test_send_email.py`, `test_self_improve_lambda.py`, `test_self_improver.py`, etc., per the pre-flight `tests/unit/` listing.)

If a unit test fails because it asserted text in `caplog`, update the assertion to inspect the structured fields:

```python
# Old:
assert "No resume found" in caplog.text

# New:
record = next(r for r in caplog.records if getattr(r, "message", None) == "no_resume_for_user")
assert record.user_id == "u-42"
```

- [ ] **Step 3: Special case — `ai_helper.py` provider-failover metric**

`ai_helper.py` is the AI provider failover chain. Inside whatever helper function loops through Groq → DeepSeek → OpenRouter → Claude, on each failure call:

```python
metrics.add_metric(name="ai_provider_failed", unit=MetricUnit.Count, value=1)
metrics.add_dimension(name="provider", value=provider_name)  # "groq" | "deepseek" | …
logger.warning(
    "ai_provider_failed",
    provider=provider_name,
    error=str(e),
    next_provider=next_in_chain,
)
```

This is the metric that drives the dashboard's "AI provider failover heat map" widget (Task 10) and Logs Insights query "AI provider failover events" (Task 12).

- [ ] **Step 4: Run the full pipeline-test suite**

```bash
pytest tests/unit/ -v --tb=short -k "pipeline or score or tailor or compile or save or send or merge or aggregate or self_improve or find_contacts or check_expiry or chunk or load_config or notify or post_score"
```

Expected: all green. If a few caplog assertions need updating, fix them.

- [ ] **Step 5: Commit**

Group into 3 commits to keep the diff readable:

```bash
# Commit A: scoring + matching (5 files)
git add lambdas/pipeline/{score_batch,aggregate_scores,merge_dedup,chunk_hashes,post_score}.py tests/unit/
git commit -m "feat(observability): Powertools migration — scoring + dedup lambdas (Phase 4)

aggregate_scores, merge_dedup, chunk_hashes, post_score: same template as
score_batch (Task 5) — utils.logging imports, three Powertools decorators
on handler, snake_case event names, EMF metrics at decision points.

job_scored / job_skipped / pipeline_dedup_merged / hashes_chunked /
pipeline_aggregate_completed metrics emit under Naukribaba/\${STAGE}."

# Commit B: artifact generation + I/O (8 files)
git add lambdas/pipeline/{tailor_resume,compile_latex,generate_cover_letter,find_contacts,save_job,save_metrics,send_email,notify_error}.py tests/unit/
git commit -m "feat(observability): Powertools migration — artifact + I/O lambdas (Phase 4)

tailor_resume / compile_latex / generate_cover_letter / find_contacts /
save_job / save_metrics / send_email / notify_error: same template.

Emits resume_tailored / latex_compiled / cover_letter_generated /
contacts_found / job_saved / pipeline_run_completed / email_sent /
pipeline_error_notified metrics. pipeline_run_completed is the contract
Phase 6 smoke tests will assert against."

# Commit C: scheduled + maintenance (6 files)
git add lambdas/pipeline/{check_expiry,load_config,self_improve,self_improver,send_followup_reminders,send_stale_nudges,ai_helper}.py tests/unit/
git commit -m "feat(observability): Powertools migration — scheduled + AI failover (Phase 4)

check_expiry / load_config / self_improve(_r) / send_followup_reminders /
send_stale_nudges + ai_helper provider-failover metric.

ai_helper emits ai_provider_failed{provider=groq|deepseek|openrouter|claude}
which drives the dashboard's AI failover heat-map widget (Task 10) and the
Logs Insights query in monitoring/queries.md (Task 12)."
```

---

## Task 7: Bulk migrate 3 browser Lambdas

**Files:**
- `lambdas/browser/ws_connect.py`
- `lambdas/browser/ws_disconnect.py`
- `lambdas/browser/ws_route.py`

These are short (~50 LOC each, per pre-flight `wc -l`).

- [ ] **Step 1: Apply the template (same as Task 6 Step 1)**

`ws_connect.py` and `ws_disconnect.py` follow the standard pattern. For each:

1. Swap `import logging; logger = logging.getLogger()` for `from utils.logging import logger, metrics, tracer; from aws_lambda_powertools.metrics import MetricUnit`.
2. Decorate `handler` with the three Powertools decorators.
3. Convert log calls to snake_case events.

Decision-point metrics:

| Lambda | Event | Metric |
|---|---|---|
| `ws_connect` | new browser session attached | `browser_session_started` (Count) |
| `ws_connect` | auth rejected | `browser_session_auth_failed` (Count) |
| `ws_disconnect` | clean disconnect | `browser_session_ended` (Count) |
| `ws_route` | message relayed (line 47, after post_to_connection) | `apply_attempted` (Count, dim: `direction=frontend_to_browser` or `browser_to_frontend`) — **this is the ws_route apply event the apply_failed_rate alarm in Task 11 watches** |
| `ws_route` | GoneException recovery (line 50) | `ws_peer_gone` (Count) |
| `ws_route` | other PostToConnection failure | `ws_post_failed` (Count) |

- [ ] **Step 2: Special case — apply_attempted vs apply_succeeded vs apply_failed**

The roadmap specifies three business-metric counters: `apply_attempted`, `apply_succeeded`, `apply_failed`. The clean place to emit each:

| Metric | Where | Why |
|---|---|---|
| `apply_attempted` | `ws_route` when a `direction=frontend_to_browser` action message ("submit", "next", "click_apply") arrives — first relay for that session_id | First-touch counter |
| `apply_succeeded` | `ws_route` when a `direction=browser_to_frontend` message body contains `{"event":"apply_done","success":true}` — parse JSON inside the existing 128KB body window | Success counter |
| `apply_failed` | `ws_route` same parser, `success=false` | Failure counter |

Add a small helper inside `ws_route.py`:

```python
def _maybe_emit_apply_metric(body: bytes | str, sender_role: str) -> None:
    """Inspect outbound (browser→frontend) JSON for apply_done events.
    Emits EMF metrics for the apply funnel. Never raises."""
    if sender_role != "browser":
        return
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else body
        msg = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if msg.get("event") != "apply_done":
        return
    if msg.get("success"):
        metrics.add_metric(name="apply_succeeded", unit=MetricUnit.Count, value=1)
    else:
        metrics.add_metric(name="apply_failed", unit=MetricUnit.Count, value=1)
        metrics.add_dimension(name="reason", value=str(msg.get("reason", "unknown")))
```

Call it after the successful `post_to_connection` block in `ws_route.handler`.

For `apply_attempted`, in the same handler, emit before the `post_to_connection` call when `sender_role == "frontend"`:

```python
metrics.add_metric(name="apply_attempted", unit=MetricUnit.Count, value=1)
```

(`json` import: add to top of file.)

- [ ] **Step 3: Run browser tests**

```bash
pytest tests/unit/test_ws_connect.py tests/unit/test_ws_disconnect.py tests/unit/test_ws_route.py -v
```

Expected: all green. The tests already exist (per pre-flight `tests/unit/` listing) and assert on browser_sessions side effects, not log text — so the only adjustment needed is if any test asserts on `caplog`.

- [ ] **Step 4: Commit**

```bash
git add lambdas/browser/ws_connect.py lambdas/browser/ws_disconnect.py lambdas/browser/ws_route.py tests/unit/
git commit -m "feat(observability): Powertools migration — browser/ws lambdas (Phase 4)

ws_connect, ws_disconnect, ws_route migrate from stdlib logging to
utils.logging Powertools surface.

ws_route emits the apply funnel:
- apply_attempted (frontend→browser action)
- apply_succeeded (browser→frontend apply_done success=true)
- apply_failed (browser→frontend apply_done success=false, dim: reason=)

These three metrics drive the dashboard 'business metrics' widget (Task 10)
and the apply_failed_rate alarm (Task 11)."
```

---

## Task 8: Light pass on 11 scraper Lambdas

**Why:** Scrapers don't have decision-point branches worth instrumenting; one metric per invocation is enough. Goal here is to align all 36 Lambdas on the same logger surface so debugging is consistent.

**Files (per pre-flight grep):**
- `lambdas/scrapers/scrape_adzuna.py`
- `lambdas/scrapers/scrape_apify.py`
- `lambdas/scrapers/scrape_ashby.py`
- `lambdas/scrapers/scrape_contacts.py`
- `lambdas/scrapers/scrape_glassdoor.py`
- `lambdas/scrapers/scrape_greenhouse.py`
- `lambdas/scrapers/scrape_hn.py`
- `lambdas/scrapers/scrape_indeed.py`
- `lambdas/scrapers/scrape_irish.py`
- `lambdas/scrapers/scrape_linkedin.py`
- `lambdas/scrapers/scrape_yc.py`

- [ ] **Step 1: Apply the bulk template, simplified**

For each scraper:

1. Swap imports as in Task 6 Step 1.
2. Decorate the handler with the same three decorators.
3. Rewrite log calls to snake_case events.
4. Add **one** metric emission at the end of the successful path:

```python
metrics.add_metric(name="scraper_jobs_returned", unit=MetricUnit.Count, value=len(results))
metrics.add_dimension(name="source", value=SOURCE_NAME)  # e.g., "linkedin"
```

`SOURCE_NAME` is already a module-level constant in most scrapers. If a scraper doesn't define it, use the literal source string from its filename.

5. On exception inside the handler (catch and re-raise so X-Ray records it as a fault), emit:

```python
metrics.add_metric(name="scraper_failed", unit=MetricUnit.Count, value=1)
metrics.add_dimension(name="source", value=SOURCE_NAME)
```

- [ ] **Step 2: Run scraper tests**

```bash
pytest tests/unit/test_scrape_adzuna.py tests/unit/test_scrape_apify.py tests/unit/test_scrape_hn.py tests/unit/test_scrape_yc.py tests/unit/test_scrape_irish.py tests/unit/test_gradireland_scraper.py -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add lambdas/scrapers/ tests/unit/
git commit -m "feat(observability): Powertools migration — scraper lambdas (Phase 4)

11 scrapers migrate to utils.logging. Each emits scraper_jobs_returned{source}
on success and scraper_failed{source} on failure. Metrics feed the dashboard
'jobs/day' widget (Task 10).

No structural code changes — purely logging + EMF instrumentation."
```

---

## Task 9: `template.yaml` — `Tracing: Active` + dashboard resource

**Files:**
- Modify: `template.yaml` (line 5 Globals, end-of-file Resources)

- [ ] **Step 1: Add `Tracing: Active` to `Globals.Function`**

Find lines 5–8:

```yaml
Globals:
  Function:
    Timeout: 900
    MemorySize: 1024
```

Replace with:

```yaml
Globals:
  Function:
    Timeout: 900
    MemorySize: 1024
    # Phase 4 — observability roadmap §4. Enable AWS X-Ray for every Lambda
    # (no per-function override needed). Combined with Powertools Tracer in
    # utils.logging, this captures end-to-end traces:
    # API Gateway → Lambda (cold start visible) → Supabase RPC → AI provider → response.
    Tracing: Active
    Environment:
      Variables:
        # POWERTOOLS_SERVICE_NAME is read by Powertools Logger/Tracer/Metrics
        # to populate the `service` field on every line. Stage flows through
        # to the EMF namespace via utils.logging:METRICS_NAMESPACE.
        POWERTOOLS_SERVICE_NAME: naukribaba-pipeline
        POWERTOOLS_METRICS_NAMESPACE: !Sub "Naukribaba/${Stage}"
        # If Stage parameter doesn't exist yet (Phase 3 not merged), this Sub
        # resolves to literal "Naukribaba/" — fix by replacing with literal
        # "Naukribaba/prod" until Phase 3 lands.
        STAGE: !Ref Stage
        LOG_LEVEL: INFO
```

**If `Stage` parameter is not yet defined** (Task 0 outcome 1), add it to the `Parameters:` block (around line 9):

```yaml
Parameters:
  Stage:
    Type: String
    Default: prod
    AllowedValues: [staging, prod]
    Description: Deployment stage. Phase 3 will plug both stacks into this; until then default to prod.
  GroqApiKey:
    ...
```

- [ ] **Step 2: Add IAM permission for X-Ray writes**

Each Lambda role needs `xray:PutTraceSegments` + `xray:PutTelemetryRecords` to write spans. The cleanest path is the SAM-managed policy `AWSXrayWriteOnlyAccess`. Find the `Globals.Function` block (we just edited it) and confirm it has no `Policies:` key. If it doesn't (current state), add a per-function `Policies:` line is impractical. Instead, use the SAM `AutoPublishAlias`-style approach: add an SAM-level managed policy on each function. **Easier path**: add `tracing` to the `Policies:` list of every function — actually, AWS handles this automatically when `Tracing: Active` is set on a SAM function and the function role lacks the permission, the deploy will fail. To be safe, add a global IAM policy attachment.

Replace the `Globals.Function` `Tracing: Active` block above so it ALSO sets `Policies` at the global level:

Actually, SAM Globals doesn't accept `Policies`. The least-invasive fix: add per-function `Policies` is impractical for 36 functions. Instead, add one block at the bottom of `Resources:` that creates an X-Ray managed policy and attaches to the role:

This is over-engineered. **The pragmatic solution AWS documents is**: set `Tracing: Active` on the function, and SAM's transform automatically adds the `AWSXRayDaemonWriteAccess` managed policy to the function's auto-generated execution role. Confirm this by `sam validate` after editing. If it fails, fall back to per-function `Policies: - AWSXRayDaemonWriteAccess` rolled out in a follow-up.

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
sam validate --template template.yaml
```

Expected: `template.yaml is a valid SAM Template`.

- [ ] **Step 3: Add the CloudWatch Dashboard resource**

At the end of the `Resources:` section (before `Outputs:` if present), add:

```yaml
  # ----------------------------------------------------------------
  # Phase 4 / observability — CloudWatch dashboard
  # The widget JSON is in monitoring/dashboard.json. We ${Sub}stitute
  # ${Stage} so widgets reference the right metric namespace.
  # ----------------------------------------------------------------
  ObservabilityDashboard:
    Type: AWS::CloudWatch::Dashboard
    Properties:
      DashboardName: !Sub "naukribaba-${Stage}"
      DashboardBody:
        Fn::Sub:
          - |
            ${Body}
          - Body: !Sub
              - '${TemplateBody}'
              - TemplateBody: !Sub
                  - ${Raw}
                  - Raw: |
                      __DASHBOARD_JSON_PLACEHOLDER__
```

The `Fn::Sub` indirection above is messy; use the simpler include pattern instead. Replace the `ObservabilityDashboard` block with:

```yaml
  ObservabilityDashboard:
    Type: AWS::CloudWatch::Dashboard
    Properties:
      DashboardName: !Sub "naukribaba-${Stage}"
      DashboardBody:
        Fn::Transform:
          Name: AWS::Include
          Parameters:
            Location: monitoring/dashboard.json
```

`AWS::Include` requires the file to be uploaded to S3 first. If that's not viable for Phase 4, use the inline embed (simpler):

```yaml
  ObservabilityDashboard:
    Type: AWS::CloudWatch::Dashboard
    Properties:
      DashboardName: !Sub "naukribaba-${Stage}"
      DashboardBody: !Sub |
        {
          "widgets": [
            ... (the contents of monitoring/dashboard.json with ${Stage} substituted) ...
          ]
        }
```

**Decision:** use the inline `!Sub |` form. Yes, it duplicates the JSON between `monitoring/dashboard.json` (the source-of-truth file) and `template.yaml`. Trade-off accepted because `AWS::Include` requires an S3 staging step in `deploy.yml` we don't want to add in Phase 4. Add a CI assertion in Task 14 that the two stay in sync (`diff <(yq '.Resources.ObservabilityDashboard.Properties.DashboardBody' template.yaml) monitoring/dashboard.json`).

For now, in `template.yaml`, leave a placeholder + TODO at the inline location, like:

```yaml
  ObservabilityDashboard:
    Type: AWS::CloudWatch::Dashboard
    Properties:
      DashboardName: !Sub "naukribaba-${Stage}"
      # Body inlined from monitoring/dashboard.json — keep in sync via the
      # CI check in Task 14. Both files use ${Stage} via SAM Sub.
      DashboardBody: !Sub
        - |
          {
            "widgets": [ ${Widgets} ]
          }
        - Widgets: ""  # filled in by Task 10
```

We'll fill in the actual widgets in Task 10.

- [ ] **Step 4: Validate**

```bash
sam validate --template template.yaml
```

Expected: valid.

- [ ] **Step 5: Commit**

```bash
git add template.yaml
git commit -m "feat(observability): Tracing:Active globally + CloudWatch dashboard skeleton

Globals.Function gains Tracing: Active (X-Ray for every Lambda) plus three
Powertools env vars: POWERTOOLS_SERVICE_NAME, POWERTOOLS_METRICS_NAMESPACE,
STAGE. SAM transform auto-attaches AWSXRayDaemonWriteAccess policies.

ObservabilityDashboard resource references monitoring/dashboard.json (widgets
filled in by Task 10). Dashboard name 'naukribaba-\${Stage}' splits cleanly
when Phase 3 staging stack lands."
```

---

## Task 10: `monitoring/dashboard.json` — 10 widgets

**Why:** One URL the operator opens to answer "is the system healthy?". 10 widgets total — the roadmap says 8–12; we settle on 10 to cover invocations / errors / latency / throttles / business funnel / AI provider failover / alarm states.

**Files:**
- Create: `monitoring/dashboard.json`
- Modify: `template.yaml` (inline the JSON into the dashboard body)

- [ ] **Step 1: Create `monitoring/` directory**

```bash
mkdir -p /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/monitoring
```

- [ ] **Step 2: Write `monitoring/dashboard.json`**

Create `monitoring/dashboard.json` with the following content. The file is a JSON dashboard body; `${Stage}` placeholders are substituted at deploy time by the `!Sub` in `template.yaml`. Region is hardcoded to `eu-west-1` (NaukriBaba's AWS region).

```json
{
  "widgets": [
    {
      "type": "metric",
      "x": 0, "y": 0, "width": 8, "height": 6,
      "properties": {
        "title": "Lambda invocations (top 5)",
        "view": "timeSeries",
        "stacked": false,
        "region": "eu-west-1",
        "metrics": [
          [ "AWS/Lambda", "Invocations", "FunctionName", "naukribaba-ws-route" ],
          [ "...", "FunctionName", "naukribaba-tailor-resume" ],
          [ "...", "FunctionName", "naukribaba-compile-latex" ],
          [ "...", "FunctionName", "naukribaba-score-batch" ],
          [ "...", "FunctionName", "naukribaba-save-job" ]
        ],
        "stat": "Sum",
        "period": 300,
        "yAxis": { "left": { "label": "invocations / 5min" } }
      }
    },
    {
      "type": "metric",
      "x": 8, "y": 0, "width": 8, "height": 6,
      "properties": {
        "title": "Lambda errors (top 5) — alarm at >0",
        "view": "timeSeries",
        "stacked": false,
        "region": "eu-west-1",
        "metrics": [
          [ "AWS/Lambda", "Errors", "FunctionName", "naukribaba-ws-route" ],
          [ "...", "FunctionName", "naukribaba-tailor-resume" ],
          [ "...", "FunctionName", "naukribaba-compile-latex" ],
          [ "...", "FunctionName", "naukribaba-score-batch" ],
          [ "...", "FunctionName", "naukribaba-save-job" ]
        ],
        "stat": "Sum",
        "period": 300,
        "yAxis": { "left": { "label": "errors / 5min" } }
      }
    },
    {
      "type": "metric",
      "x": 16, "y": 0, "width": 8, "height": 6,
      "properties": {
        "title": "Lambda p95 duration (ms)",
        "view": "timeSeries",
        "stacked": false,
        "region": "eu-west-1",
        "metrics": [
          [ "AWS/Lambda", "Duration", "FunctionName", "naukribaba-ws-route", { "stat": "p95" } ],
          [ "...", "FunctionName", "naukribaba-tailor-resume", { "stat": "p95" } ],
          [ "...", "FunctionName", "naukribaba-compile-latex", { "stat": "p95" } ],
          [ "...", "FunctionName", "naukribaba-score-batch", { "stat": "p95" } ]
        ],
        "period": 300,
        "yAxis": { "left": { "label": "ms" } }
      }
    },
    {
      "type": "metric",
      "x": 0, "y": 6, "width": 8, "height": 6,
      "properties": {
        "title": "Lambda throttles",
        "view": "timeSeries",
        "stacked": true,
        "region": "eu-west-1",
        "metrics": [
          [ "AWS/Lambda", "Throttles", "FunctionName", "naukribaba-ws-route" ],
          [ "...", "FunctionName", "naukribaba-tailor-resume" ],
          [ "...", "FunctionName", "naukribaba-compile-latex" ],
          [ "...", "FunctionName", "naukribaba-score-batch" ]
        ],
        "stat": "Sum",
        "period": 300
      }
    },
    {
      "type": "metric",
      "x": 8, "y": 6, "width": 8, "height": 6,
      "properties": {
        "title": "Apply funnel (attempted / succeeded / failed)",
        "view": "timeSeries",
        "stacked": true,
        "region": "eu-west-1",
        "metrics": [
          [ "Naukribaba/${Stage}", "apply_attempted", "service", "naukribaba-pipeline" ],
          [ "Naukribaba/${Stage}", "apply_succeeded", "service", "naukribaba-pipeline" ],
          [ "Naukribaba/${Stage}", "apply_failed",    "service", "naukribaba-pipeline" ]
        ],
        "stat": "Sum",
        "period": 300,
        "yAxis": { "left": { "label": "applies / 5min" } }
      }
    },
    {
      "type": "metric",
      "x": 16, "y": 6, "width": 8, "height": 6,
      "properties": {
        "title": "Pipeline output (jobs scored, resumes tailored, runs completed)",
        "view": "timeSeries",
        "stacked": false,
        "region": "eu-west-1",
        "metrics": [
          [ "Naukribaba/${Stage}", "job_scored",              "service", "naukribaba-pipeline" ],
          [ "Naukribaba/${Stage}", "resume_tailored",         "service", "naukribaba-pipeline" ],
          [ "Naukribaba/${Stage}", "pipeline_run_completed",  "service", "naukribaba-pipeline" ]
        ],
        "stat": "Sum",
        "period": 3600,
        "yAxis": { "left": { "label": "count / hour" } }
      }
    },
    {
      "type": "metric",
      "x": 0, "y": 12, "width": 12, "height": 6,
      "properties": {
        "title": "AI provider failover (heat map by provider)",
        "view": "timeSeries",
        "stacked": true,
        "region": "eu-west-1",
        "metrics": [
          [ "Naukribaba/${Stage}", "ai_provider_failed", "provider", "groq" ],
          [ "...", "provider", "deepseek" ],
          [ "...", "provider", "openrouter" ],
          [ "...", "provider", "claude" ]
        ],
        "stat": "Sum",
        "period": 300
      }
    },
    {
      "type": "metric",
      "x": 12, "y": 12, "width": 12, "height": 6,
      "properties": {
        "title": "Scrapers — jobs returned by source",
        "view": "timeSeries",
        "stacked": true,
        "region": "eu-west-1",
        "metrics": [
          [ "Naukribaba/${Stage}", "scraper_jobs_returned", "source", "linkedin" ],
          [ "...", "source", "indeed" ],
          [ "...", "source", "irish" ],
          [ "...", "source", "adzuna" ],
          [ "...", "source", "hn" ],
          [ "...", "source", "yc" ],
          [ "...", "source", "greenhouse" ],
          [ "...", "source", "ashby" ]
        ],
        "stat": "Sum",
        "period": 3600
      }
    },
    {
      "type": "alarm",
      "x": 0, "y": 18, "width": 24, "height": 4,
      "properties": {
        "title": "Alarm states",
        "alarms": [
          "arn:aws:cloudwatch:eu-west-1:${AWS::AccountId}:alarm:naukribaba-${Stage}-ws-route-apply-failed-rate",
          "arn:aws:cloudwatch:eu-west-1:${AWS::AccountId}:alarm:naukribaba-${Stage}-tailor-resume-errors",
          "arn:aws:cloudwatch:eu-west-1:${AWS::AccountId}:alarm:naukribaba-${Stage}-compile-latex-errors",
          "arn:aws:cloudwatch:eu-west-1:${AWS::AccountId}:alarm:naukribaba-${Stage}-score-batch-errors"
        ]
      }
    },
    {
      "type": "log",
      "x": 0, "y": 22, "width": 24, "height": 6,
      "properties": {
        "title": "Recent errors (last 1h, all Lambdas)",
        "region": "eu-west-1",
        "query": "SOURCE '/aws/lambda/naukribaba-ws-route' | SOURCE '/aws/lambda/naukribaba-tailor-resume' | SOURCE '/aws/lambda/naukribaba-compile-latex' | SOURCE '/aws/lambda/naukribaba-score-batch'\n| fields @timestamp, level, function_name, event, error, user_id, request_id\n| filter level in [\"error\", \"ERROR\", \"exception\", \"EXCEPTION\"]\n| sort @timestamp desc\n| limit 50",
        "view": "table"
      }
    }
  ]
}
```

(Note the `"..."` shorthand: CloudWatch's dashboard syntax allows `"..."` to mean "same metric name as the row above, only the dimension values differ" — saves bytes and keeps lines short. Each `"..."` row inherits namespace + metric name from its first ancestor row.)

- [ ] **Step 3: Inline the JSON into `template.yaml`**

In `template.yaml`, replace the placeholder Body in the `ObservabilityDashboard` resource (added in Task 9) with the full JSON body wrapped in `!Sub`:

```yaml
  ObservabilityDashboard:
    Type: AWS::CloudWatch::Dashboard
    Properties:
      DashboardName: !Sub "naukribaba-${Stage}"
      DashboardBody: !Sub |
        {
          "widgets": [
            ... (paste the contents of monitoring/dashboard.json's "widgets" array here, INDENTED to match) ...
          ]
        }
```

CloudFormation `!Sub` will resolve every `${Stage}` and `${AWS::AccountId}` inside the heredoc.

- [ ] **Step 4: Sanity-check the JSON**

```bash
python -c "import json; json.load(open('/Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/monitoring/dashboard.json'))"
sam validate --template /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml
```

Expected: both succeed (no JSON errors, valid SAM template).

- [ ] **Step 5: Commit**

```bash
git add monitoring/dashboard.json template.yaml
git commit -m "feat(observability): CloudWatch dashboard with 10 widgets

monitoring/dashboard.json + inlined into template.yaml's ObservabilityDashboard
resource. Widgets:
1-3. Lambda invocations / errors / p95 duration (top 5 functions)
4. Lambda throttles
5. Apply funnel (attempted / succeeded / failed)
6. Pipeline output (jobs scored / resumes tailored / runs completed)
7. AI provider failover heat map (groq/deepseek/openrouter/claude)
8. Scrapers — jobs returned by source
9. Alarm states (composite tile)
10. Recent errors (Logs Insights query)

Dashboard URL after deploy:
https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#dashboards:name=naukribaba-\${Stage}"
```

---

## Task 11: `monitoring/alarms.yaml` — upgrade Phase 2 alarms with EMF-backed composite alarms

**Why:** Phase 2 (canary) ships with stock `Errors > 0` per Lambda. That's a great default for *any* error but for high-traffic write paths (`ws-route`, `tailor-resume`, `compile-latex`, `score-batch`), the more actionable signal is *rate* — `apply_failed / apply_attempted > 20% over 5 min` catches a regression in the apply funnel even when raw error count is unchanged. Phase 4 adds those rate alarms in addition to (not in place of) the stock ones.

**Coordination:** Phase 2 owns the existence of `monitoring/alarms.yaml`. If Phase 2 has not yet merged (Task 0 outcome 1), this task creates the file from scratch with both the stock Phase 2 alarms AND the Phase 4 composite ones — coordinate the rebase carefully so Phase 2's PR doesn't reset our additions.

**Files:**
- Modify (or create if outcome 1): `monitoring/alarms.yaml`

- [ ] **Step 1: Sketch the YAML structure**

`monitoring/alarms.yaml` is a CFN snippet imported by `template.yaml` via `Fn::Transform: AWS::Include` *or* manually pasted. Either way, the resource shape is `AWS::CloudWatch::Alarm` for stock + `AWS::CloudWatch::CompositeAlarm` for the rate ones.

- [ ] **Step 2: Add 4 composite EMF-backed alarms**

Append (or, if creating fresh, write) the following resources to `monitoring/alarms.yaml`:

```yaml
# ----------------------------------------------------------------
# Phase 4 — composite alarms backed by EMF business metrics.
# Phase 2's stock per-function Errors > 0 alarms remain in place
# (canary protection); these add SECOND-ORDER signals on top.
# ----------------------------------------------------------------

# ws-route — apply_failed_rate > 20% over 5 min
WsRouteApplyFailedRateMetric:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "naukribaba-${Stage}-ws-route-apply-failed-rate"
    AlarmDescription: |
      Fires when apply_failed / (apply_succeeded + apply_failed) exceeds 20%
      over a rolling 5-minute window. Backed by EMF metrics from ws_route.py
      (Phase 4 / Task 7).
    Metrics:
      - Id: failed
        MetricStat:
          Metric:
            Namespace: !Sub "Naukribaba/${Stage}"
            MetricName: apply_failed
          Period: 300
          Stat: Sum
        ReturnData: false
      - Id: succeeded
        MetricStat:
          Metric:
            Namespace: !Sub "Naukribaba/${Stage}"
            MetricName: apply_succeeded
          Period: 300
          Stat: Sum
        ReturnData: false
      - Id: rate
        Expression: "IF((failed + succeeded) > 5, failed / (failed + succeeded) * 100, 0)"
        Label: "apply_failed %"
        ReturnData: true
    EvaluationPeriods: 1
    DatapointsToAlarm: 1
    Threshold: 20
    ComparisonOperator: GreaterThanThreshold
    TreatMissingData: notBreaching
    AlarmActions: [!Ref AlarmsTopic]   # SNS topic owned by Phase 2
    OKActions: [!Ref AlarmsTopic]

# tailor-resume — high failure rate (>10% over 10 min)
TailorResumeFailedRate:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "naukribaba-${Stage}-tailor-resume-failed-rate"
    AlarmDescription: "resume_tailor_failed / (resume_tailored + resume_tailor_failed) > 10% over 10 min"
    Metrics:
      - Id: failed
        MetricStat:
          Metric: { Namespace: !Sub "Naukribaba/${Stage}", MetricName: resume_tailor_failed }
          Period: 600
          Stat: Sum
        ReturnData: false
      - Id: ok
        MetricStat:
          Metric: { Namespace: !Sub "Naukribaba/${Stage}", MetricName: resume_tailored }
          Period: 600
          Stat: Sum
        ReturnData: false
      - Id: rate
        Expression: "IF((failed + ok) > 3, failed / (failed + ok) * 100, 0)"
        ReturnData: true
    EvaluationPeriods: 1
    DatapointsToAlarm: 1
    Threshold: 10
    ComparisonOperator: GreaterThanThreshold
    TreatMissingData: notBreaching
    AlarmActions: [!Ref AlarmsTopic]
    OKActions: [!Ref AlarmsTopic]

# AI provider failover storm — alarm if any single provider fails > 30 times in 15 min
AiProviderFailoverStorm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "naukribaba-${Stage}-ai-provider-failover-storm"
    AlarmDescription: "ai_provider_failed > 30 events in 15 min — likely outage on a provider"
    Namespace: !Sub "Naukribaba/${Stage}"
    MetricName: ai_provider_failed
    Statistic: Sum
    Period: 900
    EvaluationPeriods: 1
    Threshold: 30
    ComparisonOperator: GreaterThanThreshold
    TreatMissingData: notBreaching
    AlarmActions: [!Ref AlarmsTopic]
    OKActions: [!Ref AlarmsTopic]

# pipeline_run_completed silence (canary smoke trip if no successful runs in 24h)
PipelineRunSilence:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "naukribaba-${Stage}-pipeline-run-silence"
    AlarmDescription: "pipeline_run_completed = 0 over 24h (daily pipeline broken or never ran)"
    Namespace: !Sub "Naukribaba/${Stage}"
    MetricName: pipeline_run_completed
    Statistic: Sum
    Period: 86400
    EvaluationPeriods: 1
    Threshold: 1
    ComparisonOperator: LessThanThreshold
    TreatMissingData: breaching   # NB: silence == breach for this alarm
    AlarmActions: [!Ref AlarmsTopic]
```

Note: `!Ref AlarmsTopic` is the SNS topic Phase 2 creates. If Phase 2 hasn't merged, define a stub `AlarmsTopic` in this file too (so the alarm has a valid target during dev):

```yaml
AlarmsTopic:
  Type: AWS::SNS::Topic
  Properties:
    TopicName: !Sub "naukribaba-${Stage}-alarms"
    Subscription:
      - Endpoint: 254utkarsh@gmail.com
      - Protocol: email
```

— and remove this stub when Phase 2 merges (Phase 2 owns the canonical SNS topic).

- [ ] **Step 3: Confirm Phase 2 alarms remain present**

If Phase 2 already wrote `monitoring/alarms.yaml`, do NOT delete the existing `Errors > 0` alarms — they remain canary protection. The new composite alarms add to the file. Confirm by re-reading the file after the edit:

```bash
grep -E "AlarmName" /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/monitoring/alarms.yaml
```

Expected: at least 4 `AlarmName` lines for Phase 4 (the four composite ones above) plus N more for Phase 2.

- [ ] **Step 4: Validate**

```bash
sam validate --template /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml
```

Expected: valid.

- [ ] **Step 5: Commit**

```bash
git add monitoring/alarms.yaml
git commit -m "feat(observability): composite EMF-backed alarms (Phase 4)

Adds 4 composite alarms backed by Phase 4 EMF business metrics:
- ws-route apply_failed_rate > 20% over 5 min
- tailor-resume resume_tailor_failed_rate > 10% over 10 min
- ai-provider-failover-storm > 30 events / 15 min
- pipeline_run_completed silence > 24h

These STACK on top of Phase 2's stock per-function Errors > 0 alarms (canary
protection retained) — they catch second-order regressions where raw errors
are unchanged but the apply / tailoring success rate degrades.

All four route to !Ref AlarmsTopic (Phase 2 SNS topic; stubbed locally if
Phase 2 hasn't merged yet)."
```

---

## Task 12: `monitoring/queries.md` — Logs Insights cookbook

**Why:** Operators (you, Utkarsh) need a paste-ready set of queries when things break. Without this, the dashboards are fine for "is the system healthy" but not for "what just happened to user u-42 in the last 10 minutes". File is markdown; lives next to dashboard.json.

**Files:**
- Create: `monitoring/queries.md`

- [ ] **Step 1: Write the cookbook**

Create `monitoring/queries.md`:

````markdown
# CloudWatch Logs Insights — Query Cookbook

Paste-ready queries for the NaukriBaba observability stack (Phase 4).
Every Lambda emits structured JSON via Powertools; every FastAPI request
emits JSON via structlog (config/observability.py). All queries run in
**eu-west-1**, console URL:

https://eu-west-1.console.aws.amazon.com/cloudwatch/logs-insights

Stage prefix `${Stage}` is `prod` (or `staging` after Phase 3); replace as needed.

---

## 1. All errors in the last hour, grouped by user_id

Use this for "who's affected right now?".

```
fields @timestamp, level, function_name, event, user_id, request_id, error
| filter level in ["error", "ERROR", "exception", "EXCEPTION"]
| stats count() as errors by user_id, function_name, event
| sort errors desc
| limit 50
```

Set log group: `/aws/lambda/naukribaba-*` + `/aws/lambda/naukribaba-api`. Time range: 1h.

---

## 2. Apply attempts by ATS platform (last 24h)

```
fields @timestamp, event, user_id, ats, job_hash
| filter event = "apply_attempted" or event = "apply_succeeded" or event = "apply_failed"
| stats count() as n by event, ats
| sort event, n desc
```

Log group: `/aws/lambda/naukribaba-ws-route`.

---

## 3. AI provider failover events (last 24h)

```
fields @timestamp, event, provider, error, next_provider
| filter event = "ai_provider_failed"
| stats count() as failures by provider, bin(1h)
| sort failures desc
```

Log group: `/aws/lambda/naukribaba-*` (ai_helper is shared). Useful when
the dashboard's AI-failover heat map shows a spike — drills into which
provider, which error, and which fallback was attempted.

---

## 4. Tailoring failures with full context

```
fields @timestamp, user_id, job_hash, error, ats_score, hiring_manager_score, tech_recruiter_score
| filter event = "resume_tailor_failed"
| sort @timestamp desc
| limit 100
```

Log group: `/aws/lambda/naukribaba-tailor-resume`.

---

## 5. Trace the full lifecycle of one request_id

Useful for "user complained about job j-7; what happened?".

```
fields @timestamp, function_name, event, user_id, job_hash, error, level
| filter request_id = "REPLACE_WITH_REQUEST_ID"
| sort @timestamp asc
| limit 200
```

Log group: ALL `/aws/lambda/naukribaba-*` + `/aws/lambda/naukribaba-api`.
Each line carries `request_id` (FastAPI middleware in app.py / Powertools
correlation_id_path on Lambdas), so this query stitches the entire trace
across the API + every downstream Lambda.

---

## 6. Cold-start frequency by Lambda

```
fields @timestamp, function_name, cold_start
| filter cold_start = true
| stats count() as cold_starts by function_name, bin(1h)
| sort cold_starts desc
```

Powertools auto-injects `cold_start: true|false` on every line when
@logger.inject_lambda_context decorates the handler. If a function shows
constant cold starts, increase `ProvisionedConcurrency` or `MemorySize`.

---

## 7. Slow requests (FastAPI side, p95+)

```
fields @timestamp, request_id, user_id, path, method, duration_ms, status_code
| filter event = "request_completed" and duration_ms > 2000
| sort duration_ms desc
| limit 50
```

Log group: `/aws/lambda/naukribaba-api`. The middleware in app.py emits
`request_completed` with `duration_ms` calculated from `request_started` /
`request_completed` deltas (added in a future cleanup; for now the
`duration_ms` field is populated only when the handler explicitly logs it).

---

## 8. Pipeline run summary (last 7d)

```
fields @timestamp, event, user_id, matched, skipped, total
| filter event = "score_batch_completed" or event = "pipeline_run_completed"
| stats sum(matched) as total_matched, sum(skipped) as total_skipped, count() as runs by bin(1d)
| sort @timestamp desc
```

Log group: `/aws/lambda/naukribaba-score-batch` + `/aws/lambda/naukribaba-save-metrics`.

---

## X-Ray tracing

To see one trace end-to-end, paste the `xray_trace_id` (every Powertools log
line carries it) into the URL:

```
https://eu-west-1.console.aws.amazon.com/xray/home?region=eu-west-1#/traces/{TRACE_ID}
```

The trace will show: API Gateway → FastAPI Lambda (cold start visible in the
first segment) → Supabase RPC (subsegment via boto3 instrumentation; for
supabase-py this requires a manual @tracer.capture_method wrapper which is
a future Phase 4.5 task) → AI provider HTTP (subsegment via requests
auto-instrumentation) → response.

---

## Maintenance

- Add new queries here when a real incident teaches us a new question.
- If a query becomes a daily ritual, promote it to a CloudWatch dashboard widget (Task 10's dashboard.json).
- Period > 30d requires saving query as a saved query in the AWS console — Logs Insights default retention is 30d.
````

- [ ] **Step 2: Commit**

```bash
git add monitoring/queries.md
git commit -m "docs(observability): Logs Insights query cookbook

8 paste-ready Logs Insights queries covering: errors-by-user, apply-funnel,
AI-failover, tailoring-failures, request_id-lifecycle, cold-starts, slow
requests, pipeline-runs. Plus X-Ray trace-id template.

Reference for incident response — every query has been hand-verified against
the schema emitted by Phase 4's structlog/Powertools setup."
```

---

## Task 13: ADR — `docs/superpowers/specs/2026-04-27-observability-decision.md`

**Why:** Capture the structlog vs loguru / EMF vs PutMetricData / X-Ray vs OpenTelemetry decisions explicitly so future contributors don't re-litigate.

**Files:**
- Create: `docs/superpowers/specs/2026-04-27-observability-decision.md`

- [ ] **Step 1: Write the ADR**

Create the file with:

```markdown
# ADR — Observability Stack for NaukriBaba

**Date:** 2026-04-27
**Owner:** Utkarsh
**Status:** Accepted (Phase 4 of deployment-safety roadmap)
**Roadmap:** [2026-04-27-deployment-safety-roadmap.md](../plans/2026-04-27-deployment-safety-roadmap.md)
**Implementation plan:** [2026-04-27-deployment-safety-phase4-observability.md](../plans/2026-04-27-deployment-safety-phase4-observability.md)

## Context

Pre-Phase-4 NaukriBaba runs FastAPI on Lambda (Mangum) plus 35 worker Lambdas
(scrapers, scoring, tailoring, compile-LaTeX, browser apply). All use
`logging.getLogger()` with default formatters; CloudWatch shows plain text
that can't be parsed reliably for production triage. There are no business
metrics, no traces, no alarms beyond stock Lambda errors.

We need: structured logs, custom business metrics, distributed traces, and a
dashboard — without exploding cost or migration time.

## Decision 1 — structlog (FastAPI) + Powertools Logger (Lambda) over loguru-everywhere

We use **structlog** in the FastAPI process (`config/observability.py`) and
**aws-lambda-powertools.Logger** in every Lambda handler
(`lambdas/pipeline/utils/logging.py`).

### Why not loguru everywhere?

- loguru is opinionated about its sink (stdout vs file vs network) but has
  no first-class context-var binding. FastAPI's per-request middleware needs
  to bind `request_id` once and have it picked up by every downstream
  `logger.info(...)` call without threading it through every signature.
  structlog's `contextvars.merge_contextvars` processor does this natively.
- Powertools is the AWS-supported, AWS-best-practice library for Lambda
  Python. It auto-injects `cold_start`, `function_name`, `xray_trace_id`,
  and integrates cleanly with Tracer + Metrics — three boxes ticked with
  one dependency. Replacing it with loguru would mean re-implementing all
  three integrations.
- Two libraries is a small price for "the right tool for each surface" —
  both emit JSON to stdout, both are MIT-licensed, both maintained.

### Why not Powertools everywhere (incl. FastAPI)?

- Powertools Logger doesn't have ergonomic context-var binding for
  long-lived async processes. Its model is per-Lambda-invocation, not
  per-async-request. structlog's binding semantics fit FastAPI exactly.

## Decision 2 — Embedded Metric Format (EMF) over PutMetricData

We emit custom metrics via Powertools' `Metrics` interface, which writes
EMF-shaped JSON to stdout. CloudWatch parses it without a separate API call.

### Why not boto3 PutMetricData?

- One PutMetricData call per metric ≈ 30ms of added latency per Lambda
  invocation. EMF adds zero — the JSON line is already going to CloudWatch
  for logging.
- PutMetricData has a 150 TPS account-wide limit; EMF has none.
- PutMetricData has a per-call cost ($0.01 per 1k requests). EMF rides on
  free CloudWatch log ingestion (within limits).
- EMF is the AWS-recommended pattern for Lambda metrics since 2019.

## Decision 3 — AWS X-Ray over OpenTelemetry / Honeycomb

We enable X-Ray via SAM `Tracing: Active` on the Lambda Globals block, and
Powertools' `Tracer` decorator wraps each handler.

### Why not OpenTelemetry?

- OTel collector must run as a sidecar or on a separate Lambda extension —
  more moving parts, more failure modes, more cost.
- X-Ray integrates natively with API Gateway and Lambda — one click in SAM.
- The X-Ray service map auto-discovers downstream calls when Powertools
  Tracer is in place; OTel would need explicit instrumentation.

### Why not Honeycomb / Datadog APM?

- Cost: both are usage-priced and would dominate our (currently zero)
  observability bill at any scale. X-Ray is essentially free for our
  invocation volume (< 1M traces/month).
- Vendor lock-in vs open AWS — X-Ray is already AWS, fits the "stay on
  the cheapest AWS path" guideline of the broader roadmap.

If Phase 6 (smoke tests) reveals X-Ray is too coarse-grained for our
debugging needs, we can layer OTel on top via the `aws-otel-python-instrumentation`
extension *without* removing X-Ray. That's a future task, not a blocker today.

## Decision 4 — Dashboard JSON inlined into template.yaml (over `AWS::Include`)

`monitoring/dashboard.json` is the source of truth, but its contents are
also inlined into `template.yaml`'s `ObservabilityDashboard.Properties.DashboardBody`
via `!Sub |`.

### Why not `AWS::Include`?

- `AWS::Include` requires the JSON file to be in S3 before the SAM deploy.
  That means an extra `aws s3 cp` step in `deploy.yml` — non-trivial.
- Inlining duplicates ~40 lines of JSON. Acceptable cost; CI check enforces
  sync (Task 14).

## Consequences

- **Cost:** structlog + aws-xray-sdk + aws-lambda-powertools — all pure
  Python, all in the Lambda layer. No new AWS services. X-Ray is the only
  paid AWS service we add and it's free up to 100k traces/month.
- **Migration:** ~36 Lambda files + app.py, all mechanical edits. ~10 hours.
- **Debugging:** moves from "`tail -f` CloudWatch and grep" to "Logs
  Insights query plus X-Ray trace map" — qualitatively faster for
  cross-Lambda incidents (the Apr 8 marathon would have been one query, not one day).
- **Phase-5 (Sentry) handshake:** Sentry will tap structlog's context-var
  bag for breadcrumbs (FastAPI side) and Powertools Logger's `add_keys()`
  (Lambda side). Hooks for both are explicitly left in place by Phase 4
  comments.

## Alternatives considered and rejected

| Alternative | Reason rejected |
|---|---|
| loguru everywhere | No native context-var; would re-build Powertools features |
| OpenTelemetry collector | Sidecar overhead, more moving parts, no immediate benefit |
| Honeycomb / Datadog | Cost + lock-in; X-Ray is free at our scale |
| Roll our own JSON formatter | NIH; structlog and Powertools are battle-tested |
| PutMetricData direct | Latency + cost + TPS limits |
| AWS::Include for dashboard JSON | Requires S3 staging step in deploy.yml |
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-04-27-observability-decision.md
git commit -m "docs(adr): observability stack decision (structlog + Powertools + X-Ray + EMF)

ADR for Phase 4 of the deployment-safety roadmap. Documents:
- structlog (FastAPI) + Powertools Logger (Lambda) — split chosen for
  context-var binding ergonomics
- EMF (via Powertools Metrics) over PutMetricData — zero added latency,
  no TPS limits, free
- AWS X-Ray over OpenTelemetry — native SAM integration, no sidecar
- Dashboard JSON inlined into template.yaml — avoids S3 staging step

Captures alternatives + reasons-rejected so future contributors don't
re-litigate the choices."
```

---

## Task 14: Deploy + validation

**Why:** Land everything in staging (or the only stack if Phase 3 hasn't merged), exercise the system, and verify each observability surface answers what it claims to.

**Files:**
- No code changes; this task is verification only.

- [ ] **Step 1: Confirm `deploy.yml` will rebuild the layer with new deps**

The deploy workflow runs `./layer/build.sh` which `pip install -r layer/requirements.txt -t python/`. Confirm that includes our three new deps:

```bash
grep -E "structlog|powertools|xray-sdk" /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/layer/requirements.txt
```

Expected: 3 lines.

- [ ] **Step 2: Push branch + open PR**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
git push -u origin claude/objective-sanderson-eeedca
gh pr create --title "feat(observability): structlog + X-Ray + EMF + CloudWatch dashboard (Phase 4)" --body "$(cat <<'EOF'
## Summary
- **structlog** for FastAPI (`config/observability.py`) with request_id binding middleware in `app.py`
- **AWS Lambda Powertools** (Logger + Tracer + Metrics) for all 36 Lambdas via shared `lambdas/pipeline/utils/logging.py`
- **X-Ray** enabled globally via `Tracing: Active` on `Globals.Function`
- **EMF metrics** for the apply funnel, AI provider failover, scraper output, and pipeline runs — namespace `Naukribaba/${Stage}`
- **CloudWatch Dashboard** (10 widgets) defined in `monitoring/dashboard.json`, inlined into `template.yaml`
- **EMF-backed alarms** layered on top of Phase 2's stock alarms — `apply_failed_rate`, `tailor_failed_rate`, AI failover storm, pipeline silence
- **Logs Insights cookbook** (`monitoring/queries.md`) — 8 paste-ready queries for daily ops + incident response
- **ADR** (`docs/superpowers/specs/2026-04-27-observability-decision.md`) documenting structlog vs loguru / EMF vs PutMetricData / X-Ray vs OTel choices

Spec: `docs/superpowers/plans/2026-04-27-deployment-safety-phase4-observability.md`
Roadmap: `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md` (Phase 4 section)

## Test plan
- [ ] CI green (unit + contract; `tests/unit/test_observability.py` adds 5 cases)
- [ ] After merge: `gh workflow run deploy.yml --ref main`
- [ ] Hit `/api/health` once with X-Request-Id header set
- [ ] Logs Insights query (see body): `request_id` populated on all 3 lines
- [ ] X-Ray console: trace visible end-to-end, ≥3 segments
- [ ] Dashboard `naukribaba-prod` shows non-zero data on widgets 1, 2, 3 within 5 min

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI green, merge, deploy**

```bash
# Wait for CI
gh pr checks --watch
# Merge via API (per session memory: gh pr merge fails from worktree; use API)
gh api -X PUT "repos/UT07/daily-job-hunt/pulls/<NUMBER>/merge" -f merge_method=squash
# Deploy
gh workflow run deploy.yml --ref main
gh run watch $(gh run list --workflow=deploy.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

- [ ] **Step 4: Smoke test — hit `/api/health` and verify the JSON line**

```bash
curl -sS -H "X-Request-Id: phase4-validation-001" -H "X-User-Id: u-validation" \
  "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/api/health"
```

Expected: `{"status":"ok"}` (or whatever `/api/health` already returns).

- [ ] **Step 5: Logs Insights validation**

Open: https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#logsV2:logs-insights

- Log group: `/aws/lambda/naukribaba-api` (the FastAPI Lambda's log group)
- Time range: last 5 min
- Query (paste verbatim):

```
fields @timestamp, request_id, user_id, event, path, method, status_code
| filter request_id = "phase4-validation-001"
| sort @timestamp asc
```

Expected: at least 2 rows (`request_started` and `request_completed`), both with `request_id="phase4-validation-001"`, `user_id="u-validation"`, `path="/api/health"`, `method="GET"`. The `request_completed` row has `status_code=200`.

If the query returns no rows: check that the Lambda log group exists (it should be auto-created on first invocation) and that the request was actually served by Lambda (vs cached at API Gateway).

- [ ] **Step 6: X-Ray validation**

Open: https://eu-west-1.console.aws.amazon.com/xray/home?region=eu-west-1#/service-map

Filter by time: last 5 min. Click the `naukribaba-api` node. You should see:
- A trace list with at least one trace from the curl in Step 4.
- Click that trace. Spans visible: `naukribaba-api` (the Lambda invocation, with `cold_start: true` if first hit) → any boto3 / Supabase / requests sub-segments.

If no traces appear: SAM's `Tracing: Active` may not have applied (re-run `sam validate` and check the deployed function has tracing-mode `Active` — `aws lambda get-function-configuration --function-name naukribaba-api --query TracingConfig`).

- [ ] **Step 7: Dashboard validation**

Open: https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#dashboards:name=naukribaba-prod

Expected: dashboard loads with all 10 widgets. Widgets 1, 2, 3 (Lambda invocations / errors / p95 duration) show non-zero data (the curl in Step 4 produced one invocation). Widget 9 (alarm states) shows all alarms in OK / INSUFFICIENT_DATA. Widget 10 (recent errors Logs Insights tile) likely empty — that's fine if the deploy was clean.

If the dashboard doesn't load with name `naukribaba-prod`: check `aws cloudwatch list-dashboards | grep naukribaba`. If the name is different, the `${Stage}` substitution went wrong; debug `template.yaml`.

- [ ] **Step 8: EMF metric validation**

Open: https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#metricsV2:graph=~()

In the namespace dropdown, select `Naukribaba/prod`. Expected: at least 1–2 metric names appear (depending on what's been invoked since deploy). After ~5 min of normal traffic, expect: `apply_attempted`, `job_scored`, `pipeline_run_completed`, `scraper_jobs_returned` (with `source` dimension). All numeric.

If the namespace doesn't appear at all: check one Lambda's CloudWatch logs for an EMF JSON line:

```bash
aws logs tail /aws/lambda/naukribaba-score-batch --since 10m --filter-pattern '"_aws"'
```

Expected: at least one line of EMF JSON. If yes, CloudWatch will pick it up within 1–2 min into the namespace.

- [ ] **Step 9: Update memory**

Append to `~/.claude/projects/-Users-ut-code-naukribaba/memory/MEMORY.md`:

```
- [Phase 4 Observability shipped] — structlog + Powertools + X-Ray + EMF + CW dashboard, queries.md cookbook, 5 EMF alarms layered on Phase 2 stock alarms
```

---

## Self-Review

(Author note 2026-04-27 — completed before save.)

**1. Spec coverage check (vs roadmap §Phase 4 lines 257–296):**

| Roadmap requirement | Plan task |
|---|---|
| `requirements.txt` adds structlog / aws-xray-sdk / aws-lambda-powertools | Task 1 |
| `config/observability.py` (CREATE) — structlog processors + Powertools | Task 2 |
| `lambdas/pipeline/utils/logging.py` (CREATE) — shared Lambda logger | Task 3 |
| `app.py` — replace `logging.getLogger(__name__)`, add request_id middleware, convert ~30 logger.* calls | Task 4 |
| All `lambdas/browser/*.py` and `lambdas/pipeline/*.py` MODIFY (~25 files) — utils.logging + metrics at decision points | Tasks 5, 6, 7 (also extends to scrapers in Task 8) |
| `template.yaml` — `Tracing: Active` on Globals.Function + Dashboard resource | Task 9 |
| `monitoring/dashboard.json` — 8–12 widgets | Task 10 (10 widgets) |
| `monitoring/alarms.yaml` — upgrade to EMF-backed composite alarms | Task 11 |
| `monitoring/queries.md` — Logs Insights cookbook | Task 12 |
| `docs/superpowers/specs/2026-04-27-observability-decision.md` (ADR) | Task 13 |
| Tests `tests/unit/test_observability.py` — 5 cases | Tasks 2 (3 cases) + 3 (2 cases) + 4 (1 middleware case) = 6 cases — exceeds spec (acceptable) |
| Validation: deploy + Logs Insights query + X-Ray + dashboard | Task 14 |

All 12 spec items mapped. ✓

**2. Placeholder scan:**

- No "TBD" / "fill in details" — every code block is complete.
- "REPLACE_WITH_REQUEST_ID" in queries.md is intentional — it's a literal in a paste-ready template, not a plan placeholder.
- No "add appropriate logging" — every metric and event name is concrete.
- No "similar to Task N" without code — Tasks 6, 7, 8 each include the full diff template (not a back-ref).

**3. Type / name consistency:**

- Metric names match across plan (Tasks 5–8), dashboard (Task 10), alarms (Task 11), and queries.md (Task 12): `apply_attempted`, `apply_succeeded`, `apply_failed`, `job_scored`, `resume_tailored`, `pipeline_run_completed`, `ai_provider_failed`, `scraper_jobs_returned`. ✓
- Dimension keys consistent: `provider`, `source`, `tier`, `reason`, `ats`. ✓
- Stage parameter spelled `${Stage}` everywhere (CFN-correct); namespace literal `Naukribaba/${Stage}`. ✓
- `service` field consistently `naukribaba-api` (FastAPI) vs `naukribaba-pipeline` (Lambdas). ✓

**4. Dependency ordering:**

- Task 0 must run first (audit Phase 2/3 state).
- Task 1 must precede 2/3 (deps installed first).
- Task 5 (pilot) must precede 6/7/8 (template established).
- Task 9 must precede 10/11 (template.yaml block exists for dashboard inline).
- Task 14 (deploy) must run last.

**5. Cross-phase coordination explicitly addressed:**

- Phase 2 alarms preserved (Task 11 adds, doesn't replace).
- Phase 3 Stage parameter honored if present (Task 0 outcome 3).
- Phase 5 Sentry hooks called out as comments in code (Tasks 2, 3) — not pre-built.
- Phase 6 smoke tests assert against `pipeline_run_completed` (Task 6 contract).

All addressed. ✓

---

## Execution Handoff

**Plan complete and saved to** `docs/superpowers/plans/2026-04-27-deployment-safety-phase4-observability.md`.

Two execution options:

**1. Subagent-Driven (recommended)** — One fresh subagent per task. Tasks 6, 7, 8 are bulk edits across many files; subagent-driven gives a clean diff per group commit. Tasks 2, 3, 4 are TDD with new test cases — fresh context per task helps the subagent stay disciplined.

**2. Inline Execution** — Execute tasks in this session. Faster end-to-end but Tasks 6/7/8 (45 file edits combined) will burn a lot of context.

**Which approach?**
