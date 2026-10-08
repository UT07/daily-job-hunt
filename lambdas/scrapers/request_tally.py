"""Count a scraper's search requests so "0 jobs" can say WHY it is zero.

Until 2026-10-08 every scraper returned `{"count": 0}` with no `error` when
every request it made failed — a proxy outage, a revoked key, LinkedIn's 999
block. save_metrics stores `result.get("error")` per scraper per run, so that
row was indistinguishable from a genuinely quiet market. CLAUDE.md rule 2: a
status that reads the same for "did the work" and "did nothing" is not one.

The scrapers RETURN the error rather than raising it. Raising would hit the
branch's Retry (two more rounds of paid proxy requests that fail the same
way) and then a Catch that replaces the specific cause with a generic
"scraper_failed". Returning keeps the parallel scrape state alive and puts
the real reason in pipeline_metrics.error_message.

Only the SEARCH requests are tallied. Detail-page fetches are enrichment: a
failed one degrades a description to a snippet but still yields a job.
"""
from __future__ import annotations

from collections import Counter

# 401/403 = refused credentials or IP, 407 = proxy auth, 999 = LinkedIn's
# bot block. A run where these are the only answers is an access problem, not
# an empty market, and is labelled so.
AUTH_STATUSES = frozenset({401, 403, 407, 999})


class RequestTally:
    def __init__(self, source: str):
        self.source = source
        self.succeeded = 0
        self.failures: Counter = Counter()
        self.auth_failures = 0

    @property
    def attempted(self) -> int:
        return self.succeeded + sum(self.failures.values())

    def ok(self) -> None:
        self.succeeded += 1

    def http_failure(self, status: int) -> None:
        self.failures[f"HTTP {status}"] += 1
        if status in AUTH_STATUSES:
            self.auth_failures += 1

    def blocked(self, why: str) -> None:
        """A 200 that is really a refusal (a login wall, a captcha page)."""
        self.failures[why] += 1
        self.auth_failures += 1

    def exception(self, exc: BaseException) -> None:
        self.failures[type(exc).__name__] += 1

    def record_status(self, status: int) -> bool:
        """Record a response by status; True if it is usable."""
        if status == 200:
            self.ok()
            return True
        self.http_failure(status)
        return False

    @property
    def all_failed(self) -> bool:
        return self.attempted > 0 and self.succeeded == 0

    def error(self) -> str | None:
        """None unless every search request failed."""
        if not self.all_failed:
            return None
        detail = ", ".join(f"{k} x{n}" for k, n in sorted(self.failures.items()))
        kind = "auth_failed" if self.auth_failures == self.attempted else "all_requests_failed"
        return f"{kind}: {self.attempted}/{self.attempted} {self.source} search requests failed ({detail})"

    def annotate(self, result: dict) -> dict:
        """Add request counts, and `error` when every request failed."""
        result["requests"] = self.attempted
        result["failed_requests"] = self.attempted - self.succeeded
        err = self.error()
        if err:
            result["error"] = err
        return result
