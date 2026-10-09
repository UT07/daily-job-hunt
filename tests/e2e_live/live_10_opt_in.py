"""Journey 10: costly or side-effecting features, each behind an opt-in flag.

Default OFF. Each is reported SKIPPED(opt-in) unless its E2E_LIVE_* flag is 1:

  E2E_LIVE_RUN_PIPELINE   Dashboard > Run Pipeline (daily Step Functions, paid
                          scrapers, notification email)
  E2E_LIVE_FIND_CONTACTS  Workspace > Contacts > Find Contacts (paid Apify)
  E2E_LIVE_REGENERATE     Workspace > Resume > Regenerate, then Restore v1
                          (a second full single-job pipeline run)
  E2E_LIVE_COVER_LETTER   Add Job > Cover Letter (a second full run-single)
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import (
    api_call,
    assert_status,
    ensure_signed_in,
    fetch_pdf,
    opt_in,
    require,
)


def test_run_pipeline(live):
    opt_in(live.cfg, "E2E_LIVE_RUN_PIPELINE")
    ensure_signed_in(live)
    page = live.page
    live.goto("/")
    with live.rec.step("Run Pipeline -> POST /api/pipeline/run, poll to a terminal state"):
        resp = api_call(live, "POST", r"^/api/pipeline/run$",
                        lambda: page.get_by_role("button", name="▶ Run Pipeline").click())
        assert_status(resp, {200, 202}, "start daily pipeline")
        expect(page.get_by_role("button", name="▶ Run Pipeline")).to_be_enabled(timeout=45 * 60 * 1000)


def test_find_contacts(live):
    opt_in(live.cfg, "E2E_LIVE_FIND_CONTACTS")
    job_id = require(live.state, "job_id", "Tailor Resume did not produce a job")
    ensure_signed_in(live)
    page = live.page
    live.goto(f"/jobs/{job_id}")
    page.get_by_role("button", name="Contacts", exact=True).click()
    with live.rec.step("Find Contacts -> contacts listed and persisted"):
        resp = api_call(live, "POST", r"/find-contacts$",
                        lambda: page.get_by_role("button", name="Find Contacts").click(), timeout_s=120)
        assert_status(resp, {200, 202}, "find contacts")
        expect(page.get_by_text(re.compile(r"^[1-9]\d* Contacts? ·"))).to_be_visible(timeout=600_000)


def test_regenerate_and_restore(live):
    opt_in(live.cfg, "E2E_LIVE_REGENERATE")
    job_id = require(live.state, "job_id", "Tailor Resume did not produce a job")
    ensure_signed_in(live)
    page = live.page
    live.goto(f"/jobs/{job_id}")
    page.get_by_role("button", name="Resume", exact=True).click()
    with live.rec.step("Regenerate résumé -> new version, real PDF"):
        resp = api_call(live, "POST", r"^/api/pipeline/re-tailor/",
                        lambda: page.get_by_role("button", name="Regenerate").click())
        assert_status(resp, {200, 202}, "re-tailor")
        expect(page.get_by_role("button", name="Regenerate")).to_be_enabled(timeout=15 * 60 * 1000)
        expect(page.get_by_text(re.compile("Regenerate failed"))).to_have_count(0)
        fetch_pdf(page.get_by_title("Resume PDF Preview").get_attribute("src"))
    with live.rec.step("Restore an earlier version"):
        older = page.get_by_role("button", name=re.compile(r"^v1 \("))
        expect(older).to_be_visible()
        older.click()
        resp = api_call(live, "POST", r"/restore$", lambda: page.get_by_role("button", name="Restore").click())
        assert_status(resp, 200, "restore version")


def test_add_job_cover_letter(live):
    opt_in(live.cfg, "E2E_LIVE_COVER_LETTER")
    require(live.state, "onboarded", "onboarding did not complete")
    from tests.e2e_live.live_06_add_job import fill_form

    ensure_signed_in(live)
    page = live.page
    live.goto("/add-job")
    fill_form(page, live.state)
    with live.rec.step("Add Job > Cover Letter -> run-single -> card with a PDF"):
        resp = api_call(live, "POST", r"^/api/pipeline/run-single$",
                        lambda: page.get_by_role("button", name="Cover Letter").click())
        assert_status(resp, {200, 202}, "cover letter run-single")
        card = page.locator("div.animate-fade-in").filter(has_text="Cover Letter")
        expect(card.first).to_be_visible(timeout=15 * 60 * 1000)
        link = card.first.locator("a").filter(has_text=re.compile("PDF", re.I))
        if not link.count():
            pytest.fail(f"cover letter card has no PDF link: {card.first.inner_text()[:300]}")
        fetch_pdf(link.first.get_attribute("href"))
