"""Journey 6: Add Job — Save & Score, the draft-restore banner, Score again, and
the long Tailor Resume run (6-9 minutes; 15-minute ceiling)."""

from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import expect

from tests.e2e_live._live import (
    api_call,
    assert_status,
    body_snippet,
    ensure_signed_in,
    fetch_pdf,
    require,
)

TITLE = "Site Reliability Engineer"


def company(state) -> str:
    # The per-run marker makes every row this run creates findable (and
    # cleanable) without touching anyone else's data.
    return f"Halvard Systems {state['marker']}"


JD = """About the role
Halvard Systems runs a payments platform processing 40 million transactions a day across
three AWS regions. We are hiring a Site Reliability Engineer to join the platform team in Dublin.

What you will do:
- Own the reliability of our Kubernetes (EKS) clusters and the services running on them
- Define SLOs and error budgets with product teams, and build the alerting that enforces them
- Automate infrastructure with Terraform and GitHub Actions; remove toil with Python and Go tooling
- Lead incident response and write blameless postmortems that actually change the system
- Improve observability with Prometheus, Grafana and OpenTelemetry tracing
- Drive capacity planning and cost optimisation across EC2, RDS and S3

What we are looking for:
- 3+ years running production systems on AWS
- Strong Linux, networking and containers fundamentals; hands-on Kubernetes
- Infrastructure as code (Terraform), CI/CD pipelines, and scripting in Python or Bash
- Experience with distributed systems, on-call and incident management
- Clear written communication

Nice to have: Go, PostgreSQL performance tuning, chaos engineering, PCI-DSS environments.
Hybrid, Dublin. Visa sponsorship is not required for Stamp 1G / Stamp 4 holders.
"""


def fill_form(page, state, *, location="Dublin, Ireland", url="https://halvard.example.com/careers/sre"):
    page.locator("#jd").fill(JD)
    page.locator("#job-title").fill(TITLE)
    page.locator("#company").fill(company(state))
    page.locator("#location").fill(location)
    page.locator("#apply-url").fill(url)
    page.locator("#resume-type").select_option("sre_devops")


@pytest.fixture()
def addjob(live):
    require(live.state, "onboarded", "onboarding did not complete")
    ensure_signed_in(live)
    live.goto("/add-job")
    expect(live.page.get_by_role("heading", name="Add Job")).to_be_visible()
    return live


def _score_card(page):
    return page.locator("div.animate-fade-in").filter(has_text="Score Card —")


def _await_score_card(live, resp, n_cards: int) -> None:
    """Wait for the n-th score card; on failure say what the task actually did.

    /api/score may answer 200 (sync) or 202 + poll_url (async). For the async
    form, the UI gives up after 120s with "Task timed out"; the evidence that
    matters is the task's own final status, read here from the poll URL."""
    page = live.page
    banner = page.locator("div.bg-error-light.border-error")  # ErrorBanner
    try:
        expect(_score_card(page).nth(n_cards - 1).or_(banner.first)).to_be_visible(timeout=240_000)
        if _score_card(page).count() >= n_cards:
            return
        shown = banner.first.inner_text()
    except AssertionError:
        shown = "<no card and no error after 240s>"
    body = resp.json() if resp.status == 202 else {}
    polls = live.net.find("GET", r"^API /api/(tasks|score)/")
    task = None
    if body.get("poll_url"):
        r = live.api.get(body["poll_url"])
        task = f"{r.status_code} {r.text[:300]}"
    pytest.fail(f"POST /api/score -> {resp.status} {str(body)[:160]}; UI shows {shown!r}; "
                f"{len(polls)} polls (statuses {sorted({c['status'] for c in polls})}); "
                f"task now: {task}")


def test_save_and_score(addjob):
    live, page = addjob, addjob.page
    fill_form(page, live.state)
    with live.rec.step("Save & Score -> POST /api/score returns a score (not 5xx)"):
        t0 = time.monotonic()
        resp = api_call(live, "POST", r"^/api/score$",
                        lambda: page.get_by_role("button", name="Save & Score").click(), timeout_s=150)
        # Declared before asserting so a 5xx is reported once, as THIS
        # failure, with its evidence — not again by the teardown monitor.
        live.net.allow({500, 502, 503, 504}, r"API /api/score$",
                       "asserted explicitly below; a 5xx here IS the reported failure")
        assert_status(resp, {200, 202}, "Save & Score", started=t0)
        _await_score_card(live, resp, 1)
    with live.rec.step("score card shows 3 perspectives + 'Saved to your dashboard.'"):
        card = _score_card(page).first
        expect(card.get_by_text("Saved to your dashboard.")).to_be_visible()
        for label in ("ATS", "Hiring Mgr", "Tech Recruiter", "Average"):
            expect(card.get_by_text(label, exact=True)).to_be_visible()
    with live.rec.step("scored job persisted (jobs row with match_score + tier)"):
        rows = live.admin.select("jobs", {"user_id": f"eq.{live.account['id']}",
                                          "company": f"eq.{company(live.state)}",
                                          "select": "job_id,match_score,score_tier,ats_score,apply_url"})
        assert len(rows) == 1, f"expected 1 saved job, found {len(rows)}"
        assert rows[0]["match_score"] is not None and rows[0]["ats_score"] is not None, rows[0]
        assert rows[0]["apply_url"] == "https://halvard.example.com/careers/sre", rows[0]
        live.state["scored_job_id"] = rows[0]["job_id"]


