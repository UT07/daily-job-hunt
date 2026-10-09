"""Journey 9: Résumé Studio (edit a bullet, blur-compile, Suggest improvements,
Apply/Undo/Dismiss, Recompile) and the workspace Editor tab (Save & Compile)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, fetch_pdf, require
from tests.e2e_live.live_04_dashboard import job_row, list_request

EDIT_MARK = " (verified in e2e)"
COMPILE_TIMEOUT_S = 240


@pytest.fixture()
def studio(live):
    job_id = require(live.state, "job_id", "Tailor Resume did not produce a job")
    ensure_signed_in(live)
    page = live.page
    list_request(live, lambda: live.goto("/?min_score=0"))
    row = job_row(live, job_id)
    with live.rec.step("open Studio from the dashboard row"):
        resp = api_call(live, "GET", rf"^/api/dashboard/jobs/{re.escape(job_id)}/sections$",
                        lambda: row.get_by_role("button", name="Studio").click())
        assert_status(resp, 200, "load sections")
        expect(page).to_have_url(re.compile(rf"/jobs/{re.escape(job_id)}/studio$"))
        expect(page.get_by_role("heading", level=1)).to_contain_text("Site Reliability Engineer")
    return live


def _compile(live, action):
    """Trigger a compile and wait for the PDF pane to show a NEW pdf url."""
    page = live.page
    frame = page.get_by_title("Resume preview")
    before = frame.get_attribute("src") if frame.count() else None
    resp = api_call(live, "POST", r"/sections$", action, timeout_s=60)
    live.net.allow({500, 502, 503, 504}, r"/sections$", "asserted explicitly")
    assert_status(resp, {200, 202}, "compile sections")
    expect(page.get_by_role("button", name="Recompile")).to_be_enabled(timeout=COMPILE_TIMEOUT_S * 1000)
    alert = page.get_by_role("alert")
    if alert.count():
        pytest.fail(f"compile reported an error: {alert.first.inner_text()}")
    expect(frame).not_to_have_attribute("src", before or "", timeout=COMPILE_TIMEOUT_S * 1000)
    src = frame.get_attribute("src")
    fetch_pdf(src)
    return src


def test_studio_edit_bullet_blur_compiles(studio):
    live, page = studio, studio.page
    bullet = page.get_by_role("textbox", name=re.compile(r" bullet 1$")).first
    with live.rec.step("studio: edit a bullet, blur -> compile -> new PDF"):
        expect(bullet).to_be_visible()
        original = bullet.input_value()
        bullet.fill(original + EDIT_MARK)
        expect(page.get_by_text(re.compile(r"1 change not yet compiled"))).to_be_visible()
        _compile(live, lambda: page.keyboard.press("Tab"))
        expect(page.get_by_text(re.compile("not yet compiled"))).to_have_count(0)
    with live.rec.step("studio: edited bullet persisted (GET sections after reload)"):
        data = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}/sections")
        bullets = [b for e in (data.get("sections") or {}).get("experience", []) for b in e.get("bullets", [])]
        assert any(EDIT_MARK.strip() in b for b in bullets), "the compiled edit was not saved to the sections"
        page.reload()
        expect(page.get_by_role("textbox", name=re.compile(r" bullet 1$")).first).to_have_value(
            re.compile(re.escape(EDIT_MARK.strip())))
    live.state["studio_ok"] = True


def test_studio_suggestions_apply_undo_dismiss_recompile(studio):
    live, page = studio, studio.page
    panel = page.get_by_test_id("suggestions")
    with live.rec.step("studio: Suggest improvements -> POST /suggestions"):
        resp = api_call(live, "POST", r"/suggestions$",
                        lambda: panel.get_by_role("button", name="Suggest improvements").click(), timeout_s=120)
        live.net.allow({500, 502, 503, 504}, r"/suggestions$", "asserted explicitly")
        assert_status(resp, {200, 202}, "suggestions")
        expect(panel.get_by_role("button", name="Re-analyse")).to_be_visible(timeout=240_000)
        alert = panel.get_by_role("alert")
        if alert.count():
            pytest.fail(f"suggestions error: {alert.inner_text()}")
    items = panel.locator("li[data-testid^=suggestion-]")
    n = items.count()
    live.rec.note(f"studio: {n} suggestion(s) returned")
    if n == 0:
        expect(panel.get_by_text("Nothing to suggest")).to_be_visible()
        pytest.skip("BLOCKED: the model returned 0 suggestions, so Apply/Undo/Dismiss cannot be exercised")
    first = items.nth(0)
    sid = first.get_attribute("data-testid")
    with live.rec.step("studio: Apply a suggestion -> marked applied, draft changes"):
        first.get_by_role("button", name="Apply", exact=True).click()
        expect(panel.get_by_test_id(sid)).to_have_attribute("data-status", "applied")
        expect(page.get_by_text(re.compile("not yet compiled"))).to_be_visible()
    with live.rec.step("studio: Undo restores it"):
        panel.get_by_test_id(sid).get_by_role("button", name="Undo").click()
        expect(panel.get_by_test_id(sid)).not_to_have_attribute("data-status", "applied")
    with live.rec.step("studio: re-Apply then Recompile -> new PDF"):
        panel.get_by_test_id(sid).get_by_role("button", name="Apply", exact=True).click()
        _compile(live, lambda: page.get_by_role("button", name="Recompile").click())
    if n > 1:
        other = items.nth(1)
        oid = other.get_attribute("data-testid")
        with live.rec.step("studio: Dismiss removes a suggestion"):
            other.get_by_role("button", name="Dismiss").click()
            expect(panel.get_by_test_id(oid)).to_have_count(0)
    else:
        live.rec.add("studio: Dismiss removes a suggestion", "SKIPPED",
                     "only one suggestion returned and it was applied")


def test_editor_tab_save_and_compile(live):
    job_id = require(live.state, "job_id", "Tailor Resume did not produce a job")
    ensure_signed_in(live)
    page = live.page
    live.goto(f"/jobs/{job_id}")
    with live.rec.step("editor tab: loads sections"):
        resp = api_call(live, "GET", r"/sections$",
                        lambda: page.get_by_role("button", name="Editor", exact=True).click())
        assert_status(resp, 200, "editor sections")
        expect(page.get_by_text("Edit Sections")).to_be_visible()
    with live.rec.step("editor tab: Save & Compile -> success + PDF"):
        resp = api_call(live, "POST", r"/sections$",
                        lambda: page.get_by_role("button", name="Save & Compile").click(), timeout_s=60)
        live.net.allow({500, 502, 503, 504}, r"/sections$", "asserted explicitly")
        assert_status(resp, {200, 202}, "editor compile")
        ok = page.get_by_text("Saved and compiled successfully.")
        err = page.get_by_text(re.compile(r"^Error: "))
        expect(ok.or_(err)).to_be_visible(timeout=COMPILE_TIMEOUT_S * 1000)
        if err.count():
            pytest.fail(f"editor compile error: {err.inner_text()}")
        fetch_pdf(page.get_by_title("Resume PDF Preview").get_attribute("src"))
