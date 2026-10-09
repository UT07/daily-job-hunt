"""Journey 2: onboarding — upload a real .tex résumé, profile, preferences, Complete Setup."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from tests.e2e_live._live import REPO_ROOT, api_call, assert_status, ensure_signed_in, require

RESUME = REPO_ROOT / "resumes" / "sre_devops.tex"
PROFILE = {
    "full_name": "E2E Tester",
    "phone": "+353 85 000 0000",
    "location": "Dublin, Ireland",
    "linkedin_url": "https://www.linkedin.com/in/e2e-tester",
    "visa_status": "Stamp 1G",
    "notice": "1 month",
}


def test_onboarding_complete(live):
    require(live.state, "signed_in", "sign-in failed")
    page = live.page
    ensure_signed_in(live)
    live.goto("/onboarding")

    with live.rec.step("onboarding: welcome -> Next"):
        expect(page.get_by_role("heading", name="Welcome to NaukriBaba")).to_be_visible()
        page.get_by_role("button", name="Next →").click()
        expect(page.get_by_role("heading", name="Upload Your Resume")).to_be_visible()

    with live.rec.step("onboarding: upload real .tex résumé (Upload & Parse)"):
        page.locator("input[type=file]").set_input_files(str(RESUME))
        expect(page.get_by_text("sre_devops.tex")).to_be_visible()
        resp = api_call(live, "POST", r"^/api/resumes/upload$",
                        lambda: page.get_by_role("button", name="Upload & Parse").click(), timeout_s=90)
        assert_status(resp, 200, "résumé upload")
        expect(page.get_by_text("Resume parsed successfully!")).to_be_visible()

    with live.rec.step("onboarding: résumé persisted verbatim (user_resumes.tex_content)"):
        rows = live.admin.select("user_resumes", {"user_id": f"eq.{live.account['id']}",
                                                  "select": "id,label,tex_content"})
        assert len(rows) == 1, f"expected 1 user_resumes row, found {len(rows)}"
        stored, original = rows[0]["tex_content"] or "", RESUME.read_text()
        # 2026-09-29 the pipeline stored 9% of an uploaded document; a .tex is
        # stored verbatim, so anything short of the full text is that bug.
        assert stored.strip() == original.strip(), (
            f"stored {len(stored)} chars of a {len(original)}-char .tex upload")
        live.state["resume_id"] = rows[0]["id"]

    with live.rec.step("onboarding: profile step (two-word name + required fields)"):
        page.get_by_role("button", name="Next →").click()
        expect(page.get_by_role("heading", name="Complete Your Profile")).to_be_visible()
        page.locator("#full-name").fill(PROFILE["full_name"])
        page.locator("#phone").fill(PROFILE["phone"])
        page.locator("#location").fill(PROFILE["location"])
        page.locator("#linkedin").fill(PROFILE["linkedin_url"])
        page.locator("#visa-status").fill(PROFILE["visa_status"])
        page.get_by_role("button", name="+ Add authorization").click()
        page.get_by_placeholder("Country").fill("Ireland")
        page.locator("select").filter(has_text="Select status").select_option("stamp_1g")
        page.get_by_test_id("notice-period-select").select_option(PROFILE["notice"])

    with live.rec.step("onboarding: preferences step"):
        page.get_by_role("button", name="Next →").click()
        expect(page.get_by_role("heading", name="Search Preferences")).to_be_visible()
        q = page.get_by_placeholder("e.g. Backend Engineer, Python Developer")
        q.fill("Site Reliability Engineer")
        q.press("Enter")
        loc = page.get_by_placeholder("e.g. Dublin, Remote, London")
        loc.fill("Dublin")
        loc.press("Enter")
        expect(page.locator("span").filter(has_text=re.compile(r"^Site Reliability Engineer")).first).to_be_visible()
        expect(page.get_by_test_id("onboarding-blocked-reason")).to_have_count(0)

    with live.rec.step("onboarding: Complete Setup (PUT profile + PUT search-config)"):
        with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/search-config"),
                                  timeout=60_000) as cfg_resp:
            prof = api_call(live, "PUT", r"^/api/profile$",
                            lambda: page.get_by_role("button", name="Complete Setup").click())
        assert_status(prof, 200, "PUT /api/profile")
        assert_status(cfg_resp.value, 200, "PUT /api/search-config")
        done = page.get_by_role("heading", name="You're All Set!")
        dash = page.get_by_role("heading", name="Job Dashboard")
        expect(done.or_(dash)).to_be_visible()
        skipped_done = dash.is_visible()

    with live.rec.step("onboarding: lands on the Dashboard"):
        if not skipped_done:
            page.get_by_role("button", name="Go to Dashboard →").click()
        expect(page).to_have_url(re.compile(r"^[^?#]+/$"))
        expect(dash).to_be_visible()

    with live.rec.step("onboarding: persisted (GET /api/profile + /api/search-config)"):
        p = live.api.json("/api/profile")
        assert p["full_name"] == PROFILE["full_name"], p["full_name"]
        assert p["phone"] == PROFILE["phone"]
        assert p["linkedin_url"] == PROFILE["linkedin_url"]
        assert p["visa_status"] == PROFILE["visa_status"]
        assert p["notice_period_text"] == PROFILE["notice"]
        assert p["work_authorizations"] == {"Ireland": "stamp_1g"}, p["work_authorizations"]
        assert p["onboarding_completed_at"], "onboarding_completed_at not set"
        assert p.get("profile_complete") is True, f"profile_complete={p.get('profile_complete')} missing={p.get('missing_required_fields')}"
        sc = live.api.json("/api/search-config")
        assert "Site Reliability Engineer" in (sc.get("queries") or []), sc.get("queries")
        assert "Dublin" in (sc.get("locations") or []), sc.get("locations")
    live.state["onboarded"] = True
    if skipped_done:
        # Judged last so the rest of the journey is not blocked by it.
        with live.rec.step("onboarding: 'You're All Set!' step is shown after Complete Setup"):
            raise AssertionError(
                "Complete Setup jumped straight to the Dashboard: the wizard's "
                "'already onboarded -> navigate(/)' effect fired on the profile refetch "
                "before the Done step rendered (intermittent; seen 1 of 4 runs)")
