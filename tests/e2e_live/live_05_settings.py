"""Journey 5: Settings — profile save + reload-persist, preferences, sources,
the résumé list, and change password then sign in with it."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import (
    REPO_ROOT,
    api_call,
    assert_status,
    ensure_signed_in,
    has_session,
    require,
    sign_in,
)


def card(page, heading: str):
    return page.get_by_role("heading", name=heading, exact=True).locator(
        "xpath=ancestor::div[.//button][1]")


@pytest.fixture()
def settings(live):
    require(live.state, "onboarded", "onboarding did not complete")
    ensure_signed_in(live)
    page = live.page
    with page.expect_response(lambda r: r.url.endswith("/api/resumes") and r.request.method == "GET",
                              timeout=60_000) as res:
        live.goto("/settings")
    assert_status(res.value, 200, "GET /api/resumes")
    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
    # Profile load must finish before editing: the form refuses to save a
    # blank form over the stored profile until then.
    expect(page.get_by_placeholder("Utkarsh Singh")).not_to_have_value("")
    return live


def test_profile_form_shows_stored_values(settings):
    live, page = settings, settings.page
    with live.rec.step("settings: loaded form shows the stored profile (name, work auth, notice period)"):
        stored = live.api.json("/api/profile")
        expect(page.get_by_placeholder("Utkarsh Singh")).to_have_value(stored["full_name"])
        expect(page.get_by_placeholder("Country (e.g. Ireland)")).to_have_value("Ireland")
        # The stored notice period must be what the dropdown shows; a form that
        # displays "Select..." over a stored value invites the user to "fix" it.
        expect(page.get_by_test_id("notice-period-select")).to_have_value(stored["notice_period_text"])


def test_profile_save_and_reload_persists(settings):
    live, page = settings, settings.page
    prof = card(page, "Profile")
    with live.rec.step("settings: edit phone + location, Save Changes -> PUT /api/profile"):
        page.get_by_placeholder("+353 85 123 4567").fill("+353 85 111 2222")
        prof.get_by_placeholder("Dublin, Ireland").fill("Cork, Ireland")
        page.get_by_placeholder("https://yoursite.com").fill("https://e2e.example.com")
        resp = api_call(live, "PUT", r"^/api/profile$",
                        lambda: prof.get_by_role("button", name="Save Changes").click())
        assert_status(resp, 200, "save profile")
        expect(prof.get_by_text("Profile saved.")).to_be_visible()
    with live.rec.step("settings: profile edits survive a reload (UI + API)"):
        page.reload()
        expect(page.get_by_placeholder("Utkarsh Singh")).to_have_value("E2E Tester")
        expect(page.get_by_placeholder("+353 85 123 4567")).to_have_value("+353 85 111 2222")
        expect(card(page, "Profile").get_by_placeholder("Dublin, Ireland")).to_have_value("Cork, Ireland")
        p = live.api.json("/api/profile")
        assert (p["phone"], p["location"], p["website"]) == ("+353 85 111 2222", "Cork, Ireland", "https://e2e.example.com"), p
        # Saving must not wipe fields the form did not touch (PR #212's bug).
        assert p["full_name"] == "E2E Tester" and p["visa_status"] == "Stamp 1G", p
        assert p["work_authorizations"] == {"Ireland": "stamp_1g"}, p["work_authorizations"]
        assert p.get("profile_complete") is True, p.get("missing_required_fields")


def test_preferences_save_and_reload_persists(settings):
    live, page = settings, settings.page
    prefs = card(page, "Search Preferences")
    with live.rec.step("settings: add a search query, Save -> PUT /api/search-config"):
        # The placeholder disappears once a tag exists, so address the input itself.
        tag = prefs.locator("input[type=text]").first
        expect(prefs.locator("span").filter(has_text=re.compile(r"^Site Reliability Engineer")).first).to_be_visible()
        tag.fill("Platform Engineer")
        tag.press("Enter")
        prefs.locator("input[type=number]").first.fill("14")
        resp = api_call(live, "PUT", r"^/api/search-config$",
                        lambda: prefs.get_by_role("button", name="Save Changes").click())
        assert_status(resp, 200, "save preferences")
        expect(prefs.get_by_text("Search preferences saved.")).to_be_visible()
    with live.rec.step("settings: preferences survive a reload (UI + API)"):
        page.reload()
        prefs = card(page, "Search Preferences")
        expect(prefs.locator("span").filter(has_text=re.compile(r"^Platform Engineer")).first).to_be_visible()
        sc = live.api.json("/api/search-config")
        assert {"Site Reliability Engineer", "Platform Engineer"} <= set(sc.get("queries") or []), sc.get("queries")
        assert sc.get("days_back") == 14, sc.get("days_back")


def test_job_sources_toggle_persists(settings):
    live, page = settings, settings.page
    src = card(page, "Job Sources")
    row = src.locator("div.flex.items-center.justify-between").filter(has_text="HN Hiring")
    with live.rec.step("settings: toggle HN Hiring off, Save Sources -> PUT /api/search-config"):
        before = live.api.json("/api/search-config").get("enabled_sources")
        row.get_by_role("button").click()
        with page.expect_request(lambda r: r.url.endswith("/api/search-config") and r.method == "PUT") as req:
            resp = api_call(live, "PUT", r"^/api/search-config$",
                            lambda: src.get_by_role("button", name="Save Sources").click())
        assert_status(resp, 200, "save sources")
        sent = (req.value.post_data_json or {}).get("enabled_sources")
        assert sent and "hn" not in sent and "linkedin" in sent, f"UI sent enabled_sources={sent}"
        expect(src.get_by_text("Job sources saved.")).to_be_visible()
    with live.rec.step("settings: sources persisted (hn removed, others kept)"):
        sc = live.api.json("/api/search-config")
        after = sc.get("enabled_sources") or []
        assert "hn" not in after, after
        assert "linkedin" in after, (f"UI sent {sent}, PUT answered {resp.text()[:200]}, "
                                     f"GET now returns enabled_sources={sc.get('enabled_sources', '<key absent>')} (before: {before})")
        page.reload()
        src = card(page, "Job Sources")
        expect(src.locator("div.flex.items-center.justify-between").filter(has_text="HN Hiring")
               .get_by_role("button")).to_have_class(re.compile(r"bg-white"))


def test_resume_list_and_reupload(settings):
    live, page = settings, settings.page
    res = card(page, "Resumes")
    with live.rec.step("settings: résumé list shows the onboarding upload"):
        expect(res.get_by_text("sre_devops.tex")).to_be_visible()
    with live.rec.step("settings: re-upload the same .tex via Upload & Parse"):
        res.locator("input[type=file]").set_input_files(str(REPO_ROOT / "resumes" / "sre_devops.tex"))
        resp = api_call(live, "POST", r"^/api/resumes/upload$",
                        lambda: res.get_by_role("button", name="Upload & Parse").click(), timeout_s=90)
        assert_status(resp, 200, "re-upload résumé")
        expect(res.get_by_text("Resume uploaded and parsed successfully.")).to_be_visible()
        rows = live.api.json("/api/resumes")["resumes"]
        assert len(rows) == 1, f"re-uploading the same file created {len(rows)} rows"
    with live.rec.step("settings: a résumé upload does not overwrite the profile the user typed"):
        p = live.api.json("/api/profile")
        clobbered = {k: p.get(k) for k, mine in (("full_name", "E2E Tester"), ("location", "Cork, Ireland"),
                                                ("phone", "+353 85 111 2222")) if p.get(k) != mine}
        assert not clobbered, (f"after uploading a résumé the profile changed to {clobbered} "
                               "(values parsed from the résumé replaced what the user entered)")


def test_change_password_then_sign_in(settings, fresh_page):
    live, page = settings, settings.page
    pw = card(page, "Change Password")
    new = live.state["password"][:-2] + "Zq"
    with live.rec.step("settings: Update Password -> Supabase PUT /auth/v1/user"):
        page.locator("#new-password").fill(new)
        page.locator("#confirm-new-password").fill(new)
        with page.expect_response(lambda r: r.url.startswith(f"{live.cfg.supabase_url}/auth/v1/user")
                                  and r.request.method == "PUT") as info:
            pw.get_by_role("button", name="Update Password").click()
        assert info.value.status == 200, f"update password -> {info.value.status}"
        live.state["password"] = new
    with live.rec.step("sign in with the NEW password in a clean browser"):
        resp = sign_in(fresh_page.page, live.cfg, live.account["email"], new)
        assert resp.status == 200, f"sign in with new password -> {resp.status}"
        expect(fresh_page.page).not_to_have_url(re.compile(r"/login"))
        assert has_session(fresh_page.page)
    with live.rec.step("settings: 'Password updated successfully' confirmation is shown"):
        profile_gets = len(live.net.find("GET", r"^API /api/profile$"))
        msg = card(page, "Change Password").get_by_text("Password updated successfully")
        try:
            expect(msg).to_be_visible(timeout=10_000)
        except AssertionError as e:
            raise AssertionError(
                "password changed (PUT 200, new password signs in) but no confirmation is shown; "
                f"the page issued {profile_gets} GET /api/profile (a remount on the USER_UPDATED "
                "auth event would discard the message)") from e
