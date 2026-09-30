"""Harness for the browser E2E suite.

What a test gets
---------------
A **real production build** of `web/`, served over HTTP, driven by a real
Chromium, with every outbound HTTP call intercepted inside the browser. No API
process, no Supabase project, no AWS. The only thing faked is HTTP: the router,
the lazy chunks, `AuthProvider`, the CSS and every component are the ones that
ship to Netlify.

Why a built bundle and not `vite dev`
------------------------------------
`vite dev` serves unminified ESM with HMR and `import.meta.env.DEV === true`,
so it cannot catch a production-only break (a bad lazy chunk, a DEV-guarded
branch, dead-code elimination). The build is also what actually deploys.

Why `--mode e2e`
----------------
`vite build` defaults to mode `production`, which loads `web/.env.production` —
a gitignored file that on the operator's laptop holds the **real** API,
Supabase and PostHog values, and that does not exist in CI at all. Building in
that mode means this suite talks to production locally and to nothing in CI:
the two runs would not be the same test. Mode `e2e` has no dotenv file in
either place, so the only configuration is what this module puts in the build
subprocess's environment.

What this suite does and does not prove
---------------------------------------
It proves the shipped bundle renders, routes, gates on auth, and issues the
right HTTP requests with the right payloads for a given user action. It cannot
prove anything about the server that answers them: every response here is a
fixture. Per CLAUDE.md § Verification rules 5, a stubbed boundary confirms only
what the fixture author already believed, so anything that must cross a real
boundary (Step Functions actually executing, a row actually persisting, a PDF
actually compiling) is left to `scripts/smoke_prod.py` and the contract and
integration suites, and the tests here say so in their docstrings.

Two guards keep that honest (§ Verification rules 2 — a status that cannot
tell "did the work" from "did nothing" is a lie):

* An `/api/**` call no stub matched is answered **503** and recorded, and
  `api_stub` fails the test at teardown naming the path. A page that quietly
  rendered its error state because the app began calling a new endpoint is a
  failure here, never a pass.
* `api_stub.assert_called(...)` lets a test state that the request it cares
  about really left the browser, so "the assertion held because the component
  never rendered" cannot read as success.

Nothing in this file skips when the frontend is missing. If the build or the
server is broken the fixture raises, because "the app would not start" must
not be reported as "no tests to run".
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

# `test_full_system.py` is a standalone script that hits the live production
# API and calls sys.exit(...) at module top level (see its docstring). Pytest
# auto-discovers it by name but importing it during collection crashes the run
# with INTERNALERROR. Skip it from collection -- it is still runnable as a
# script.
collect_ignore = ["test_full_system.py"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = PROJECT_ROOT / "web"
DIST_DIR = WEB_DIR / "dist"

# Origins baked into the test build. `stub.supabase.test` does not resolve, on
# purpose: if an interception ever fails to match, the request dies with a DNS
# error instead of reaching a real service.
SUPABASE_ORIGIN = "https://stub.supabase.test"
SUPABASE_ANON_KEY = "stub-anon-key-not-a-secret"

# Same shape as tests/security/conftest.py -- an explicit, obviously-fake
# default rather than a real secret. The bundle never receives this value; it
# only signs the access token the stubbed GoTrue endpoint hands back, and
# nothing in this suite verifies that signature. Reading it from the
# environment is what lets the same helper mint a token a live API would
# accept, when SUPABASE_JWT_SECRET happens to be the real one.
os.environ.setdefault("SUPABASE_JWT_SECRET", "super-secret-jwt-key-for-testing-only-32chars!")

TEST_USER_ID = "00000000-0000-4000-8000-000000000001"
TEST_EMAIL = "e2e@example.test"
TEST_PASSWORD = "e2e-password-1234"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Record each phase's report on the item.

    `api_stub`'s teardown check reads `rep_call` so that a test which already
    failed is not also reported as leaking unstubbed endpoints -- the second
    message is always a consequence of the first and buries it.
    """
    rep = yield
    setattr(item, f"rep_{rep.when}", rep)
    return rep


