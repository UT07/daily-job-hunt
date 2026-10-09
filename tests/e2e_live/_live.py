"""Shared machinery for the LIVE end-to-end suite.

Everything here talks to the real deployed system. Nothing is stubbed except
PostHog analytics (so a test account does not pollute product analytics).

Configuration is read ONLY from explicit ``E2E_LIVE_*`` environment variables
(CLAUDE.md verification rule 8): this module never imports ``app.py``, never
reads ``.env`` and never falls back to a default URL. ``scripts/run_e2e_live.sh``
is the one place that turns the repo's config files into those variables.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

import httpx

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
ARTIFACTS = HERE / "artifacts"

# API Gateway kills a synchronous request at ~29-30s. Anything slower than this
# passed by luck of the day's latency and is reported as a risk even when green.
SLOW_SYNC_CALL_S = 25.0

REQUIRED_ENV = {
    "E2E_LIVE_SITE_URL": "deployed frontend origin, e.g. https://naukribaba.netlify.app",
    "E2E_LIVE_API_URL": "deployed API base, e.g. https://<id>.execute-api.<region>.amazonaws.com/prod",
    "E2E_LIVE_SUPABASE_URL": "Supabase project URL the frontend is built against",
    "E2E_LIVE_SUPABASE_ANON_KEY": "Supabase anon key (what the browser uses)",
    "E2E_LIVE_SUPABASE_SERVICE_KEY": "Supabase service-role key (test-account setup + cleanup only)",
    "E2E_LIVE_S3_BUCKET": "S3 bucket holding users/<uid>/ artifacts (cleanup only)",
}

# Opt-in flags. Each gates something that costs money, sends email or cannot be
# automated. Default OFF; a test behind one is reported SKIPPED(opt-in).
OPT_IN = {
    "E2E_LIVE_RUN_PIPELINE": "Run Pipeline: daily Step Functions, paid scrapers, notification email",
    "E2E_LIVE_FIND_CONTACTS": "Find Contacts: paid Apify actor",
    "E2E_LIVE_REGENERATE": "Regenerate + Restore: a second full single-job pipeline run",
    "E2E_LIVE_COVER_LETTER": "Add Job > Cover Letter: a second full single-job pipeline run",
}


class LiveConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveConfig:
    site_url: str
    api_url: str
    supabase_url: str
    anon_key: str
    service_key: str
    s3_bucket: str
    headed: bool
    opt_in: dict

    @classmethod
    def from_env(cls) -> "LiveConfig":
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k, "").strip()]
        if missing:
            lines = "\n".join(f"  {k}: {REQUIRED_ENV[k]}" for k in missing)
            raise LiveConfigError(
                "tests/e2e_live refuses to run without explicit configuration.\n"
                f"Missing:\n{lines}\n"
                "Run it through scripts/run_e2e_live.sh, which derives these from "
                ".env, web/.env.production and app.py (CORS origin, S3 default)."
            )
        env = os.environ
        return cls(
            site_url=env["E2E_LIVE_SITE_URL"].rstrip("/"),
            api_url=env["E2E_LIVE_API_URL"].rstrip("/"),
            supabase_url=env["E2E_LIVE_SUPABASE_URL"].rstrip("/"),
            anon_key=env["E2E_LIVE_SUPABASE_ANON_KEY"],
            service_key=env["E2E_LIVE_SUPABASE_SERVICE_KEY"],
            s3_bucket=env["E2E_LIVE_S3_BUCKET"],
            headed=env.get("E2E_LIVE_HEADED") == "1",
            opt_in={k: env.get(k) == "1" for k in OPT_IN},
        )


# ---------------------------------------------------------------------------
# Supabase admin (service key): account setup, persisted-state checks, cleanup
# ---------------------------------------------------------------------------


class Admin:
    def __init__(self, cfg: LiveConfig):
        self.base = cfg.supabase_url
        self.h = {"apikey": cfg.service_key, "Authorization": f"Bearer {cfg.service_key}"}
        self.http = httpx.Client(timeout=60)

    # auth
    def create_user(self, email: str, password: str) -> str:
        r = self.http.post(
            f"{self.base}/auth/v1/admin/users",
            headers=self.h,
            json={"email": email, "password": password, "email_confirm": True},
        )
        if r.status_code >= 300:
            raise RuntimeError(f"admin create_user -> {r.status_code}: {r.text[:300]}")
        return r.json()["id"]

    def auth_user_status(self, uid: str) -> int:
        return self.http.get(f"{self.base}/auth/v1/admin/users/{uid}", headers=self.h).status_code

    def delete_auth_user(self, uid: str) -> int:
        return self.http.delete(f"{self.base}/auth/v1/admin/users/{uid}", headers=self.h).status_code

    def list_auth_users(self) -> list[dict]:
        out, page = [], 1
        while True:
            r = self.http.get(
                f"{self.base}/auth/v1/admin/users",
                headers=self.h,
                params={"page": page, "per_page": 200},
            )
            r.raise_for_status()
            users = r.json().get("users", [])
            out.extend(users)
            if len(users) < 200:
                return out
            page += 1

    def recovery_link(self, email: str, redirect_to: str) -> str:
        r = self.http.post(
            f"{self.base}/auth/v1/admin/generate_link",
            headers=self.h,
            json={"type": "recovery", "email": email, "redirect_to": redirect_to},
        )
        if r.status_code >= 300:
            raise RuntimeError(f"admin generate_link -> {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body.get("action_link") or body["properties"]["action_link"]

    # rest
    def select(self, table: str, params: dict) -> list[dict]:
        r = self.http.get(f"{self.base}/rest/v1/{table}", headers=self.h, params=params)
        if r.status_code >= 300:
            raise RuntimeError(f"select {table} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def count(self, table: str, params: dict) -> int:
        r = self.http.get(
            f"{self.base}/rest/v1/{table}",
            headers={**self.h, "Prefer": "count=exact", "Range": "0-0"},
            params={"select": "*", **params},
        )
        if r.status_code >= 300 and r.status_code != 416:
            raise RuntimeError(f"count {table} -> {r.status_code}: {r.text[:300]}")
        total = r.headers.get("content-range", "*/0").split("/")[-1]
        return int(total) if total.isdigit() else 0

    def insert(self, table: str, row: dict) -> list[dict]:
        r = self.http.post(
            f"{self.base}/rest/v1/{table}",
            headers={**self.h, "Prefer": "return=representation"},
            json=row,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"insert {table} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def delete(self, table: str, params: dict) -> httpx.Response:
        return self.http.delete(f"{self.base}/rest/v1/{table}", headers=self.h, params=params)

    def tables_with_column(self, column: str) -> list[str]:
        """Every exposed table that has `column`, read from PostgREST's OpenAPI.

        Discovered rather than hard-coded so a table added after this suite was
        written is still cleaned (CLAUDE.md rule 10: a guard nobody wired up to
        the new table is not a guard)."""
        r = self.http.get(f"{self.base}/rest/v1/", headers=self.h)
        r.raise_for_status()
        defs = r.json().get("definitions", {})
        return sorted(t for t, d in defs.items() if column in (d.get("properties") or {}))


# ---------------------------------------------------------------------------
# The test user's own view of the API (what the browser would see)
# ---------------------------------------------------------------------------


class UserApi:
    def __init__(self, cfg: LiveConfig, email: str, password_fn: Callable[[], str] | None = None):
        self.cfg, self.email, self.password_fn = cfg, email, password_fn
        self.token: str | None = None
        self.http = httpx.Client(timeout=120)

    def login(self, password: str) -> httpx.Response:
        r = self.http.post(
            f"{self.cfg.supabase_url}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": self.cfg.anon_key},
            json={"email": self.email, "password": password},
        )
        if r.status_code == 200:
            self.token = r.json()["access_token"]
        return r

    def get(self, path: str, **params) -> httpx.Response:
        assert self.token, "UserApi.get before a successful login"

        def once():
            return self.http.get(
                f"{self.cfg.api_url}{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params or None,
            )

        r = once()
        if r.status_code == 401 and self.password_fn:
            # The token expired or the password was rotated by a test; the
            # current password is in journey state.
            if self.login(self.password_fn()).status_code == 200:
                r = once()
        return r

    def json(self, path: str, **params):
        r = self.get(path, **params)
        assert r.status_code == 200, f"GET {path} -> {r.status_code}: {r.text[:300]}"
        return r.json()


# ---------------------------------------------------------------------------
# Recording: per-interaction outcome + latency, slow calls, console errors
# ---------------------------------------------------------------------------


def _short(exc: BaseException, limit: int = 600) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


@dataclass
class Recorder:
    steps: list = field(default_factory=list)
    slow_calls: list = field(default_factory=list)
    console_errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    cleanup_lines: list = field(default_factory=list)
    current_test: str = ""

    @contextmanager
    def step(self, name: str) -> Iterator[dict]:
        import pytest

        entry = {"test": self.current_test, "interaction": name, "outcome": "PASS",
                 "latency_s": None, "evidence": ""}
        self.steps.append(entry)
        t0 = time.monotonic()
        try:
            yield entry
        except pytest.skip.Exception as e:
            entry["outcome"] = "SKIPPED"
            entry["evidence"] = str(e)[:400]
            raise
        except BaseException as e:  # noqa: BLE001 - recorded then re-raised
            entry["outcome"] = "FAIL"
            entry["evidence"] = _short(e)
            raise
        finally:
            entry["latency_s"] = round(time.monotonic() - t0, 2)

    def add(self, name: str, outcome: str, evidence: str = "", latency_s=None) -> None:
        self.steps.append({"test": self.current_test, "interaction": name, "outcome": outcome,
                           "latency_s": latency_s, "evidence": evidence[:600]})

    def note(self, text: str) -> None:
        self.notes.append(f"[{self.current_test}] {text}")


# ---------------------------------------------------------------------------
# Network + console monitors, attached to every page
# ---------------------------------------------------------------------------


class Net:
    """Records every request to the API and to Supabase, with status + latency.

    A test fails at teardown on any 4xx/5xx or transport failure it did not
    declare with `allow(...)` — a page that quietly rendered its error state is
    a failure, never a pass."""

    def __init__(self, page, cfg: LiveConfig, rec: Recorder):
        self.cfg, self.rec = cfg, rec
        self.hosts = (cfg.api_url, cfg.supabase_url)
        self.calls: list[dict] = []
        self.allowed: list[tuple[set, re.Pattern, str]] = []
        self._start: dict = {}
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_failed)

    def _watched(self, url: str) -> bool:
        return url.startswith(self.hosts)

    def _label(self, url: str) -> str:
        base = self.cfg.api_url if url.startswith(self.cfg.api_url) else self.cfg.supabase_url
        tag = "API" if base == self.cfg.api_url else "SUPABASE"
        rest = url[len(base):]
        return f"{tag} {rest.split('?')[0]}"

    def _on_request(self, req) -> None:
        if self._watched(req.url):
            self._start[req] = time.monotonic()

    def _record(self, req, status, failure=None) -> None:
        t0 = self._start.pop(req, None)
        dur = round(time.monotonic() - t0, 2) if t0 else None
        call = {"method": req.method, "url": req.url, "label": self._label(req.url),
                "status": status, "failure": failure, "duration_s": dur,
                "test": self.rec.current_test}
        self.calls.append(call)
        if dur is not None and dur > SLOW_SYNC_CALL_S and req.method != "OPTIONS":
            self.rec.slow_calls.append(call)

    def _on_response(self, resp) -> None:
        if self._watched(resp.url):
            self._record(resp.request, resp.status)

    def _on_failed(self, req) -> None:
        if self._watched(req.url):
            self._record(req, None, req.failure)

    def allow(self, status: int | set, pattern: str, reason: str) -> None:
        statuses = status if isinstance(status, set) else {status}
        self.allowed.append((statuses, re.compile(pattern), reason))

    def _is_allowed(self, c: dict) -> bool:
        for statuses, rx, _ in self.allowed:
            if rx.search(c["label"]) and (c["status"] in statuses or (c["status"] is None and None in statuses)):
                return True
        return False

    def problems(self) -> list[str]:
        out = []
        for c in self.calls:
            if c["status"] is None:
                # Chromium aborts in-flight fetches when the page navigates or
                # closes; that is the test moving on, not the server failing.
                if c["failure"] and "ERR_ABORTED" in c["failure"]:
                    continue
                if self._is_allowed(c):
                    continue
                out.append(f"{c['method']} {c['label']} transport failure: {c['failure']}")
            elif c["status"] >= 400 and not self._is_allowed(c):
                out.append(f"{c['method']} {c['label']} -> HTTP {c['status']} ({c['duration_s']}s)")
        return out

    def find(self, method: str, label_regex: str) -> list[dict]:
        rx = re.compile(label_regex)
        return [c for c in self.calls if c["method"] == method and rx.search(c["label"])]


# Console errors that are NOT failures, each with the reason. Keep this small.
CONSOLE_ALLOWLIST: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"^Failed to load resource: the server responded with a status of \d+"),
        "Chromium's own echo of an HTTP error status. The Net monitor records the "
        "URL of every API/Supabase response and fails the test on any status the "
        "test did not declare, so judging the echo here would only double-count it "
        "(and would turn a declared, expected 4xx into a failure).",
    ),
]


class Console:
    def __init__(self, page, rec: Recorder):
        self.rec = rec
        self.errors: list[str] = []
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)

    def _on_console(self, msg) -> None:
        if msg.type == "error":
            self._add(f"console.error: {msg.text}")

    def _on_pageerror(self, err) -> None:
        self._add(f"pageerror: {err}")

    def _add(self, text: str) -> None:
        bare = text.split(": ", 1)[1] if ": " in text else text
        allowed = any(rx.search(bare) for rx, _ in CONSOLE_ALLOWLIST)
        self.rec.console_errors.append({"test": self.rec.current_test, "text": text[:500],
                                        "allowlisted": allowed})
        if not allowed:
            self.errors.append(text[:500])

    def problems(self) -> list[str]:
        return list(self.errors)


# ---------------------------------------------------------------------------
# Small helpers used by the journey modules
# ---------------------------------------------------------------------------


def path_of(url: str) -> str:
    return urlparse(url).path


def api_predicate(cfg: LiveConfig, method: str, path_regex: str) -> Callable:
    rx = re.compile(path_regex)

    def pred(resp) -> bool:
        return (resp.request.method == method and resp.url.startswith(cfg.api_url)
                and bool(rx.search(resp.url[len(cfg.api_url):].split("?")[0])))

    return pred


def body_snippet(resp, limit: int = 300) -> str:
    try:
        return resp.text()[:limit].replace("\n", " ")
    except Exception as e:  # noqa: BLE001
        return f"<body unreadable: {e}>"


def assert_status(resp, ok: set | int, what: str, started: float | None = None) -> None:
    ok = ok if isinstance(ok, set) else {ok}
    took = f" after {time.monotonic() - started:.1f}s" if started else ""
    assert resp.status in ok, (
        f"{what}: {resp.request.method} {path_of(resp.url)} -> HTTP {resp.status}{took}; "
        f"expected {sorted(ok)}. Body: {body_snippet(resp)}"
    )


def fetch_pdf(url: str) -> int:
    """GET a document URL the way a user's browser would and prove it is a PDF."""
    r = httpx.get(url, follow_redirects=True, timeout=90)
    assert r.status_code == 200, f"PDF link {url[:120]}... -> HTTP {r.status_code}: {r.text[:200]}"
    assert r.content[:4] == b"%PDF", (
        f"PDF link returned {len(r.content)} bytes that are not a PDF "
        f"(starts {r.content[:20]!r}; content-type {r.headers.get('content-type')})"
    )
    return len(r.content)


