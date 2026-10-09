"""Journey 7: the tailored job on the Dashboard (min_score=0), the status
dropdown, card view, the filtered-empty state, and the Artifacts page."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, fetch_pdf, require
from tests.e2e_live.live_04_dashboard import job_row, list_request
from tests.e2e_live.live_06_add_job import TITLE, company


@pytest.fixture()
def withjob(live):
    require(live.state, "job_id", "Tailor Resume did not produce a job")
    ensure_signed_in(live)
    return live


def _row(page, state):
    return page.locator("table tbody tr").filter(has_text=company(state))


def test_job_listed_on_dashboard(withjob):
    live, page = withjob, withjob.page
    with live.rec.step("job appears on the Dashboard with min_score=0"):
        q = list_request(live, lambda: live.goto("/?min_score=0"))
        assert "min_score" not in q, q
        row = job_row(live, live.state["job_id"])
        expect(row).to_contain_text(company(live.state))
        expect(row.get_by_text(TITLE)).to_be_visible()
    with live.rec.step("one pasted JD is one dashboard row (no duplicate job)"):
        rows = _row(page, live.state)
        if rows.count() != 1:
            dupes = live.admin.select("jobs", {"user_id": f"eq.{live.account['id']}",
                                               "company": f"eq.{company(live.state)}",
                                               "select": "job_id,job_hash,canonical_hash,score_tier,first_seen"})
            pytest.fail(f"{rows.count()} dashboard rows for one job description: {dupes}")
    with live.rec.step("searching the job's company finds exactly it"):
        box = page.get_by_role("searchbox", name="Search jobs by title or company")
        q = list_request(live, lambda: (box.fill(live.state["marker"]), box.press("Enter")))
        assert q.get("q") == live.state["marker"]
        expect(job_row(live, live.state["job_id"])).to_contain_text(company(live.state))
    with live.rec.step("filtered-empty state: no match shows count of hidden jobs + Clear all filters"):
        q = list_request(live, lambda: (box.fill("zzzz-no-such-job"), box.press("Enter")))
        expect(page.get_by_text(re.compile(r"No jobs match your current filters\."))).to_be_visible()
        expect(page.get_by_text(re.compile(r"\d+ active jobs? (is|are) hidden by the filters"))).to_be_visible()
        list_request(live, lambda: page.get_by_role("button", name="Clear all filters").click())
        expect(job_row(live, live.state["job_id"])).to_contain_text(company(live.state))


def test_status_dropdown_persists(withjob):
    live, page = withjob, withjob.page
    list_request(live, lambda: live.goto("/?min_score=0"))
    row = job_row(live, live.state["job_id"])
    with live.rec.step("status dropdown: New -> Applied (PATCH /api/dashboard/jobs/{id})"):
        row.get_by_role("button", name=re.compile(r"^New")).click()
        resp = api_call(live, "PATCH", r"^/api/dashboard/jobs/[^/]+$",
                        lambda: page.get_by_role("button", name="Applied", exact=True).click())
        assert_status(resp, 200, "status change")
        expect(row.get_by_role("button", name=re.compile(r"^Applied"))).to_be_visible()
    with live.rec.step("status persisted (API + reload)"):
        job = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}")
        assert job.get("application_status") == "Applied", job.get("application_status")
        list_request(live, page.reload)
        expect(job_row(live, live.state["job_id"]).get_by_role("button", name=re.compile(r"^Applied"))).to_be_visible()


def test_card_view_shows_job(withjob):
    live, page = withjob, withjob.page
    list_request(live, lambda: live.goto("/?min_score=0"))
    with live.rec.step("card view renders the job card and opens the workspace"):
        page.get_by_title("Card view").click()
        ids = live.state["_last_list"]
        cardv = page.locator("div.shadow-brutal.cursor-pointer").nth(ids.index(live.state["job_id"]))
        expect(cardv).to_contain_text(company(live.state))
        with page.expect_response(lambda r: r.url.endswith(f"/api/dashboard/jobs/{live.state['job_id']}")) as info:
            cardv.get_by_text(TITLE).click()
        assert_status(info.value, 200, "open job from card")
        expect(page).to_have_url(re.compile(rf"/jobs/{re.escape(live.state['job_id'])}$"))
        page.evaluate("localStorage.setItem('naukribaba_view', 'list')")


def test_artifacts_page(withjob):
    live, page = withjob, withjob.page
    with live.rec.step("Artifacts page loads S/A/B tier jobs"):
        resp = api_call(live, "GET", r"^/api/dashboard/jobs$", lambda: live.goto("/artifacts"))
        assert_status(resp, 200, "artifacts list")
        assert "tier=S%2CA%2CB" in resp.url or "tier=S,A,B" in resp.url, resp.url
        expect(page.get_by_role("heading", name="Artifacts")).to_be_visible()
        expect(page.get_by_text("Ready to send")).to_be_visible()
    tier = (live.state.get("job") or {}).get("score_tier")
    with live.rec.step(f"tailored job (tier {tier}) is listed iff tier is S/A/B, and its Resume link is a PDF"):
        entry = page.locator("li, div.border-2").filter(has_text=company(live.state))
        if tier in ("S", "A", "B"):
            expect(entry.first).to_be_visible()
            link = entry.first.locator("a").filter(has_text="Resume")
            expect(link).to_be_visible()
            fetch_pdf(link.get_attribute("href"))
        else:
            expect(entry).to_have_count(0)
            live.rec.note(f"artifacts: job tier {tier!r} is outside S/A/B, so it is correctly not listed")
