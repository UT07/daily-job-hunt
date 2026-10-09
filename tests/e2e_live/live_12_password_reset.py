"""Journey 12: password reset via a real recovery link.

The link is minted with the admin `generate_link` (type=recovery) instead of
the "Forgot password?" form, so no email is sent. Everything after that — the
GoTrue verify redirect, the app's /reset-password page, the password update and
signing in with the new password — is the real flow."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.e2e_live._live import has_session, require, sign_in


def test_forgot_password_form_offered(live, fresh_page):
    # A signed-out browser: the shared context is signed in and /login would
    # redirect it to the dashboard.
    page = fresh_page.page
    with live.rec.step("login: 'Forgot password?' opens the reset form (not submitted: it sends email)"):
        page.goto(live.url("/login"))
        page.get_by_role("button", name="Forgot password?").click()
        expect(page.get_by_role("heading", name="Reset your password")).to_be_visible()
        expect(page.get_by_role("button", name="Send reset link")).to_be_visible()


def test_reset_password_via_recovery_link(live, fresh_page):
    require(live.state, "onboarded", "onboarding did not complete")
    page = fresh_page.page
    prev = live.state["password"]
    new = prev[:-2] + "Rx"
    with live.rec.step("recovery link opens the app's 'Set new password' page"):
        link = live.admin.recovery_link(live.account["email"], f"{live.cfg.site_url}/reset-password")
        page.goto(link, wait_until="domcontentloaded")
        expect(page).to_have_url(re.compile(r"/reset-password"), timeout=30_000)
        expect(page.get_by_role("heading", name="Set new password")).to_be_visible()
    with live.rec.step("set new password -> Supabase PUT /auth/v1/user -> 'Password updated'"):
        page.locator("#new-password").fill(new)
        page.locator("#confirm-password").fill(new)
        # The page signs out after the update; wait for that request too, or
        # navigating away aborts it mid-flight ("Failed to fetch").
        with page.expect_response(lambda r: "/auth/v1/logout" in r.url, timeout=30_000), \
                page.expect_response(lambda r: r.url.startswith(f"{live.cfg.supabase_url}/auth/v1/user")
                                     and r.request.method == "PUT") as info:
            page.get_by_role("button", name="Update password").click()
        assert info.value.status == 200, f"update password -> {info.value.status}: {info.value.text()[:200]}"
        expect(page.get_by_role("heading", name="Password updated")).to_be_visible()
        live.state["password"] = new
    with live.rec.step("old password rejected, new password signs in"):
        fresh_page.net.allow(400, r"SUPABASE /auth/v1/token$", "the old password is expected to be refused")
        old = sign_in(page, live.cfg, live.account["email"], prev)
        assert old.status == 400, f"old password still accepted ({old.status})"
        ok = sign_in(page, live.cfg, live.account["email"], new)
        assert ok.status == 200, f"new password -> {ok.status}"
        expect(page).not_to_have_url(re.compile(r"/login"))
        assert has_session(page)