def test_draft_restore_banner(addjob):
    live, page = addjob, addjob.page
    with live.rec.step("draft: typed JD survives reload with a 'Restored the draft' banner"):
        page.locator("#jd").fill(JD)
        page.locator("#company").fill(company(live.state))
        page.wait_for_timeout(300)
        page.reload()
        expect(page.get_by_text("Restored the draft you left in this tab.")).to_be_visible()
        expect(page.locator("#jd")).to_have_value(JD)
        expect(page.locator("#company")).to_have_value(company(live.state))
    with live.rec.step("draft: Start fresh clears the form and the stored draft"):
        page.get_by_role("button", name="Start fresh").click()
        expect(page.locator("#jd")).to_have_value("")
        expect(page.get_by_text("Restored the draft you left in this tab.")).to_have_count(0)
        page.reload()
        expect(page.locator("#jd")).to_have_value("")
        expect(page.get_by_text("Restored the draft you left in this tab.")).to_have_count(0)


def test_score_again(addjob):
    live, page = addjob, addjob.page
    require(live.state, "scored_job_id", "Save & Score did not save a job")
    fill_form(page, live.state)
    with live.rec.step("re-score same JD returns the stored score ('Already scored')"):
        resp = api_call(live, "POST", r"^/api/score$",
                        lambda: page.get_by_role("button", name="Save & Score").click(), timeout_s=60)
        assert_status(resp, 200, "repeat Save & Score")
        expect(page.get_by_text("Already scored — this is the stored score, so it will not drift.")).to_be_visible()
    with live.rec.step("Score again -> POST /api/score {force:true} returns a fresh score"):
        t0 = time.monotonic()
        btn = page.get_by_role("button", name="Score again")
        with page.expect_request(lambda r: r.url.endswith("/api/score") and r.method == "POST") as req:
            resp = api_call(live, "POST", r"^/api/score$", btn.click, timeout_s=150)
        assert req.value.post_data_json.get("force") is True, req.value.post_data
        live.net.allow({500, 502, 503, 504}, r"API /api/score$", "asserted explicitly below")
        assert_status(resp, {200, 202}, "Score again", started=t0)
        _await_score_card(live, resp, 2)
        expect(_score_card(page).first.get_by_text("Saved to your dashboard.")).to_be_visible()


def test_tailor_resume(addjob):
    """The long one: Step Functions single-job pipeline, 6-9 minutes."""
    live, page = addjob, addjob.page
    fill_form(page, live.state)
    with live.rec.step("Tailor Resume -> POST /api/pipeline/run-single 202 + pollUrl"):
        resp = api_call(live, "POST", r"^/api/pipeline/run-single$",
                        lambda: page.get_by_role("button", name="Tailor Resume").click(), timeout_s=60)
        assert_status(resp, {200, 202}, "start single-job pipeline")
        body = resp.json()
        assert body.get("pollUrl"), f"no pollUrl: {body_snippet(resp)}"
        expect(page.get_by_text("Processing job...")).to_be_visible()
    with live.rec.step("pipeline finishes and the Tailored Resume card renders (<=15 min)"):
        tailor = page.get_by_test_id("tailor-card")
        expect(tailor).to_be_visible(timeout=15 * 60 * 1000)
        polls = live.net.find("GET", r"API /api/pipeline/status/")
        assert polls, "the page never polled the execution"
        assert all(c["status"] == 200 for c in polls), [c["status"] for c in polls if c["status"] != 200]
        live.rec.note(f"tailor: {len(polls)} status polls")
        # Record what was actually saved BEFORE judging the card, so a card
        # failure still leaves the downstream journeys a job to work on and
        # the report says which row the card picked versus what exists.
        rows = live.admin.select("jobs", {"user_id": f"eq.{live.account['id']}",
                                          "company": f"eq.{company(live.state)}",
                                          "select": "job_id,job_hash,canonical_hash,source,score_tier,"
                                                    "match_score,resume_s3_url,cover_letter_s3_url,first_seen"})
        summary = [{k: (bool(v) if k.endswith("_url") else v) for k, v in r.items()} for r in rows]
        live.rec.note(f"tailor: jobs rows for this company after the run: {summary}")
        view = tailor.locator("a").filter(has_text="View in Dashboard")
        card_job = re.search(r"/jobs/([^/?#]+)", view.get_attribute("href") or "").group(1) if view.count() else None
        with_pdf = [r for r in rows if r.get("resume_s3_url")]
        chosen = card_job if any(r["job_id"] == card_job and r.get("resume_s3_url") for r in rows) else (
            with_pdf[0]["job_id"] if with_pdf else card_job)
        if chosen:
            live.state["job_id"] = chosen
            live.state["job"] = live.api.json(f"/api/dashboard/jobs/{chosen}")
        alert = tailor.get_by_role("alert")
        if alert.count():
            pytest.fail(f"tailor card reports a non-ready outcome: {alert.first.inner_text()!r}; "
                        f"card links job {card_job}; rows: {summary}")
    with live.rec.step("tailor card's Download Resume PDF link returns a real PDF"):
        link = tailor.locator("a").filter(has_text="Download Resume PDF")
        expect(link).to_be_visible()
        href = link.get_attribute("href")
        n = fetch_pdf(href)
        live.rec.note(f"tailored résumé PDF: {n} bytes")
    with live.rec.step("tailored job persisted with résumé + scores"):
        job_id = live.state["job_id"]
        job = live.api.json(f"/api/dashboard/jobs/{job_id}")
        assert job.get("resume_s3_url"), "job has no resume_s3_url"
        assert job.get("company") == company(live.state), job.get("company")
        live.state.update(job_id=job_id, job=job)
        live.rec.note(f"tailored job {job_id}: tier={job.get('score_tier')} match={job.get('match_score')} "
                      f"cover_letter={'yes' if job.get('cover_letter_s3_url') else 'no'}")
