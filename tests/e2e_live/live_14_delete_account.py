"""Journey 14 (last): Delete My Account in the UI.

The page tells the user "Your account and all data have been permanently
deleted." This test holds the system to that sentence: after the request, the
data must actually be gone and the account must not sign in. Whatever it finds,
the session fixture's admin cleanup then removes everything."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, require, sign_in


def test_delete_account(live, fresh_page):
    require(live.state, "onboarded", "onboarding did not complete")
    ensure_signed_in(live)
    page, uid = live.page, live.account["id"]
    live.goto("/data-export")
    with live.rec.step("Delete My Account: confirm gate requires typing DELETE"):
        page.get_by_role("button", name="Delete My Account").click()
        confirm = page.get_by_role("button", name="Confirm Delete")
        expect(confirm).to_be_disabled()
        page.get_by_placeholder("Type DELETE to confirm").fill("DELETE")
        expect(confirm).to_be_enabled()
    with live.rec.step("Confirm Delete -> DELETE /api/gdpr/delete -> success, signed out"):
        resp = api_call(live, "DELETE", r"^/api/gdpr/delete$", confirm.click)
        assert_status(resp, 200, "delete account")
        msg = page.get_by_text("Your account and all data have been permanently deleted.")
        expect(msg).to_be_visible()
        expect(page).to_have_url(re.compile(r"/login$"), timeout=20_000)
        live.rec.note(f"DELETE /api/gdpr/delete body: {resp.text()[:200]}")
    with live.rec.step("'permanently deleted' is true: no jobs/résumés remain and the account cannot sign in"):
        users = live.admin.select("users", {"id": f"eq.{uid}", "select": "id,gdpr_deletion_requested_at"})
        jobs = live.admin.count("jobs", {"user_id": f"eq.{uid}"})
        resumes = live.admin.count("user_resumes", {"user_id": f"eq.{uid}"})
        fresh_page.net.allow(400, r"SUPABASE /auth/v1/token$", "a deleted account is expected to be refused")
        login = sign_in(fresh_page.page, live.cfg, live.account["email"], live.state["password"])
        evidence = (f"after the UI said 'permanently deleted': users row={'present' if users else 'gone'}"
                    f"{' (gdpr_deletion_requested_at=' + str(users[0].get('gdpr_deletion_requested_at')) + ')' if users else ''}, "
                    f"jobs={jobs}, user_resumes={resumes}, sign-in with the same password -> HTTP {login.status}")
        live.rec.note(evidence)
        assert not users and jobs == 0 and resumes == 0 and login.status != 200, evidence
