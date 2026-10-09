"""Journey 8: the Job Workspace — every tab.

Overview (status timeline, edit location + apply URL, flag score), Research,
Interview Prep, Contacts > Email Composer, Resume (iframe + Download), Cover
Letter. Find Contacts and Regenerate are opt-in (live_10_opt_in.py)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import api_call, assert_status, ensure_signed_in, fetch_pdf, require


@pytest.fixture()
def ws(live):
    job_id = require(live.state, "job_id", "Tailor Resume did not produce a job")
    ensure_signed_in(live)
    resp = api_call(live, "GET", rf"^/api/dashboard/jobs/{re.escape(job_id)}$",
                    lambda: live.goto(f"/jobs/{job_id}"))
    assert_status(resp, 200, "load job workspace")
    expect(live.page.get_by_role("button", name="Overview", exact=True)).to_be_visible()
    return live


def tab(page, name):
    page.get_by_role("button", name=name, exact=True).click()


def test_overview_status_timeline(ws):
    live, page = ws, ws.page
    with live.rec.step("overview: Update Status -> Interview + notes (POST timeline)"):
        page.get_by_role("button", name="Update Status").click()
        form = page.locator("form").filter(has_text="New Status")
        form.locator("select").select_option("Interview")
        form.locator("textarea").fill("e2e: first-round call booked")
        resp = api_call(live, "POST", r"/timeline$", lambda: form.get_by_role("button", name="Save").click())
        assert_status(resp, {200, 201}, "add timeline event")
        expect(page.get_by_text("e2e: first-round call booked")).to_be_visible()
    with live.rec.step("overview: timeline event + status persisted (reload + API)"):
        page.reload()
        expect(page.get_by_text("e2e: first-round call booked")).to_be_visible()
        events = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}/timeline")
        assert any(e.get("status") == "Interview" for e in events), events
        job = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}")
        assert job.get("application_status") == "Interview", job.get("application_status")


def test_overview_edit_location_and_apply_url(ws):
    live, page = ws, ws.page
    new_loc, new_url = "Remote (EU)", "https://halvard.example.com/careers/sre-remote"
    with live.rec.step("overview: Edit -> location + apply URL -> Save (PATCH)"):
        page.get_by_test_id("apply-url-edit").click()
        expect(page.get_by_test_id("job-readonly-title")).to_be_visible()
        page.get_by_label("Location", exact=True).fill(new_loc)
        page.get_by_label("Apply URL", exact=True).fill(new_url)
        resp = api_call(live, "PATCH", r"^/api/dashboard/jobs/[^/]+$",
                        lambda: page.get_by_role("button", name="Save", exact=True).click())
        assert_status(resp, 200, "save job details")
        expect(page.get_by_text("Job updated.")).to_be_visible()
    with live.rec.step("overview: edits persisted (reload + API)"):
        page.reload()
        expect(page.get_by_role("link", name=new_url)).to_be_visible()
        job = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}")
        assert (job.get("location"), job.get("apply_url")) == (new_loc, new_url), job


def test_overview_flag_score(ws):
    live, page = ws, ws.page
    with live.rec.step("overview: Flag this score -> POST /api/feedback/flag-score"):
        page.get_by_role("button", name=re.compile("Flag this score as inaccurate")).click()
        page.get_by_placeholder("0-100").fill("85")
        page.locator("form").filter(has_text="Flag this score").locator("textarea").fill("e2e flag")
        resp = api_call(live, "POST", r"^/api/feedback/flag-score$",
                        lambda: page.get_by_role("button", name="Submit Feedback").click())
        assert_status(resp, 200, "flag score")
        expect(page.get_by_text("Thanks — feedback recorded.")).to_be_visible()
    with live.rec.step("flag persisted (pipeline_adjustments row for this user)"):
        n = live.admin.count("pipeline_adjustments", {"user_id": f"eq.{live.account['id']}"})
        assert n >= 1, "no pipeline_adjustments row recorded for the flag"


def test_research_tab(ws):
    live, page = ws, ws.page
    tab(page, "Research")
    with live.rec.step("research: Generate Research renders an AI company overview"):
        expect(page.get_by_text("Quick Links")).to_be_visible()
        resp = api_call(live, "POST", r"/research$",
                        lambda: page.get_by_role("button", name="Generate Research").click(), timeout_s=120)
        live.net.allow({500, 502, 503, 504}, r"/research$", "asserted explicitly below")
        assert_status(resp, {200, 202}, "generate research")
        expect(page.get_by_text("Company Overview")).to_be_visible(timeout=240_000)
    with live.rec.step("research: persisted on the job (reload shows it without regenerating)"):
        job = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}")
        assert job.get("company_research"), "company_research not stored on the job"


def test_interview_prep_tab(ws):
    live, page = ws, ws.page
    tab(page, "Interview Prep")
    with live.rec.step("interview prep: Generate Prep renders prep content"):
        resp = api_call(live, "POST", r"/interview-prep$",
                        lambda: page.get_by_role("button", name="Generate Prep").click(), timeout_s=120)
        live.net.allow({500, 502, 503, 504}, r"/interview-prep$", "asserted explicitly below")
        assert_status(resp, {200, 202}, "generate interview prep")
        expect(page.get_by_text(re.compile("STAR Stories for This Role|Technical Topics to Review|Likely Behavioral Questions"))
               .first).to_be_visible(timeout=240_000)
    with live.rec.step("interview prep: persisted on the job"):
        job = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}")
        assert job.get("interview_prep"), "interview_prep not stored on the job"


def test_email_composer(ws):
    live, page = ws, ws.page
    tab(page, "Contacts")
    with live.rec.step("email composer (Contacts tab): Follow-Up template -> Generate Email"):
        expect(page.get_by_text("Email Composer")).to_be_visible()
        page.get_by_role("button", name=re.compile("^Follow-Up")).click()
        page.locator("#email-to").fill("Sam Rivera, Hiring Manager")
        with page.expect_request(lambda r: r.url.endswith("/generate-email")) as req:
            resp = api_call(live, "POST", r"/generate-email$",
                            lambda: page.get_by_role("button", name="Generate Email").click(), timeout_s=120)
        assert req.value.post_data_json.get("template") == "follow_up", req.value.post_data
        live.net.allow({500, 502, 503, 504}, r"/generate-email$", "asserted explicitly below")
        assert_status(resp, {200, 202}, "generate email")
        subject = page.locator("label:text-is('Subject') + input")
        expect(subject).not_to_have_value("", timeout=240_000)
        body = page.locator("label:text-is('Body') + textarea")
        assert len(body.input_value()) > 80, f"email body too short: {body.input_value()!r}"
        expect(page.get_by_role("button", name="Copy Full Email")).to_be_visible()


def test_resume_tab_iframe_and_download(ws):
    live, page = ws, ws.page
    tab(page, "Resume")
    with live.rec.step("resume tab: preview iframe src is a real PDF"):
        frame = page.get_by_title("Resume PDF Preview")
        expect(frame).to_be_visible()
        fetch_pdf(frame.get_attribute("src"))
    with live.rec.step("resume tab: Download PDF link is a real PDF"):
        link = page.locator("a").filter(has=page.get_by_role("button", name="Download PDF"))
        fetch_pdf(link.get_attribute("href"))


def test_cover_letter_tab(ws):
    live, page = ws, ws.page
    tab(page, "Cover Letter")
    job = live.api.json(f"/api/dashboard/jobs/{live.state['job_id']}")
    tier = job.get("score_tier")
    if job.get("cover_letter_s3_url"):
        with live.rec.step(f"cover letter tab (tier {tier}): iframe + Download are real PDFs"):
            frame = page.get_by_title("Cover Letter PDF Preview")
            expect(frame).to_be_visible()
            fetch_pdf(frame.get_attribute("src"))
            link = page.locator("a").filter(has=page.get_by_role("button", name="Download PDF"))
            fetch_pdf(link.get_attribute("href"))
    else:
        with live.rec.step(f"cover letter tab (tier {tier}): no letter -> honest empty state"):
            expect(page.get_by_text("No cover letter generated yet")).to_be_visible()
            expect(page.get_by_role("button", name="Generate Cover Letter")).to_be_visible()
            if tier in ("S", "A"):
                pytest.fail(f"tier {tier} job has no cover letter; S/A tier should get one (artifact policy)")
            live.rec.note(f"cover letter: none for tier {tier!r} (policy: only S/A get one); "
                          "generating is opt-in (E2E_LIVE_REGENERATE)")
