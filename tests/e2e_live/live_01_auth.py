"""Journey 1: the login page — sign in, a rejected password, the Google button."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.e2e_live._live import api_predicate, body_snippet, has_session, sign_in


def test_google_oauth_button_present(live):
    """Google OAuth cannot be automated (a real Google login); assert only that it is offered."""
    with live.rec.step("login page renders + 'Continue with Google' offered (not clicked)"):
        live.goto("/login")
        expect(live.page.get_by_role("button", name="Continue with Google")).to_be_visible()


def test_wrong_password_is_rejected(live, fresh_page):
    page = fresh_page.page
    fresh_page.net.allow(400, r"SUPABASE /auth/v1/token$", "the test submits a wrong password on purpose")
    with live.rec.step("sign in with a wrong password shows an error and no session"):
        resp = sign_in(page, live.cfg, live.account["email"], live.state["password"] + "x")
        assert resp.status == 400, f"wrong password -> {resp.status}: {body_snippet(resp)}"
        expect(page.get_by_text(re.compile("Invalid login credentials", re.I))).to_be_visible()
        assert not has_session(page)
        expect(page).to_have_url(re.compile(r"/login"))


def test_sign_in(live):
    with live.rec.step("sign in with email + password"):
        page = live.page
        with page.expect_response(api_predicate(live.cfg, "GET", r"^/api/profile$"), timeout=60_000) as prof:
            resp = sign_in(page, live.cfg, live.account["email"], live.state["password"])
        assert resp.status == 200, f"token grant -> {resp.status}: {body_snippet(resp)}"
        assert prof.value.status == 200, f"GET /api/profile after sign-in -> {prof.value.status}: {body_snippet(prof.value)}"
        assert has_session(page), "signed in but no Supabase session in localStorage"
    with live.rec.step("a brand-new user is routed to onboarding"):
        expect(page).to_have_url(re.compile(r"/onboarding$"), timeout=30_000)
        expect(page.get_by_role("heading", name="Welcome to NaukriBaba")).to_be_visible()
    live.state["signed_in"] = True
