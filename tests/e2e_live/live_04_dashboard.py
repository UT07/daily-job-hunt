"""Journey 4: the Dashboard before any job exists — filters, search, tabs, sort,
view toggle, URL sync, empty states, notifications.

Every filter interaction asserts the request the browser actually sent
(GET /api/dashboard/jobs with the expected query) and the URL it synced to,
not merely that a control changed."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import assert_status, ensure_signed_in, require


def _is_main_list(cfg, resp) -> bool:
    if resp.request.method != "GET" or not resp.url.startswith(f"{cfg.api_url}/api/dashboard/jobs?"):
        return False
    q = parse_qs(urlparse(resp.url).query)
    return q.get("per_page") == ["25"] and q.get("lifecycle") == ["active"]


def list_request(live, action, timeout_s: float = 45) -> dict:
    """Do `action`; return the query of the job-list request it caused (status asserted)."""
    with live.page.expect_response(lambda r: _is_main_list(live.cfg, r), timeout=timeout_s * 1000) as info:
        action()
    resp = info.value
    assert_status(resp, 200, "job list")
    live.state["_last_list"] = [j.get("job_id") for j in (resp.json().get("jobs") or [])]
    return {k: v[0] for k, v in parse_qs(urlparse(resp.url).query).items()}


def job_row(live, job_id: str):
    """The table row of `job_id` in the list most recently loaded."""
    ids = live.state.get("_last_list") or []
    assert job_id in ids, f"job {job_id} is not in the loaded list ({len(ids)} rows)"
    return live.page.locator("table tbody tr").nth(ids.index(job_id))


def url_query(page) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(page.url).query).items()}


@pytest.fixture()
def dash(live):
    require(live.state, "onboarded", "onboarding did not complete")
    ensure_signed_in(live)
    return live


def test_dashboard_loads_with_empty_state(dash):
    live, page = dash, dash.page
    with live.rec.step("dashboard loads: jobs, stats, skills, runs all 200"):
        wanted = {
            "stats": r"^API /api/dashboard/stats$",
            "skills": r"^API /api/dashboard/skills$",
            "runs": r"^API /api/dashboard/runs$",
        }
        q = list_request(live, lambda: live.goto("/"))
        expect(page.get_by_role("heading", name="Job Dashboard")).to_be_visible()
        page.wait_for_load_state("networkidle")
        for name, rx in wanted.items():
            calls = live.net.find("GET", rx)
            assert calls, f"dashboard never requested {name}"
            assert calls[-1]["status"] == 200, f"{name} -> {calls[-1]['status']}"
        assert q.get("min_score") == "60" and q.get("hide_expired") == "true", q
    with live.rec.step("empty state for a user with no jobs ever ('No jobs yet' + Add Job)"):
        expect(page.get_by_text("No jobs yet. Add one manually or run the pipeline above to get started.")).to_be_visible()
        expect(page.get_by_text("0 jobs", exact=False).first).to_be_visible()


def test_dashboard_search(dash):
    live, page = dash, dash.page
    list_request(live, lambda: live.goto("/"))
    box = page.get_by_role("searchbox", name="Search jobs by title or company")
    with live.rec.step("search: type + Enter sends q= and syncs URL"):
        q = list_request(live, lambda: (box.fill("kubernetes"), box.press("Enter")))
        assert q.get("q") == "kubernetes", q
        expect(page).to_have_url(re.compile(r"[?&]q=kubernetes"))
        expect(page.get_by_role("button", name=re.compile('Search: "kubernetes"'))).to_be_visible()
    with live.rec.step("search: Clear drops q= from request and URL"):
        q = list_request(live, lambda: page.get_by_role("button", name="Clear", exact=True).click())
        assert "q" not in q, q
        assert "q" not in url_query(page)


def test_dashboard_filters_and_url_sync(dash):
    live, page = dash, dash.page
    list_request(live, lambda: live.goto("/"))
    apply = page.get_by_role("button", name="Apply Filters")

    with live.rec.step("filters: status + source + min score + title + company -> request + URL"):
        page.locator("#status").select_option("Applied")
        page.locator("#source").select_option("manual")
        page.locator("input[type=range]").fill("30")
        page.get_by_placeholder("Search title...").fill("Engineer")
        page.get_by_placeholder("Search company...").fill("Acme")
        q = list_request(live, apply.click)
        assert (q.get("status"), q.get("source"), q.get("min_score"), q.get("title"), q.get("company")) == \
            ("Applied", "manual", "30", "Engineer", "Acme"), q
        u = url_query(page)
        assert (u.get("status"), u.get("source"), u.get("min_score")) == ("Applied", "manual", "30"), u

    with live.rec.step("filters: Tailored + Hide Expired toggles"):
        page.locator("div:has(> label:text-is('Tailored')) > button").click()
        page.locator("div:has(> label:text-is('Hide Expired')) > button").click()
        q = list_request(live, apply.click)
        assert q.get("tailored") == "true" and "hide_expired" not in q, q
        u = url_query(page)
        assert u.get("tailored") == "true" and u.get("hide_expired") == "false", u

    with live.rec.step("advanced filters: archetype/seniority/remote/level fit each refetch"):
        page.get_by_role("button", name="+ Advanced").click()
        for sel, val, key in (("#archetype", "backend", "archetype"), ("#seniority", "Senior", "seniority"),
                              ("#remote", "Remote", "remote"), ("#level-fit", "stretch", "level_fit")):
            q = list_request(live, lambda s=sel, v=val: page.locator(s).select_option(v))
            assert q.get(key) == val, (key, q)
        assert url_query(page).get("show_advanced") == "true"

    with live.rec.step("active-filter chips + Clear all resets the request"):
        expect(page.get_by_text("Filtered by:")).to_be_visible()
        q = list_request(live, lambda: page.get_by_role("button", name="Clear all", exact=True).click())
        for k in ("status", "source", "title", "company", "tailored", "archetype", "seniority", "remote", "level_fit"):
            assert k not in q, (k, q)
        assert "min_score" not in q and "hide_expired" not in q, q


def test_dashboard_tier_tabs(dash):
    live, page = dash, dash.page
    list_request(live, lambda: live.goto("/"))
    for label, tier in (("Must Apply", "S"), ("Strong Match", "A"), ("Worth Trying", "B"), ("All Jobs", None)):
        with live.rec.step(f"tier tab '{label}' -> tier={tier}"):
            q = list_request(live, lambda lb=label: page.get_by_role("button", name=re.compile(lb)).click())
            assert q.get("tier") == tier, q
            assert url_query(page).get("tier") == tier


def test_dashboard_sort(dash):
    live, page = dash, dash.page
    list_request(live, lambda: live.goto("/"))
    with live.rec.step("sort select -> sort_by/sort_order in request + URL"):
        sort = page.locator("select").filter(has_text="Score (highest)")
        q = list_request(live, lambda: sort.select_option("match_score:desc"))
        assert (q.get("sort_by"), q.get("sort_order")) == ("match_score", "desc"), q
        assert url_query(page).get("sort_by") == "match_score"


def test_dashboard_view_toggle_persists(dash):
    live, page = dash, dash.page
    list_request(live, lambda: live.goto("/"))
    with live.rec.step("view toggle: card view persists across reload"):
        page.get_by_title("Card view").click()
        assert page.evaluate("localStorage.getItem('naukribaba_view')") == "card"
        list_request(live, page.reload)
        expect(page.get_by_title("Card view")).to_have_class(re.compile(r"bg-black"))
        page.get_by_title("List view").click()
        assert page.evaluate("localStorage.getItem('naukribaba_view')") == "list"


def test_dashboard_deep_link_restores_filters(dash):
    live, page = dash, dash.page
    with live.rec.step("deep link ?q=&status=&tier=&min_score= hydrates controls and the request"):
        q = list_request(live, lambda: live.goto("/?q=platform&status=Applied&tier=A&min_score=40"))
        assert (q.get("q"), q.get("status"), q.get("tier"), q.get("min_score")) == ("platform", "Applied", "A", "40"), q
        expect(page.get_by_role("searchbox", name="Search jobs by title or company")).to_have_value("platform")
        expect(page.locator("#status")).to_have_value("Applied")
        expect(page.get_by_text("Min Score:")).to_contain_text("40")


def test_notifications_dropdown(dash):
    live, page = dash, dash.page
    list_request(live, lambda: live.goto("/"))
    with live.rec.step("notifications: bell opens 'Recent Activity' for a new user"):
        page.wait_for_load_state("networkidle")
        runs = live.net.find("GET", r"API /api/dashboard/runs$")
        assert runs and runs[-1]["status"] == 200, f"runs request: {runs}"
        page.get_by_role("button", name="Notifications").click()
        expect(page.get_by_text(re.compile(r"Recent Activity \(\d+\)"))).to_be_visible()
        expect(page.get_by_text("No recent activity")).to_be_visible()