# ---------------------------------------------------------------------------
# Building and serving the frontend
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 900) -> None:
    proc = subprocess.run(  # noqa: S603
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{' '.join(cmd)}` failed in {cwd} with exit {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-4000:]}"
        )


class _SPAHandler(http.server.SimpleHTTPRequestHandler):
    """Static server with the SPA fallback Netlify performs.

    Without it, `page.goto("/settings")` gets a 404 from the static server and
    react-router never runs, so every deep-link test would fail for a reason
    that has nothing to do with the app.
    """

    def do_GET(self) -> None:  # noqa: N802
        target = Path(self.translate_path(self.path))
        if not target.exists() or target.is_dir():
            self.path = "/index.html"
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            # Chromium cancels in-flight asset requests on navigation. Letting
            # these surface buries real failures under pages of traceback.
            pass

    def log_message(self, *_args) -> None:  # silence per-request logging
        return


@pytest.fixture(scope="session")
def frontend_url() -> str:
    """Build `web/` in mode `e2e` and serve `web/dist` over HTTP.

    Raises rather than skips: a frontend that will not build is a failure.
    """
    if not (WEB_DIR / "node_modules").is_dir():
        _run(["npm", "ci"], cwd=WEB_DIR)

    build_env = {
        **os.environ,
        # Empty => same-origin, so the browser issues `/api/...` and a single
        # `**/api/**` interception catches every call.
        "VITE_API_URL": "",
        "VITE_SUPABASE_URL": SUPABASE_ORIGIN,
        "VITE_SUPABASE_ANON_KEY": SUPABASE_ANON_KEY,
        # Empty on purpose: lib/posthog.js returns early without a key, so no
        # analytics request is made. VITE_BUILD_LABEL=production keeps
        # PreviewBanner rendering null.
        "VITE_POSTHOG_KEY": "",
        "VITE_BUILD_LABEL": "production",
    }
    _run(["npm", "run", "build", "--", "--mode", "e2e"], cwd=WEB_DIR, env=build_env)

    index = DIST_DIR / "index.html"
    if not index.is_file():
        raise RuntimeError(f"vite build reported success but {index} does not exist")

    handler = functools.partial(_SPAHandler, directory=str(DIST_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session", autouse=True)
def _expect_timeout() -> None:
    """Give Playwright's web assertions a CI-sized budget.

    The default 5s is too tight for a first paint that must also fetch a lazy
    chunk on a cold GitHub runner, and a flaky suite gets muted rather than
    fixed.
    """
    from playwright.sync_api import expect

    expect.set_options(timeout=15_000)


@pytest.fixture(scope="session")
def base_url(frontend_url: str) -> str:
    """Override pytest-playwright's base_url so `page.goto("/settings")` works."""
    return frontend_url


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def mint_access_token(
    *, user_id: str = TEST_USER_ID, email: str = TEST_EMAIL, ttl: timedelta = timedelta(hours=1)
) -> str:
    """Mint an HS256 Supabase-shaped access token.

    Reuses tests/security/conftest.py's claim set and signing call, so a token
    from this suite is interchangeable with the ones the API tests send.
    """
    from jose import jwt as jose_jwt

    now = datetime.now(timezone.utc)
    return jose_jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "aud": "authenticated",
            "role": "authenticated",
            "iss": "supabase",
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def gotrue_user(*, user_id: str = TEST_USER_ID, email: str = TEST_EMAIL) -> dict:
    stamp = "2026-01-01T00:00:00Z"
    return {
        "id": user_id,
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "email_confirmed_at": stamp,
        "phone": "",
        "confirmed_at": stamp,
        "last_sign_in_at": stamp,
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {},
        "identities": [],
        "created_at": stamp,
        "updated_at": stamp,
        "is_anonymous": False,
    }


def gotrue_session(*, user_id: str = TEST_USER_ID, email: str = TEST_EMAIL) -> dict:
    """The body GoTrue returns from /auth/v1/token?grant_type=password."""
    expires_in = 3600
    return {
        "access_token": mint_access_token(user_id=user_id, email=email),
        "token_type": "bearer",
        "expires_in": expires_in,
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + expires_in,
        "refresh_token": "stub-refresh-token",
        "user": gotrue_user(user_id=user_id, email=email),
    }


class AuthStub:
    """Stubbed GoTrue. Defaults to "those credentials are good"."""

    def __init__(self) -> None:
        self.reject_password = False
        self.calls: list[str] = []

    def handle(self, route, request) -> None:
        parsed = urlparse(request.url)
        self.calls.append(f"{request.method} {parsed.path}{'?' + parsed.query if parsed.query else ''}")
        path, query = parsed.path, parsed.query

        def reply(status: int, body: dict) -> None:
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

        if path.endswith("/auth/v1/token") and "grant_type=password" in query:
            if self.reject_password:
                # GoTrue's real 400 body. supabase-js reads `msg`; older
                # clients read `error_description`. Both are sent so the
                # assertion lands on the app's handling rather than on which
                # client version is installed.
                reply(
                    400,
                    {
                        "code": 400,
                        "error_code": "invalid_credentials",
                        "msg": "Invalid login credentials",
                        "error": "invalid_grant",
                        "error_description": "Invalid login credentials",
                    },
                )
            else:
                reply(200, gotrue_session())
        elif path.endswith("/auth/v1/token"):  # refresh_token grant
            reply(200, gotrue_session())
        elif path.endswith("/auth/v1/signup"):
            # Email confirmation on => user created, no session issued.
            reply(200, {"user": gotrue_user(), "session": None})
        elif path.endswith("/auth/v1/logout"):
            route.fulfill(status=204, body="")
        elif path.endswith("/auth/v1/user"):
            reply(200, gotrue_user())
        elif path.endswith("/auth/v1/recover"):
            reply(200, {})
        else:
            reply(404, {"msg": f"tests/e2e: unstubbed GoTrue path {path}"})


@pytest.fixture()
def auth_stub(page) -> AuthStub:
    stub = AuthStub()
    page.route(f"{SUPABASE_ORIGIN}/**", stub.handle)
    return stub


# ---------------------------------------------------------------------------
# The API stub
# ---------------------------------------------------------------------------


class ApiStub:
    """Serves canned `/api/**` responses and records what was asked for.

    Routes are registered as `METHOD /path` with no query string. The query
    string is recorded but never matched, so a test asserts on it explicitly
    when it is the thing under test (`assert_called(..., query_contains=...)`).
    Three of the dashboard's requests share one path and differ only by query,
    so a handler may also be a callable receiving the recorded call.
    """

    def __init__(self, page) -> None:
        self._routes: dict[tuple[str, str], tuple[int, object]] = {}
        self._page = page
        self.calls: list[dict] = []
        self.unstubbed: list[str] = []

    # -- registration -----------------------------------------------------

    def on(self, method: str, path: str, response: object, *, status: int = 200) -> ApiStub:
        """Register a response: a dict/list (JSON), bytes (PDF), a
        `(status, body)` pair, or a callable taking the recorded call dict.
        """
        self._routes[(method.upper(), path)] = (status, response)
        return self

    def on_many(self, spec: dict[str, object]) -> ApiStub:
        """Register several routes from a `{"GET /api/x": body}` mapping."""
        for key, body in spec.items():
            method, path = key.split(" ", 1)
            self.on(method, path, body)
        return self

    # -- assertions -------------------------------------------------------

    def matching(self, method: str, path: str) -> list[dict]:
        return [c for c in self.calls if c["method"] == method.upper() and c["path"] == path]

    def _check_called(self, method: str, path: str, query_contains: str | None) -> dict:
        hits = self.matching(method, path)
        if not hits:
            seen = "\n  ".join(f"{c['method']} {c['path']}{c['query']}" for c in self.calls) or "(nothing)"
            raise AssertionError(f"browser never issued {method.upper()} {path}. It issued:\n  {seen}")
        if query_contains is not None:
            narrowed = [c for c in hits if query_contains in c["query"]]
            if not narrowed:
                raise AssertionError(
                    f"{method.upper()} {path} was called but no query contained {query_contains!r}; "
                    f"queries seen: {[c['query'] for c in hits]}"
                )
            hits = narrowed
        return hits[-1]

    def assert_called(
        self, method: str, path: str, *, query_contains: str | None = None, timeout: float = 15.0
    ) -> dict:
        """Wait for the browser to issue this request; return the last match.

        Retries like a Playwright web assertion, because a click and the fetch
        it triggers are not the same tick: an immediate check would make every
        interaction test a race, and races get "fixed" with sleeps that hide
        the very latency regressions worth catching. `wait_for_timeout` is used
        rather than `time.sleep` so the driver keeps pumping and route handlers
        keep answering while we wait.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self._check_called(method, path, query_contains)
            except AssertionError:
                if time.monotonic() >= deadline:
                    raise
                self._page.wait_for_timeout(100)

    def assert_not_called(self, method: str, path: str, *, settle: float = 1.0) -> None:
        """Assert this request is never issued, after letting the app settle.

        Without the settle window this asserts only that the request had not
        been issued *yet*, which passes on a page that is about to issue it.
        """
        self._page.wait_for_timeout(settle * 1000)
        hits = self.matching(method, path)
        if hits:
            raise AssertionError(f"{method.upper()} {path} was called {len(hits)}x and should not have been")

    # -- playwright plumbing ----------------------------------------------

    def _record(self, request) -> dict:
        parsed = urlparse(request.url)
        body: object = None
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                body = request.post_data_json
            except Exception:  # not JSON: multipart upload, or empty body
                body = request.post_data
        call = {
            "method": request.method,
            "path": parsed.path,
            "query": f"?{parsed.query}" if parsed.query else "",
            "params": {k: v[0] for k, v in parse_qs(parsed.query).items()},
            "url": request.url,
            "body": body,
            "headers": request.headers,
        }
        self.calls.append(call)
        return call

    def handle(self, route, request) -> None:
        call = self._record(request)
        entry = self._routes.get((request.method, call["path"]))
        if entry is None:
            self.unstubbed.append(f"{request.method} {call['path']}{call['query']}")
            route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"detail": f"tests/e2e: no stub for {request.method} {call['path']}"}),
            )
            return
        status, body = entry
        if callable(body):
            body = body(call)
        if isinstance(body, tuple):
            status, body = body
        if isinstance(body, (dict, list)):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        elif isinstance(body, bytes):
            route.fulfill(status=status, content_type="application/pdf", body=body)
        else:
            route.fulfill(status=status, content_type="text/plain", body=str(body))


@pytest.fixture()
def api_stub(request, page, auth_stub) -> ApiStub:
    """Intercept `/api/**`; fail the test for anything left unstubbed.

    `auth_stub` is requested, not used: it is what installs the GoTrue
    interception, and every authenticated test needs both. Depending on it here
    means a test can ask for `api_stub` alone and still be able to sign in.
    """
    stub = ApiStub(page)
    page.route("**/api/**", stub.handle)
    register_shell_routes(stub)
    yield stub

    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        return  # the primary failure is the story; do not bury it
    if stub.unstubbed:
        raise AssertionError(
            "the app called endpoints this test did not stub, so it rendered an error "
            "state instead of the state under test:\n  " + "\n  ".join(sorted(set(stub.unstubbed)))
        )


@pytest.fixture()
def console_errors(page) -> list[str]:
    """Collect browser console errors, minus noise no app controls."""
    noise = re.compile(r"favicon|ERR_NAME_NOT_RESOLVED", re.I)
    found: list[str] = []
    page.on(
        "console",
        lambda msg: found.append(msg.text) if msg.type == "error" and not noise.search(msg.text) else None,
    )
    page.on("pageerror", lambda exc: found.append(f"uncaught: {exc}"))
    return found


# ---------------------------------------------------------------------------
# Fixture data. Field names are the backend's, taken from app.py's responses
# and cross-checked against web/src/pages/__tests__/Dashboard.lifecycle.test.jsx.
# ---------------------------------------------------------------------------

PROFILE = {
    "id": TEST_USER_ID,
    "email": TEST_EMAIL,
    "full_name": "E2E Test User",
    "phone": "+353850000000",
    "location": "Dublin, Ireland",
    "github_url": "https://github.com/e2e",
    "linkedin_url": "https://linkedin.com/in/e2e",
    "website": "",
    "visa_status": "Stamp 1G",
    "work_authorizations": {"Ireland": "Stamp 1G"},
    "candidate_context": "",
    "plan": "free",
    "created_at": "2026-01-01T00:00:00Z",
    # Non-null keeps ConsentBanner from covering the bottom of the viewport.
    "gdpr_consent_at": "2026-01-01T00:00:00Z",
    "salary_expectation_notes": "",
    "notice_period_text": "1 month",
    # AppLayout's gate: without one of these two the app redirects to
    # /onboarding and every authenticated test fails for the wrong reason.
    "onboarding_completed_at": "2026-01-02T00:00:00Z",
    # False would add FinishSetupBanner above the page content.
    "profile_complete": True,
}

SEARCH_CONFIG = {
    "queries": ["site reliability engineer", "platform engineer"],
    "locations": ["Dublin, Ireland"],
    "experience_levels": ["mid"],
    "days_back": 7,
    "max_jobs_per_run": 15,
    "min_match_score": 60,
    "enabled_sources": ["linkedin", "indeed", "greenhouse", "ashby", "irish_portals", "hn", "yc"],
}


def make_job(**overrides) -> dict:
    """One dashboard row. `job_id`, not `id` -- see JobTable.jsx."""
    job = {
        "job_id": "job-0001",
        "job_hash": "hash-0001",
        "title": "Site Reliability Engineer",
        "company": "Stub Systems",
        "location": "Dublin, Ireland",
        "apply_url": "https://example.test/jobs/0001",
        "source": "linkedin",
        "match_score": 91,
        "score_tier": "S",
        "application_status": "New",
        "is_expired": False,
        "first_seen": "2026-09-25T09:00:00Z",
        "posted_date": "2026-09-24",
        "resume_s3_url": None,
        "cover_letter_s3_url": None,
        "resume_doc_url": None,
        "tailoring_model": None,
        "matched_resume": "sre_devops",
        "linkedin_contacts": None,
        "key_matches": ["AWS", "Kubernetes", "Terraform"],
        "description": "We run a large fleet on AWS and need someone to keep it alive.",
    }
    job.update(overrides)
    return job


STATS = {
    "total_jobs": 3,
    "matched_jobs": 3,
    "avg_match_score": 81.0,
    "jobs_by_status": {"New": 3},
    "total_applied": 0,
    "total_rejected": 0,
    "total_interviewing": 0,
    "total_offers": 0,
}


def register_shell_routes(stub: ApiStub) -> ApiStub:
    """The requests the app shell makes on any authenticated route.

    `/api/profile` is fetched twice (ProfileProvider and ConsentBanner) and
    `/api/search-config` by both PipelineStatus and Settings, so these are
    registered once and answered idempotently.
    """
    return stub.on_many(
        {
            "GET /api/profile": PROFILE,
            "GET /api/search-config": SEARCH_CONFIG,
            "GET /api/dashboard/runs": {"runs": []},
            "GET /api/pipeline/status": {"latest_run": None, "today_metrics": []},
        }
    )


def register_dashboard_routes(
    stub: ApiStub,
    *,
    active: list[dict] | None = None,
    stale: list[dict] | None = None,
    grand_total: int | None = None,
    total: int | None = None,
    stats: dict | None = None,
    skills: list[dict] | None = None,
) -> ApiStub:
    """Register the Dashboard's requests.

    `/api/dashboard/jobs` is hit three times with different query strings --
    the main list (`lifecycle=active`), the Past/Outdated shelf
    (`lifecycle=stale`) and the grand total (`per_page=1`) -- so one callable
    discriminates between them exactly as Dashboard.lifecycle.test.jsx does.
    `total` may exceed `len(active)` to exercise pagination.
    """
    active = [make_job()] if active is None else active
    stale = [] if stale is None else stale
    grand_total = len(active) if grand_total is None else grand_total
    list_total = len(active) if total is None else total

    def jobs(call: dict) -> dict:
        params = call["params"]
        if params.get("per_page") == "1":
            return {"jobs": [], "page": 1, "per_page": 1, "total": grand_total}
        if params.get("lifecycle") == "stale":
            return {"jobs": stale, "page": 1, "per_page": 25, "total": len(stale)}
        return {
            "jobs": active,
            "page": int(params.get("page", 1)),
            "per_page": int(params.get("per_page", 25)),
            "total": list_total,
        }

    return stub.on_many(
        {
            "GET /api/dashboard/jobs": jobs,
            "GET /api/dashboard/stats": stats if stats is not None else STATS,
            "GET /api/dashboard/skills": {"skills": skills if skills is not None else [{"name": "AWS", "count": 3}]},
        }
    )


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


def wait_until(page, predicate, *, timeout: float = 15.0, message: str = "condition never became true") -> None:
    """Poll a Python-side predicate while letting the browser make progress."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        page.wait_for_timeout(100)


def sign_in(page, *, email: str = TEST_EMAIL, password: str = TEST_PASSWORD) -> None:
    """Sign in through the real login form.

    Deliberately not a localStorage seed. Driving the form means supabase-js
    persists the session in whatever format its own version uses, so the suite
    does not encode a private storage layout that a dependency bump would
    silently break -- and the sign-in path itself gets exercised on every test
    that needs a session.
    """
    page.goto("/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()


@pytest.fixture()
def dashboard(page, api_stub):
    """A signed-in browser sitting on the dashboard with one job listed."""
    register_dashboard_routes(api_stub)
    sign_in(page)
    page.wait_for_url("**/", timeout=15_000)
    page.get_by_role("heading", name="Job Dashboard").wait_for(timeout=15_000)
    return api_stub
