"""The password-reset landing page, in a real browser. Previously uncovered.

`/reset-password` is the page a user lands on from an emailed recovery link —
the one flow where the app is the only thing standing between someone and
being locked out of their account. It had no browser coverage at all.

Two things make it a browser test rather than a jsdom one:

  * both password fields carry `minLength={8}`, so the JS branch
    `if (password.length < 8)` is unreachable in a real browser — native
    constraint validation cancels the submit before React's handler runs.
    jsdom does not implement constraint validation, so a unit test asserting
    on that error message passes against behaviour the user never sees. Same
    trap as `test_signup_with_a_short_password_never_reaches_gotrue`.
  * the page's three states are chosen by guards that run in a fixed order
    against live auth state, and `signOut()` mutates that state mid-submit.
    Which screen a user actually ends on is a property of the running app,
    not of the component in isolation.

What this proves and does not: per the suite's conftest every HTTP response is
a fixture, so these tests prove the shipped bundle renders, routes, validates
and issues the right GoTrue calls. They prove nothing about GoTrue itself.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    register_dashboard_routes,
    register_shell_routes,
    sign_in,
)

LONG = "a-long-enough-password"


def _put_user_calls(auth_stub) -> list[str]:
    """Only the password write.

    supabase-js also issues `GET /auth/v1/user` while restoring a session, so
    an assertion on "did /auth/v1/user get hit" is true before the form is
    even submitted. The method is the whole signal here.
    """
    return [c for c in auth_stub.calls if c.startswith("PUT ") and c.endswith("/auth/v1/user")]


def _open_with_session(page, base_url, api_stub):
    """Sign in through the UI, then open the reset page.

    The route itself is public — it is outside AppLayout's auth gate — but the
    page gates its own form on `user`, because a recovery link's whole purpose
    is to establish a session. Without one it renders "Link expired", so a
    bare goto tests the wrong branch. Signing in lands on the dashboard first,
    which is why its routes are stubbed too.
    """
    register_shell_routes(api_stub)
    register_dashboard_routes(api_stub)
    sign_in(page)
    page.wait_for_url("**/", timeout=15_000)
    page.goto(f"{base_url}/reset-password")


@pytest.mark.usefixtures("auth_stub")
class TestWithoutARecoverySession:
    def test_an_expired_link_says_so_and_offers_a_way_back(self, page, api_stub):
        """No session means the link was invalid or has expired. The dead end
        matters: a user who cannot get back to sign-in from here has no route
        into the app at all."""
        page.goto("/reset-password")

        expect(page.get_by_role("heading", name="Link expired")).to_be_visible()
        page.get_by_role("button", name="Back to sign in").click()
        expect(page).to_have_url(re.compile(r"/login$"))


@pytest.mark.usefixtures("auth_stub")
class TestValidationGates:
    def test_a_short_password_never_reaches_gotrue(self, page, base_url, api_stub, auth_stub):
        """Asserted on `validity.tooShort` and on no write leaving the page,
        not on the error message — see this module's docstring for why that
        message is unreachable."""
        _open_with_session(page, base_url, api_stub)

        field = page.get_by_label("New password")
        expect(field).to_be_visible()
        field.fill("short")
        page.get_by_label("Confirm password").fill("short")
        page.get_by_role("button", name="Update password").click()

        assert field.evaluate("el => el.validity.tooShort") is True
        assert _put_user_calls(auth_stub) == [], (
            f"a short password escaped the gate: {auth_stub.calls}")

    def test_mismatched_passwords_are_refused_before_any_write(self, page, base_url, api_stub, auth_stub):
        """Both are long enough to clear native validation, so this one DOES
        reach `handleSubmit` — which is what makes its message assertable
        where the length one is not."""
        _open_with_session(page, base_url, api_stub)

        page.get_by_label("New password").fill(LONG)
        page.get_by_label("Confirm password").fill(f"{LONG}-but-different")
        page.get_by_role("button", name="Update password").click()

        expect(page.get_by_text("Passwords do not match.")).to_be_visible()
        assert _put_user_calls(auth_stub) == [], (
            f"a mismatched pair was written anyway: {auth_stub.calls}")


@pytest.mark.usefixtures("auth_stub")
class TestASuccessfulReset:
    def test_a_matching_pair_is_written_to_gotrue(self, page, base_url, api_stub, auth_stub):
        _open_with_session(page, base_url, api_stub)

        page.get_by_label("New password").fill(LONG)
        page.get_by_label("Confirm password").fill(LONG)
        page.get_by_role("button", name="Update password").click()

        page.wait_for_timeout(1500)
        assert len(_put_user_calls(auth_stub)) == 1, (
            f"the new password was never sent: {auth_stub.calls}")

    def test_a_successful_reset_does_not_report_itself_as_expired(self, page, base_url, api_stub, auth_stub):
        """The one assertion here that is about the user rather than the wire.

        `handleSubmit` does `setDone(true)` and then `await signOut()`, and
        the render guards are ordered `!user` before `done` — so once the
        sign-out clears the session the page falls through to "Link expired".
        The password HAS been changed at that point. Telling someone their
        link expired immediately after it worked is the worst available
        outcome: the natural next move is to request another reset, which
        invalidates nothing and teaches them the feature is broken.
        """
        _open_with_session(page, base_url, api_stub)

        page.get_by_label("New password").fill(LONG)
        page.get_by_label("Confirm password").fill(LONG)
        page.get_by_role("button", name="Update password").click()

        expect(page.get_by_role("heading", name="Password updated")).to_be_visible()
        expect(page.get_by_role("heading", name="Link expired")).not_to_be_visible()
        page.wait_for_timeout(1500)
        expect(page.get_by_role("heading", name="Link expired")).not_to_be_visible()