def read_zip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


def require(state: dict, key: str, why: str):
    """Return state[key] or report the test BLOCKED by an earlier failure."""
    import pytest

    if not state.get(key):
        pytest.skip(f"BLOCKED: {why} (state '{key}' missing - an earlier journey step failed)")
    return state[key]


def opt_in(cfg: LiveConfig, flag: str) -> None:
    import pytest

    if not cfg.opt_in.get(flag):
        pytest.skip(f"SKIPPED(opt-in): {OPT_IN[flag]}. Set {flag}=1 to run.")


# ---------------------------------------------------------------------------
# Browser-level helpers
# ---------------------------------------------------------------------------


def sign_in(page, cfg: LiveConfig, email: str, password: str):
    """Sign in through the real login form. Returns the GoTrue token response."""
    from playwright.sync_api import expect

    page.goto(f"{cfg.site_url}/login", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    page.locator("#email").fill(email)
    page.locator("#password").fill(password)
    with page.expect_response(
        lambda r: r.url.startswith(f"{cfg.supabase_url}/auth/v1/token") and r.request.method == "POST"
    ) as info:
        page.get_by_role("button", name="Sign in", exact=True).click()
    return info.value


def has_session(page) -> bool:
    return bool(page.evaluate(
        "() => Object.keys(localStorage).some(k => k.startsWith('sb-') && k.endsWith('-auth-token'))"
    ))


def ensure_signed_in(live) -> None:
    """Make sure the shared context holds a session (signing in via the UI if not)."""
    from playwright.sync_api import expect

    live.goto("/privacy")  # any in-app page; loads the origin's storage
    token = live.page.evaluate(
        "() => { const k = Object.keys(localStorage).find(k => k.startsWith('sb-') && k.endsWith('-auth-token'));"
        " if (!k) return null; try { return JSON.parse(localStorage.getItem(k)).access_token } catch { return null } }"
    )
    valid = False
    if token:
        r = httpx.get(f"{live.cfg.supabase_url}/auth/v1/user", timeout=30,
                      headers={"apikey": live.cfg.anon_key, "Authorization": f"Bearer {token}"})
        valid = r.status_code == 200
    if not valid:
        # A password change/reset elsewhere can revoke this context's session.
        live.page.evaluate("() => localStorage.clear()")
        resp = sign_in(live.page, live.cfg, live.account["email"], live.state["password"])
        assert resp.status == 200, f"re-sign-in failed: {resp.status} {body_snippet(resp)}"
        expect(live.page).not_to_have_url(re.compile(r"/login"))


def api_call(live, method: str, path_regex: str, action: Callable, timeout_s: float = 60):
    """Run `action` and return the API response it caused.

    Fails if the call never happens: "the assertion held because nothing was
    sent" must never read as success."""
    with live.page.expect_response(api_predicate(live.cfg, method, path_regex),
                                   timeout=timeout_s * 1000) as info:
        action()
    return info.value
