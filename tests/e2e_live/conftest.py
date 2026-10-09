"""Harness for the LIVE end-to-end suite (see README.md in this directory).

A real Chromium drives the real deployed frontend, which talks to the real API
and the real Supabase. Nothing is stubbed except PostHog (analytics).

Collection: the journey modules are named ``live_*.py``, which pytest.ini's
``python_files = test_*.py`` does not match, so a plain ``pytest`` or any CI
invocation never collects them. ``scripts/run_e2e_live.sh`` passes
``-o python_files=live_*.py`` explicitly. Importing this conftest has no side
effects and needs no configuration, so a bare ``pytest`` walking ``tests/`` is
unaffected.

Refusal: if any live test is collected and the ``E2E_LIVE_*`` variables are not
all set, the session exits with an error before a browser starts (CLAUDE.md
verification rule 8: configuration is explicit, never an import side effect).

Account: one fresh Supabase user per session, created with the admin API and
``email_confirm: true`` (no email is sent), and deleted in teardown even when
tests fail — rows in every ``user_id`` table, the ``users`` row, S3 objects
under ``users/<uid>/`` and ``sessions/<uid>/``, the ``jobs_raw`` rows the run
created, and the auth user — followed by a per-table "0 rows remain" check.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import string
import time
from pathlib import Path

import pytest

from tests.e2e_live._live import (
    ARTIFACTS,
    HERE,
    Admin,
    Console,
    LiveConfig,
    LiveConfigError,
    Net,
    Recorder,
    UserApi,
    dump_json,
)

_RECORDER = Recorder()
_SESSION: dict = {}


def _is_live(item) -> bool:
    return Path(str(item.fspath)).resolve().parent == HERE


# ---------------------------------------------------------------------------
# Collection guard
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(session, config, items):
    live = [i for i in items if _is_live(i)]
    if not live:
        return
    try:
        _SESSION["cfg"] = LiveConfig.from_env()
    except LiveConfigError as e:
        pytest.exit(str(e), returncode=4)


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cfg() -> LiveConfig:
    return _SESSION.get("cfg") or LiveConfig.from_env()


@pytest.fixture(scope="session")
def rec() -> Recorder:
    return _RECORDER


@pytest.fixture(scope="session")
def admin(cfg) -> Admin:
    return Admin(cfg)


@pytest.fixture(scope="session")
def state() -> dict:
    """Cross-module journey state (job id, current password, ...)."""
    return _SESSION.setdefault("state", {})


def _password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(18))
    return f"Nb!{core}9a"


@pytest.fixture(scope="session")
def account(cfg, admin, state, rec):
    """A brand-new user for this run. Teardown ALWAYS deletes it and verifies."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    email = f"e2e+{stamp}{secrets.token_hex(2)}@naukribaba.test"
    password = _password()
    run_started = dt.datetime.now(dt.timezone.utc).isoformat()
    uid = admin.create_user(email, password)
    marker = f"E2E{stamp}"
    acct = {"id": uid, "email": email, "marker": marker, "run_started": run_started}
    state.update(password=password, marker=marker)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for old in list(ARTIFACTS.glob("*.png")) + list(ARTIFACTS.glob("*.html")):
        old.unlink()  # a stale screenshot must not be read as this run's evidence
    # Written before any test runs: if the process dies, this is what the
    # cleanup CLI needs (python -m tests.e2e_live.cleanup --user-id ...).
    (ARTIFACTS / "last_account.json").write_text(json.dumps(acct, indent=2))
    try:
        yield acct
    finally:
        from tests.e2e_live.cleanup import CleanupIncomplete, cleanup_account

        try:
            lines = cleanup_account(cfg, uid, marker=marker, run_started_iso=run_started)
            rec.cleanup_lines.extend(lines)
        except CleanupIncomplete as e:
            rec.cleanup_lines.extend(str(e).splitlines())
            raise


@pytest.fixture(scope="session")
def user_api(cfg, account, state) -> UserApi:
    api = UserApi(cfg, account["email"], password_fn=lambda: state["password"])
    r = api.login(state["password"])
    assert r.status_code == 200, f"password grant for the fresh test user -> {r.status_code}: {r.text[:200]}"
    return api


@pytest.fixture(scope="session")
def browser(cfg):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=not cfg.headed)
    try:
        yield b
    finally:
        b.close()
        pw.stop()


def _new_context(browser, cfg):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    # Analytics only: keep a test account out of product analytics. Not part
    # of any feature under test.
    ctx.route("**/*posthog.com/**", lambda route: route.fulfill(status=200, body="{}"))
    ctx.set_default_timeout(30_000)
    return ctx


@pytest.fixture(scope="session")
def context(browser, cfg, account):
    """The signed-in user's browser profile, shared across the whole journey."""
    ctx = _new_context(browser, cfg)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture()
def fresh_context(browser, cfg):
    """A clean, signed-out browser profile (for sign-in / reset-link tests)."""
    ctx = _new_context(browser, cfg)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture(scope="session", autouse=True)
