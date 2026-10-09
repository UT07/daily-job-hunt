"""Journey 14 (last): Delete My Account in the UI.

Since #217 the page says "Deletion requested. Your account and data are marked
for permanent deletion after a 30-day grace period ... You are being signed
out." (DELETE /api/gdpr/delete is a soft delete; scripts/data_retention.py does
the hard delete later.) This test holds the system to exactly those claims: the
request is recorded server-side and the browser is signed out. The session
fixture's admin cleanup then removes everything."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, has_session, require, sign_in


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
        msg = page.get_by_text(re.compile(r"^Deletion requested\. Your account and data are marked"))
        expect(msg).to_be_visible()
        expect(page).to_have_url(re.compile(r"/login$"), timeout=20_000)
        live.rec.note(f"DELETE /api/gdpr/delete body: {resp.text()[:200]}")
    with live.rec.step("deletion is recorded server-side and the browser is signed out"):
        users = live.admin.select("users", {"id": f"eq.{uid}", "select": "id,gdpr_deletion_requested_at"})
        assert users and users[0].get("gdpr_deletion_requested_at"), f"no deletion request recorded: {users}"
        assert not has_session(page), "still signed in after 'You are being signed out.'"
        jobs = live.admin.count("jobs", {"user_id": f"eq.{uid}"})
        fresh_page.net.allow(400, r"SUPABASE /auth/v1/token$", "a deleted account may be refused")
        login = sign_in(fresh_page.page, live.cfg, live.account["email"], live.state["password"])
        live.rec.note(f"after 'Deletion requested': gdpr_deletion_requested_at="
                      f"{users[0]['gdpr_deletion_requested_at']}, jobs still stored={jobs} (expected during the "
                      f"grace period), same password signs in -> HTTP {login.status}")
