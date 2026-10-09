"""Journey 3: the GDPR consent banner."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, require

BANNER = "We process your data to match jobs and tailor resumes"


def test_consent_banner_accept(live):
    require(live.state, "onboarded", "onboarding did not complete")
    page = live.page
    ensure_signed_in(live)
    live.goto("/")
    with live.rec.step("consent banner shown to a user with no consent on record"):
        expect(page.get_by_text(BANNER)).to_be_visible()
    with live.rec.step("Accept -> POST /api/gdpr/consent, banner hides"):
        resp = api_call(live, "POST", r"^/api/gdpr/consent$",
                        lambda: page.get_by_role("button", name="Accept", exact=True).click())
        assert_status(resp, 200, "record consent")
        expect(page.get_by_text(BANNER)).to_have_count(0)
    with live.rec.step("consent persisted server-side and survives reload"):
        assert live.api.json("/api/profile").get("gdpr_consent_at"), "gdpr_consent_at still empty"
        page.reload()
        expect(page.get_by_role("heading", name="Job Dashboard")).to_be_visible()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text(BANNER)).to_have_count(0)
    live.state["consented"] = True