def _preflight(cfg):
    """Prove the deployed frontend is built against the API and Supabase we
    were told about — otherwise every result below describes some other
    system (CLAUDE.md rule 12: check the instrument first)."""
    import re as _re

    import httpx

    html = httpx.get(cfg.site_url + "/", timeout=30).text
    seen, queue, blobs = set(), _re.findall(r"/assets/[\w.-]+\.js", html), []
    while queue and len(seen) < 80:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        js = httpx.get(cfg.site_url + path, timeout=30).text
        blobs.append(js)
        queue += [f"/assets/{m}" for m in _re.findall(r"assets/([\w.-]+\.js)", js)]
        queue += [f"/assets/{m}" for m in _re.findall(r"\"\./([\w.-]+\.js)\"", js)]
    bundle = "\n".join(blobs)
    missing = [name for name, v in (("E2E_LIVE_API_URL", cfg.api_url),
                                    ("E2E_LIVE_SUPABASE_URL", cfg.supabase_url)) if v not in bundle]
    if missing:
        pytest.exit(f"preflight: the bundle at {cfg.site_url} ({len(seen)} chunks) does not contain "
                    f"{', '.join(missing)} — it is not built against the system this run would verify.",
                    returncode=4)
    health = httpx.get(cfg.api_url + "/api/health", timeout=60)
    if health.status_code != 200:
        pytest.exit(f"preflight: GET /api/health -> {health.status_code}", returncode=4)


@pytest.fixture(scope="session", autouse=True)
def _expect_timeout():
    from playwright.sync_api import expect

    expect.set_options(timeout=20_000)


class Live:
    """What a journey test gets: the page plus its monitors and helpers."""

    def __init__(self, page, net, console, cfg, rec, state, account, admin, user_api):
        self.page, self.net, self.console = page, net, console
        self.cfg, self.rec, self.state = cfg, rec, state
        self.account, self.admin, self.api = account, admin, user_api

    def url(self, path: str) -> str:
        return f"{self.cfg.site_url}{path}"

    def goto(self, path: str):
        return self.page.goto(self.url(path), wait_until="domcontentloaded")

    def watch(self, page):
        """Attach monitors to an additional page (e.g. a fresh context)."""
        return Net(page, self.cfg, self.rec), Console(page, self.rec)


def _attach(request, page, cfg, rec):
    net, con = Net(page, cfg, rec), Console(page, rec)
    request.node._live_pages = getattr(request.node, "_live_pages", []) + [page]
    request.node._live_monitors = getattr(request.node, "_live_monitors", []) + [(net, con)]
    return net, con


@pytest.fixture()
def live(request, context, cfg, rec, state, account, admin, user_api):
    rec.current_test = request.node.name
    page = context.new_page()
    net, con = _attach(request, page, cfg, rec)
    obj = Live(page, net, con, cfg, rec, state, account, admin, user_api)
    request.node._live_obj = obj
    try:
        yield obj
    finally:
        try:
            _verify_monitors(request, rec)
        finally:
            page.close()


@pytest.fixture()
def fresh_page(request, live, fresh_context, cfg, rec):
    """A page in a signed-out context, monitored exactly like `live.page`.

    Depends on `live` so the same teardown verifies both pages' monitors."""
    from types import SimpleNamespace

    page = fresh_context.new_page()
    net, con = _attach(request, page, cfg, rec)
    return SimpleNamespace(page=page, net=net, console=con)


def _verify_monitors(request, rec):
    problems = []
    for net, con in getattr(request.node, "_live_monitors", []):
        problems += [f"network: {p}" for p in net.problems()]
        problems += [f"console: {p}" for p in con.problems()]
    if problems:
        rec.add("network + console clean", "FAIL", " | ".join(problems))
        pytest.fail("Unexpected network/console errors:\n  " + "\n  ".join(problems), pytrace=False)


# ---------------------------------------------------------------------------
# Failure artifacts + final report
# ---------------------------------------------------------------------------


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _is_live(item) or report.passed or report.skipped:
        return
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, page in enumerate(getattr(item, "_live_pages", [])):
        try:
            if page.is_closed():
                continue
            base = ARTIFACTS / f"{item.name}-{report.when}-{i}"
            page.screenshot(path=f"{base}.png", full_page=True)
            Path(f"{base}.html").write_text(page.content())
            saved.append(f"{base.relative_to(HERE.parents[1])}.png")
        except Exception as e:  # noqa: BLE001
            saved.append(f"<screenshot failed: {e}>")
    if saved:
        for s in reversed(_RECORDER.steps):
            if s["test"] == item.name and s["outcome"] == "FAIL":
                s["evidence"] += f" [screenshot: {', '.join(saved)}]"
                break
        else:
            _RECORDER.add("(test body)", "FAIL", f"{report.longreprtext[-400:]} [screenshot: {', '.join(saved)}]")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    rec = _RECORDER
    if not rec.steps and not rec.cleanup_lines:
        return
    tr = terminalreporter
    tr.section("e2e_live: interactions")
    for s in rec.steps:
        lat = f"{s['latency_s']:.1f}s" if isinstance(s["latency_s"], (int, float)) else "-"
        tr.write_line(f"{s['outcome']:<8} {lat:>8}  {s['test']} :: {s['interaction']}")
        if s["outcome"] != "PASS" and s["evidence"]:
            tr.write_line(f"{'':18}{s['evidence']}")
    tr.section(f"e2e_live: synchronous calls over 25s ({len(rec.slow_calls)})")
    for c in rec.slow_calls:
        tr.write_line(f"{c['duration_s']:>6}s  {c['method']} {c['label']} -> {c['status']}  [{c['test']}]")
    tr.section("e2e_live: console errors")
    for c in rec.console_errors:
        tag = "allowlisted" if c["allowlisted"] else "FAILED"
        tr.write_line(f"[{tag}] [{c['test']}] {c['text'][:300]}")
    if rec.notes:
        tr.section("e2e_live: notes")
        for n in rec.notes:
            tr.write_line(n)
    tr.section("e2e_live: cleanup verification")
    for line in rec.cleanup_lines or ["cleanup did not run (no account was created)"]:
        tr.write_line(line)
    dump_json(ARTIFACTS / "report.json", {
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "steps": rec.steps, "slow_calls": rec.slow_calls,
        "console_errors": rec.console_errors, "notes": rec.notes,
        "cleanup": rec.cleanup_lines,
    })
