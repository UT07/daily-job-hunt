"""Journey 11: delete a throwaway job from the Dashboard.

The throwaway row is seeded with the service key rather than through Add Job,
so this test judges DELETE and nothing else: creating it through Save & Score
would make a scoring outage read as a delete failure. The deletion itself goes
through the real UI and the real API."""

from __future__ import annotations

import datetime as dt
import secrets

from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, require
from tests.e2e_live.live_04_dashboard import list_request


def test_delete_throwaway_job(live):
    require(live.state, "onboarded", "onboarding did not complete")
    ensure_signed_in(live)
    page = live.page
    uid, marker = live.account["id"], live.state["marker"]
    job_id = secrets.token_hex(8)
    company = f"Throwaway Co {marker}"
    live.admin.insert("jobs", {
        "job_id": job_id, "canonical_hash": job_id, "user_id": uid,
        "title": "Throwaway Role", "company": company,
        "description": "Seeded by tests/e2e_live to exercise Delete. " * 5,
        "location": "Dublin", "source": "manual", "application_status": "New",
        "match_score": 70, "is_expired": False, "first_seen": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    list_request(live, lambda: live.goto(f"/?min_score=0&q={marker}"))
    row = page.locator("table tbody tr").filter(has_text=company)
    with live.rec.step("delete: trash -> Yes -> DELETE /api/dashboard/jobs/{id}"):
        expect(row).to_have_count(1)
        row.get_by_title("Delete job").click()
        # A delete refreshes the stats; wait for that request so the reload
        # below does not abort it mid-flight (which the app logs as an error).
        with page.expect_response(lambda r: r.url.endswith("/api/dashboard/stats")):
            resp = api_call(live, "DELETE", rf"^/api/dashboard/jobs/{job_id}$",
                            lambda: row.get_by_role("button", name="Yes").click())
        assert_status(resp, 200, "delete job")
        expect(row).to_have_count(0)
    with live.rec.step("delete: row gone from the DB and stays gone after reload"):
        assert live.admin.count("jobs", {"user_id": f"eq.{uid}", "job_id": f"eq.{job_id}"}) == 0
        list_request(live, page.reload)
        expect(page.locator("table tbody tr").filter(has_text=company)).to_have_count(0)
        if live.state.get("job_id"):
            r = live.api.get(f"/api/dashboard/jobs/{live.state['job_id']}")
            assert r.status_code == 200, "deleting the throwaway removed the real job too"
