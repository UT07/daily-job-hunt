"""Browser E2E tests for the critical user journeys.

Run with:
    pytest tests/e2e/test_critical_journeys.py

The harness (tests/e2e/conftest.py) builds `web/` in mode `e2e`, serves
`web/dist`, and intercepts every HTTP call inside a real Chromium. What that
does and does not establish is spelled out in the conftest docstring; the short
version is that these tests prove the shipped bundle renders, routes, gates on
auth and issues the right request for a given user action -- and prove nothing
about the server that answers it, because every response is a fixture.

Where a journey needs a real boundary crossed, the test says so in its name and
docstring rather than asserting on a fixture and calling it verified. Two tests
remain skipped and each names exactly what is missing; neither is skipped for
"Playwright isn't set up".

Conventions
-----------
Visibility is asserted with Playwright's `expect`, never `is_visible()`: the
latter samples the DOM once and returns, so on a lazily-loaded route it reports
False before the chunk has even arrived. Request assertions go through
`api_stub.assert_called`, which retries for the same reason.

Most of the dashboard's and Settings' form controls carry an unassociated
`<label>` (no `htmlFor`/`id`), and their toggle buttons have no accessible name
at all, so `get_by_label` and `get_by_role(name=...)` cannot reach them; those
are located by placeholder, by a distinctive `<option>`, or by their card. The
`Input`/`Select`/`Textarea` components in `components/ui/Input.jsx` do bind a
`label` prop, so wherever one is passed (the login form, the resume editor)
`get_by_label` is used.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    PROFILE,
    SEARCH_CONFIG,
    make_job,
    register_dashboard_routes,
    sign_in,
    wait_until,
)

pytestmark = pytest.mark.e2e

# A syntactically valid one-page PDF, so an iframe pointed at it receives a real
# application/pdf response rather than a 503 the test would then have to explain
# away. Nothing asserts on rendered PDF content -- see
# test_resume_tab_shows_pdf_preview for why that is not an oversight.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

JOB_ID = "job-0001"

EMPTY_STATS = {
    "total_jobs": 0,
    "matched_jobs": 0,
    "avg_match_score": 0,
    "jobs_by_status": {},
    "total_applied": 0,
    "total_rejected": 0,
    "total_interviewing": 0,
    "total_offers": 0,
}


def visible(locator):
    """Narrow a locator to the copy that is actually on screen.

    JobTable renders the same job twice -- a `md:hidden` card stack and a
    `hidden md:block` table -- so an unfiltered `get_by_text(...).first`
    resolves to the hidden mobile copy at desktop width and fails on
    visibility. Filtering is better than picking `.nth(1)`, which would
    silently invert if the two blocks ever swap order.
    """
    return locator.filter(visible=True)


def kpi_card(page, label: str):
    """One StatsBar tile (components/ui/KPICard.jsx renders `div.p-5`)."""
    return page.locator("div.p-5").filter(has_text=label).first


def settings_card(page, heading: str):
    """One Settings section, scoped by its heading.

    Both Profile and Search Preferences have a button reading `Save Changes`,
    so an unscoped locator would click whichever came first and the test would
    assert against the wrong endpoint's payload.
    """
    return page.locator("div.shadow-brutal").filter(has=page.get_by_role("heading", name=heading, exact=True))


def workspace_job(**overrides) -> dict:
    """A single job as GET /api/dashboard/jobs/{id} returns it (a bare row)."""
    job = make_job(
        job_id=JOB_ID,
        ats_score=88,
        hiring_manager_score=86,
        tech_recruiter_score=90,
        base_ats_score=71,
        tailored_ats_score=88,
        final_score=88,
        writing_quality_score=84,
        tailoring_model="stub-model-v1",
        gaps=["Go"],
        match_reasoning="Fleet-scale AWS experience lines up with the role.",
        resume_version=1,
    )
    job.update(overrides)
    return job


def open_workspace(page, api_stub, job: dict, *, timeline: list | None = None) -> None:
    """Deep-link to /jobs/{id} as a signed-in user and wait for the header."""
    api_stub.on("GET", f"/api/dashboard/jobs/{job['job_id']}", job)
    api_stub.on("GET", f"/api/dashboard/jobs/{job['job_id']}/timeline", timeline or [])
    register_dashboard_routes(api_stub, active=[job])
    sign_in(page)
    page.wait_for_url("**/", timeout=15_000)
    page.goto(f"/jobs/{job['job_id']}")
    expect(page.get_by_role("heading", name=job["title"])).to_be_visible()


def open_dashboard(page) -> None:
    sign_in(page)
    expect(page.get_by_role("heading", name="Job Dashboard")).to_be_visible()


# ---------------------------------------------------------------------------


class TestLoginFlow:
    """Supabase Auth login/signup via the frontend.

    GoTrue is stubbed (conftest.AuthStub), so what is verified is the app's
    handling of each GoTrue outcome -- not that Supabase accepts these
    credentials.
    """

    def test_login_page_renders(self, page, api_stub):
        page.goto("/login")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        expect(page.get_by_label("Email")).to_be_visible()
        expect(page.get_by_label("Password")).to_be_visible()

    def test_login_with_valid_credentials(self, page, api_stub):
        register_dashboard_routes(api_stub)
        sign_in(page)

        expect(page.get_by_role("heading", name="Job Dashboard")).to_be_visible()
        # The minted access token must reach the API layer, or the app is
        # rendering a signed-in shell over unauthenticated requests.
        jobs_call = api_stub.assert_called("GET", "/api/dashboard/jobs")
        assert jobs_call["headers"]["authorization"].startswith("Bearer ey")

    def test_login_with_invalid_credentials_shows_error(self, page, api_stub, auth_stub):
        auth_stub.reject_password = True
        sign_in(page)

        expect(page.get_by_text("Invalid login credentials")).to_be_visible()
        expect(page).to_have_url(re.compile(r"/login$"))
        api_stub.assert_not_called("GET", "/api/dashboard/jobs")

    def test_redirect_to_dashboard_after_login(self, page, api_stub):
        """The dashboard is the index route `/`, not `/dashboard`.

        Worth pinning: there is no `/dashboard` path in App.jsx, so a link to
        one falls through the `*` catch-all and redirects to `/`. The ad-hoc
        script this suite replaces navigated to `/dashboard` and read that
        redirect as a passing assertion.
        """
        register_dashboard_routes(api_stub)
        open_dashboard(page)

        expect(page).to_have_url(re.compile(r"^http://127\.0\.0\.1:\d+/$"))

    def test_logout_redirects_to_login(self, page, api_stub, auth_stub):
        register_dashboard_routes(api_stub)
        open_dashboard(page)

        # The control's only accessible name is its title attribute, and the
        # sidebar holding it is `hidden md:flex`, so it needs a >=768px
        # viewport -- pytest-playwright's default is 1280x720.
        page.get_by_title("Sign out").click()

        page.wait_for_url("**/login", timeout=15_000)
        assert any(c.startswith("POST /auth/v1/logout") for c in auth_stub.calls), auth_stub.calls
        # A redirect that left the session in storage would bounce straight
        # back on the next navigation.
        page.wait_for_function(
            "() => Object.keys(localStorage).filter(k => k.startsWith('sb-')).length === 0",
            timeout=10_000,
        )

    def test_signup_asks_for_email_confirmation_and_stays_on_login(self, page, api_stub):
        """Signup does not redirect -- it asks the user to confirm by email.

        Renamed from `test_signup_creates_account_and_redirects`, which
        described behaviour the app does not have: `LoginPage.handleSubmit`
        sets a success message and leaves the user on /login, because GoTrue
        issues no session until the address is confirmed.
        """
        page.goto("/login")
        page.get_by_role("button", name="Sign up").click()
        page.get_by_label("Email").fill("new-user@example.test")
        page.get_by_label("Password").fill("a-long-enough-password")
        page.get_by_role("button", name="Create account").click()

        expect(
            page.get_by_text("Account created. Check your email to confirm, then sign in.")
        ).to_be_visible()
        expect(page).to_have_url(re.compile(r"/login$"))

    def test_signup_with_a_short_password_never_reaches_gotrue(self, page, api_stub, auth_stub):
        """A short password is blocked before submit, by the browser.

        Note which gate actually fires. `LoginPage.handleSubmit` also throws
        "Password must be at least 8 characters.", but that branch is
        unreachable in a real browser: the password `Input` carries
        `minLength={8}`, so native constraint validation cancels the submit and
        `handleSubmit` never runs. A jsdom test cannot see this -- jsdom does
        not implement constraint validation -- which is why the assertion here
        is on `validity.tooShort` and on no request leaving the page, not on a
        message the user never sees.
        """
        page.goto("/login")
        page.get_by_role("button", name="Sign up").click()
        page.get_by_label("Email").fill("new-user@example.test")
        password = page.get_by_label("Password")
        password.fill("short")
        page.get_by_role("button", name="Create account").click()

        assert password.evaluate("el => el.validity.tooShort") is True
        expect(page).to_have_url(re.compile(r"/login$"))
        assert auth_stub.calls == [], f"a request escaped the validation gate: {auth_stub.calls}"

    def test_deep_link_while_signed_out_redirects_to_login(self, page, api_stub):
        """AppLayout's auth gate, exercised on a direct URL entry.

        This is the regression the vitest suite cannot see: it needs the real
        router, the SPA fallback and the lazy chunk all to load before the gate
        runs.
        """
        page.goto("/settings")
        page.wait_for_url("**/login", timeout=15_000)
        api_stub.assert_not_called("GET", "/api/profile")

    def test_incomplete_onboarding_redirects_to_onboarding(self, page, api_stub):
        """A user with no name and no completion stamp goes to /onboarding.

        Pinned because the gate reads two specific profile fields, and a rename
        on either silently strands every existing user on the wizard.
        """
        api_stub.on("GET", "/api/profile", {**PROFILE, "full_name": "", "onboarding_completed_at": None})
        sign_in(page)
        page.wait_for_url("**/onboarding", timeout=15_000)


class TestDashboard:
    """Main dashboard -- job list, filters, and views.

    Filter assertions land on the query string the browser sent, which is the
    dashboard's real contract with the API (web/src/lib/jobQuery.js pins the
    parameter set and its insertion order).
    """

    def test_dashboard_loads_jobs(self, page, api_stub):
        jobs = [
            make_job(job_id="job-a", title="Platform Engineer", company="Alpha Ltd"),
            make_job(job_id="job-b", title="Cloud Engineer", company="Beta GmbH", score_tier="A", match_score=84),
        ]
        register_dashboard_routes(api_stub, active=jobs)
        open_dashboard(page)

        table = page.locator("table")
        expect(table).to_contain_text("Platform Engineer")
        expect(table).to_contain_text("Alpha Ltd")
        expect(table).to_contain_text("Cloud Engineer")
        api_stub.assert_called("GET", "/api/dashboard/jobs", query_contains="lifecycle=active")

    def test_stats_bar_shows_totals_from_the_stats_endpoint(self, page, api_stub):
        register_dashboard_routes(
            api_stub,
            stats={
                "total_jobs": 42,
                "matched_jobs": 40,
                "avg_match_score": 76.4,
                "jobs_by_status": {"New": 30, "Applied": 12},
                "total_applied": 12,
                "total_rejected": 0,
                "total_interviewing": 0,
                "total_offers": 0,
            },
        )
        open_dashboard(page)

        expect(kpi_card(page, "Total Jobs")).to_contain_text("42")
        # avg_match_score is rounded for display.
        expect(kpi_card(page, "Avg Score")).to_contain_text("76")
        expect(kpi_card(page, "Applied")).to_contain_text("12")
        # Zero-valued cards are suppressed on purpose.
        expect(page.get_by_text("Offers", exact=True)).to_have_count(0)

    def test_filter_by_status(self, page, dashboard):
        page.locator("select:has(option[value='Applied'])").select_option("Applied")
        page.get_by_role("button", name="Apply Filters").click()

        dashboard.assert_called("GET", "/api/dashboard/jobs", query_contains="status=Applied")

    def test_the_search_box_queries_title_and_company_together(self, page, dashboard):
        """One box, both columns. Before this the dashboard had only `title`
        and `company`, which are AND-ed narrowing filters — you had to know
        which column the word lived in before you could find the job."""
        box = page.get_by_label("Search jobs by title or company")
        box.fill("stripe")
        box.press("Enter")

        dashboard.assert_called("GET", "/api/dashboard/jobs", query_contains="q=stripe")

    def test_the_search_term_survives_a_reload(self, page, dashboard, base_url):
        """It is a URL filter like every other one, so a link to a search is a
        link to the same results."""
        box = page.get_by_label("Search jobs by title or company")
        box.fill("camunda")
        box.press("Enter")
        page.wait_for_url("**/*q=camunda*", timeout=10_000)

    def test_clearing_the_search_drops_the_parameter(self, page, dashboard):
        """A filter you cannot turn off is a trap — the same reason every other
        filter here has a chip."""
        box = page.get_by_label("Search jobs by title or company")
        box.fill("stripe")
        box.press("Enter")
        dashboard.assert_called("GET", "/api/dashboard/jobs", query_contains="q=stripe")

        page.get_by_role("button", name="Clear").first.click()
        page.wait_for_timeout(600)
        last = [c for c in dashboard.calls if c["path"] == "/api/dashboard/jobs"][-1]
        assert "q=" not in (last.get("query") or ""), (
            f"the search term outlived the Clear button: {last.get('query')}")

    def test_filter_by_source(self, page, dashboard):
        page.locator("select:has(option[value='linkedin'])").select_option("linkedin")
        page.get_by_role("button", name="Apply Filters").click()

        dashboard.assert_called("GET", "/api/dashboard/jobs", query_contains="source=linkedin")

    def test_tier_tab_narrows_the_query_to_one_tier(self, page, dashboard):
        page.get_by_role("button", name=re.compile("Must Apply")).click()

        dashboard.assert_called("GET", "/api/dashboard/jobs", query_contains="tier=S")

    def test_sort_by_match_score(self, page, dashboard):
        page.locator("select:has(option[value='match_score:desc'])").select_option("match_score:desc")

        call = dashboard.assert_called("GET", "/api/dashboard/jobs", query_contains="sort_by=match_score")
        assert call["params"]["sort_order"] == "desc"

    def test_card_view_toggle(self, page, dashboard):
        """List and card views render the same job through different components."""
        job_title = "Site Reliability Engineer"
        expect(page.locator("table")).to_contain_text(job_title)

        page.get_by_title("Card view").click()
        expect(page.locator("table")).to_have_count(0)
        expect(visible(page.get_by_text(job_title)).first).to_be_visible()

        page.get_by_title("List view").click()
        expect(page.locator("table")).to_contain_text(job_title)

    def test_pagination(self, page, api_stub):
        """Page 2 is requested with page=2 and the same filter state."""
        register_dashboard_routes(
            api_stub,
            active=[make_job(job_id=f"job-{i}", title=f"Engineer {i}") for i in range(25)],
            total=30,
            grand_total=30,
        )
        open_dashboard(page)

        page.get_by_role("button", name="Next").click()

        call = api_stub.assert_called("GET", "/api/dashboard/jobs", query_contains="page=2")
        assert call["params"]["per_page"] == "25"
        assert call["params"]["lifecycle"] == "active"

    def test_empty_state_shown_for_new_user(self, page, api_stub):
        """Zero jobs ever gets the onboarding prompt, not the "no match" text.

        The two empty states are distinct and showing the wrong one is a real
        bug. The distinguishing signal is `total` from
        GET /api/dashboard/jobs?page=1&per_page=1, so the stats fixture has to
        agree that the account is empty or the test is checking an
        impossible state.
        """
        register_dashboard_routes(
            api_stub, active=[], grand_total=0, stats=EMPTY_STATS, skills=[]
        )
        open_dashboard(page)

        expect(
            page.get_by_text("No jobs yet. Add one manually or run the pipeline above to get started.")
        ).to_be_visible()
        expect(page.get_by_text("No jobs match your current filters.")).to_have_count(0)

    def test_filtered_to_zero_shows_the_other_empty_state(self, page, api_stub):
        register_dashboard_routes(api_stub, active=[], grand_total=63)
        open_dashboard(page)

        expect(page.get_by_text("No jobs match your current filters.")).to_be_visible()
        expect(page.get_by_text("No jobs yet.")).to_have_count(0)


class TestPipelineRun:
    """Triggering and monitoring a pipeline run from the UI.

    These verify the browser side of the contract only: the request the Run
    Pipeline button issues, and what the UI does with each poll result. They
    cannot show that a Step Functions execution really started -- that needs
    AWS credentials and lives in tests/contract/ and scripts/smoke_prod.py.
    """

    def test_run_pipeline_button_starts_execution(self, page, dashboard):
        dashboard.on("POST", "/api/pipeline/run", {"pollUrl": "/api/pipeline/status/exec-1"})
        dashboard.on("GET", "/api/pipeline/status/exec-1", {"status": "RUNNING"})

        page.get_by_role("button", name=re.compile("Run Pipeline")).click()

        call = dashboard.assert_called("POST", "/api/pipeline/run")
        # The button must forward the user's configured queries, not an empty
        # list: the backend falls back to the saved config when handed an empty
        # one, which would mask a frontend that failed to load them.
        assert call["body"] == {"queries": SEARCH_CONFIG["queries"]}

    def test_pipeline_status_updates_while_running(self, page, dashboard):
        dashboard.on("POST", "/api/pipeline/run", {"pollUrl": "/api/pipeline/status/exec-1"})
        dashboard.on("GET", "/api/pipeline/status/exec-1", {"status": "RUNNING"})

        page.get_by_role("button", name=re.compile("Run Pipeline")).click()

        expect(page.get_by_text("Scraping jobs across all sources...")).to_be_visible()
        expect(page.get_by_role("button", name="Running...")).to_be_disabled()

    def test_dashboard_refreshes_after_pipeline_completes(self, page, api_stub):
        """A terminal poll result must refetch the job list.

        PipelineStatus polls every 5s, so this really does wait out one
        interval; that delay is part of the behaviour under test.
        """
        before = [make_job(job_id="job-old", title="Existing Role")]
        after = [make_job(job_id="job-new", title="Freshly Scraped Role")]
        current = {"jobs": before}

        def jobs(call):
            params = call["params"]
            if params.get("per_page") == "1":
                return {"jobs": [], "page": 1, "per_page": 1, "total": 1}
            if params.get("lifecycle") == "stale":
                return {"jobs": [], "page": 1, "per_page": 25, "total": 0}
            rows = current["jobs"]
            return {"jobs": rows, "page": 1, "per_page": 25, "total": len(rows)}

        register_dashboard_routes(api_stub)
        api_stub.on("GET", "/api/dashboard/jobs", jobs)
        api_stub.on("POST", "/api/pipeline/run", {"pollUrl": "/api/pipeline/status/exec-1"})
        api_stub.on("GET", "/api/pipeline/status/exec-1", {"status": "SUCCEEDED"})

        open_dashboard(page)
        expect(page.locator("table")).to_contain_text("Existing Role")

        current["jobs"] = after
        page.get_by_role("button", name=re.compile("Run Pipeline")).click()

        expect(page.locator("table")).to_contain_text("Freshly Scraped Role", timeout=25_000)
        expect(page.locator("table")).not_to_contain_text("Existing Role")

    def test_pipeline_failure_shows_error_state(self, page, dashboard):
        dashboard.on("POST", "/api/pipeline/run", {"detail": "state machine ARN not configured"}, status=500)

        page.get_by_role("button", name=re.compile("Run Pipeline")).click()

        expect(page.get_by_text("state machine ARN not configured")).to_be_visible()
        # The button has to come back, or a transient failure strands the user.
        expect(page.get_by_role("button", name=re.compile("Run Pipeline"))).to_be_enabled()


class TestAddJob:
    """Manual job addition via the paste-JD flow."""

    LONG_JD = (
        "We are hiring a Site Reliability Engineer to own our AWS estate. "
        "Responsibilities include Kubernetes, Terraform, CI/CD pipelines, "
        "on-call rotation and incident response. Requirements: 3+ years of "
        "production experience and strong Linux fundamentals."
    )

    def open_add_job(self, page):
        sign_in(page)
        page.wait_for_url("**/", timeout=15_000)
        page.goto("/add-job")
        expect(page.get_by_label("Job Description")).to_be_visible()

    def test_paste_jd_and_score(self, page, api_stub):
        api_stub.on(
            "POST",
            "/api/score",
            {
                "ats_score": 88,
                "hiring_manager_score": 82,
                "tech_recruiter_score": 91,
                "avg_score": 87,
                "reasoning": "Strong AWS and Kubernetes overlap.",
                "matched_resume": "sre_devops",
                "job_id": "job-scored",
                "saved": True,
            },
        )
        self.open_add_job(page)

        page.get_by_label("Job Description").fill(self.LONG_JD)
        page.get_by_role("button", name="Save & Score").click()

        call = api_stub.assert_called("POST", "/api/score")
        assert call["body"]["job_description"] == self.LONG_JD
        assert call["body"]["resume_type"] == "sre_devops"
        expect(page.get_by_text("Strong AWS and Kubernetes overlap.")).to_be_visible()

    def test_empty_jd_disables_the_action_buttons(self, page, api_stub):
        """There is no validation *message* -- the buttons are disabled.

        Renamed from `test_empty_jd_shows_validation_error`, which described a
        message AddJob.jsx does not render: the handlers return silently and
        every button carries `disabled={!jd.trim() || ...}`. Asserting on a
        non-existent error string would have been a test that can only fail.
        """
        self.open_add_job(page)

        for label in ("Save & Score", "Tailor Resume", "Cover Letter", "Find Contacts"):
            expect(page.get_by_role("button", name=label)).to_be_disabled()

        page.get_by_role("button", name="Save & Score").click(force=True)
        api_stub.assert_not_called("POST", "/api/score")

    def test_short_jd_warns_before_scoring(self, page, api_stub):
        self.open_add_job(page)

        page.get_by_label("Job Description").fill("SRE wanted, AWS, k8s.")

        expect(
            page.get_by_text(
                "Job description seems too short. AI matching works best with a detailed JD "
                "(responsibilities, requirements, tech stack)."
            )
        ).to_be_visible()

    def test_tailor_resume_shows_progress(self, page, api_stub):
        """Tailor Resume starts the single-job pipeline and polls it.

        The poll result is a fixture, so this establishes the request/poll
        wiring and the progress UI -- not that a resume compiled.
        """
        api_stub.on(
            "POST",
            "/api/pipeline/run-single",
            {"executionArn": "arn:stub", "startDate": 0, "pollUrl": "/api/pipeline/status/single-1"},
        )
        api_stub.on(
            "GET",
            "/api/pipeline/status/single-1",
            {"name": "single-1", "status": "SUCCEEDED", "output": {"resume_url": "/api/artifacts/resume.pdf"}},
        )
        self.open_add_job(page)

        page.get_by_label("Job Description").fill(self.LONG_JD)
        page.get_by_role("button", name="Tailor Resume").click()

        expect(page.get_by_text(re.compile("Starting pipeline|Processing job")).first).to_be_visible()
        call = api_stub.assert_called("POST", "/api/pipeline/run-single")
        assert call["body"]["job_description"] == self.LONG_JD
        expect(page.get_by_text("Done!").first).to_be_visible(timeout=25_000)

    @pytest.mark.skip(
        reason="AddJob has no URL-scrape flow to test. The form's `apply_url` is a "
        "metadata field forwarded verbatim (AddJob.jsx getPayload); nothing fetches or "
        "scrapes it, and POST /api/score requires job_description min_length=20. The "
        "feature has to exist before it can have a test."
    )
    def test_paste_jd_with_url(self):
        ...


class TestJobWorkspace:
    """Individual job detail page -- tabs and actions."""

    TABS = ("Overview", "Research", "Resume", "Editor", "Cover Letter", "Contacts", "Interview Prep")

    def test_all_seven_tabs_render(self, page, api_stub):
        open_workspace(page, api_stub, workspace_job())
        for tab in self.TABS:
            expect(page.get_by_role("button", name=tab, exact=True)).to_be_visible()

    def test_overview_tab_shows_details(self, page, api_stub):
        job = workspace_job()
        open_workspace(page, api_stub, job)

        expect(page.get_by_role("heading", name=job["title"])).to_be_visible()
        expect(page.get_by_text(job["company"]).first).to_be_visible()
        expect(page.get_by_role("heading", name="Job Details")).to_be_visible()
        expect(page.get_by_role("heading", name="Application Timeline")).to_be_visible()
        expect(page.get_by_role("heading", name="Job Description")).to_be_visible()
        expect(page.get_by_text(job["description"])).to_be_visible()
        # The three-perspective scores are the point of the page.
        for label in ("ATS", "Hiring Manager", "Technical"):
            expect(page.get_by_text(label, exact=True).first).to_be_visible()
        expect(page.get_by_text("Fleet-scale AWS experience lines up with the role.")).to_be_visible()

    def test_empty_timeline_shows_its_own_prompt(self, page, api_stub):
        open_workspace(page, api_stub, workspace_job(), timeline=[])

        expect(
            page.get_by_text('No status updates yet. Click "Update Status" to record your first action.')
        ).to_be_visible()

    def test_status_update_posts_to_the_timeline(self, page, api_stub):
        """Renamed from `test_status_change_persists`.

        Persistence cannot be checked here -- a reload re-reads the same
        fixture, so "it came back" would only assert that a stub is a stub.
        What is checked is the request the UI sends. Real persistence is the
        API tests' job.
        """
        api_stub.on(
            "POST",
            f"/api/dashboard/jobs/{JOB_ID}/timeline",
            {"id": "evt-1", "status": "Applied", "notes": None, "created_at": "2026-09-30T10:00:00Z"},
        )
        open_workspace(page, api_stub, workspace_job(), timeline=[])

        page.get_by_role("button", name="Update Status").click()
        page.locator("select:has(option[value='Applied'])").select_option("Applied")
        page.get_by_role("button", name="Save", exact=True).click()

        call = api_stub.assert_called("POST", f"/api/dashboard/jobs/{JOB_ID}/timeline")
        assert call["body"]["status"] == "Applied"

    def test_resume_tab_shows_pdf_preview(self, page, api_stub):
        """Asserts the iframe's `src`, deliberately not its rendered content.

        Playwright's Chromium ships without the PDF viewer, so there is no
        document inside that frame to query in any environment -- a content
        assertion here could only ever be a sleep that passes. The `src` is
        also where the bug history is: the inline-signed `resume_s3_url` must
        drive the preview and the attachment-signed `resume_s3_download_url`
        the download link, and swapping them yields a blank pane that no
        content assertion would catch either.
        """
        job = workspace_job(
            resume_s3_url="/api/artifacts/resume.pdf",
            resume_s3_download_url="/api/artifacts/resume-download.pdf",
        )
        api_stub.on("GET", "/api/artifacts/resume.pdf", MINIMAL_PDF)
        api_stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}/versions", [])
        open_workspace(page, api_stub, job)

        page.get_by_role("button", name="Resume", exact=True).click()

        preview = page.locator('iframe[title="Resume PDF Preview"]')
        expect(preview).to_have_attribute("src", "/api/artifacts/resume.pdf")
        expect(page.get_by_role("link", name=re.compile("Download PDF"))).to_have_attribute(
            "href", "/api/artifacts/resume-download.pdf"
        )
        api_stub.assert_called("GET", f"/api/dashboard/jobs/{JOB_ID}/versions")

    def test_resume_tab_without_an_artifact_offers_to_generate_one(self, page, api_stub):
        api_stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}/versions", [])
        open_workspace(page, api_stub, workspace_job(resume_s3_url=None))

        page.get_by_role("button", name="Resume", exact=True).click()

        expect(page.get_by_text("No resume generated yet")).to_be_visible()
        expect(page.locator('iframe[title="Resume PDF Preview"]')).to_have_count(0)

    def test_cover_letter_tab_shows_pdf(self, page, api_stub):
        job = workspace_job(
            cover_letter_s3_url="/api/artifacts/cover.pdf",
            cover_letter_s3_download_url="/api/artifacts/cover-download.pdf",
        )
        api_stub.on("GET", "/api/artifacts/cover.pdf", MINIMAL_PDF)
        open_workspace(page, api_stub, job)

        page.get_by_role("button", name="Cover Letter", exact=True).click()

        expect(page.locator('iframe[title="Cover Letter PDF Preview"]')).to_have_attribute(
            "src", "/api/artifacts/cover.pdf"
        )

    def test_contacts_tab_shows_linkedin_contacts(self, page, api_stub):
        """Contacts are read off the job row; there is no GET endpoint."""
        job = workspace_job(
            linkedin_contacts=[
                {
                    "name": "Dana Moore",
                    "role": "Engineering Manager",
                    "why": "Owns the SRE team",
                    "profile_url": "https://linkedin.test/in/dana",
                }
            ]
        )
        open_workspace(page, api_stub, job)

        page.get_by_role("button", name="Contacts", exact=True).click()

        expect(page.get_by_text("Dana Moore")).to_be_visible()
        expect(page.get_by_text("Engineering Manager").first).to_be_visible()
        expect(page.get_by_role("link", name="View Profile")).to_be_visible()

    def test_contacts_tab_empty_state(self, page, api_stub):
        open_workspace(page, api_stub, workspace_job(linkedin_contacts=None))

        page.get_by_role("button", name="Contacts", exact=True).click()

        expect(page.get_by_text('No contacts found yet. Click "Find Contacts" above.')).to_be_visible()

    def test_inline_editing(self, page, api_stub):
        """Editing a section posts the edited sections back for a recompile.

        The recompiled PDF is not asserted: compiling one needs tectonic in the
        Lambda, which no local or CI run has. What is asserted is that the edit
        reaches POST /api/dashboard/jobs/{id}/sections intact, including the
        untouched sections -- the step that mangled input in the `$`/`_`
        escaping bug (b2f650e).
        """
        job = workspace_job(resume_s3_url="/api/artifacts/resume.pdf")
        api_stub.on("GET", "/api/artifacts/resume.pdf", MINIMAL_PDF)
        api_stub.on(
            "GET",
            f"/api/dashboard/jobs/{JOB_ID}/sections",
            {
                # `skills` and the list sections (experience, projects) render
                # structured sub-editors that reshape their value, so this uses
                # two plain-text sections: the assertion is about characters
                # surviving the round trip, not about those editors.
                "sections": {"summary": "SRE with 4 years on AWS.", "education": "BSc Computer Science"},
                "jd_analysis": {},
            },
        )
        api_stub.on("POST", f"/api/dashboard/jobs/{JOB_ID}/sections", {"status": "queued"})
        open_workspace(page, api_stub, job)

        page.get_by_role("button", name="Editor", exact=True).click()
        # Each section is an accordion that starts *open* (SectionEditor.jsx:339),
        # so its header must not be clicked -- that would collapse it. The
        # textarea is scoped to the Summary section rather than taken as the
        # first on the page: an unscoped `.first` silently edited Education's
        # box instead, and the assertion on the pre-fill value below is what
        # caught it.
        summary_section = page.locator("div.mb-3").filter(
            has=page.get_by_role("button", name="Summary", exact=True)
        )
        summary = summary_section.locator("textarea").first
        expect(summary).to_have_value("SRE with 4 years on AWS.")
        summary.fill("SRE with 5 years on AWS and a $100k cost saving.")
        page.get_by_role("button", name="Save & Compile").click()

        call = api_stub.assert_called("POST", f"/api/dashboard/jobs/{JOB_ID}/sections")
        assert call["body"]["sections"]["summary"] == "SRE with 5 years on AWS and a $100k cost saving."
        assert call["body"]["sections"]["education"] == "BSc Computer Science", "an untouched section was dropped"


class TestSettings:
    """User settings and profile management."""

    def open_settings(self, page, api_stub):
        register_dashboard_routes(api_stub)
        api_stub.on("GET", "/api/resumes", {"resumes": []})
        sign_in(page)
        page.wait_for_url("**/", timeout=15_000)
        page.goto("/settings")
        expect(page.get_by_role("heading", name="Job Sources")).to_be_visible()

    def _source_toggle(self, page, label: str):
        """The toggles have no accessible name; reach them through their row."""
        return (
            page.get_by_text(label, exact=True)
            .locator("xpath=ancestor::div[descendant::button][1]")
            .locator("button")
        )

    def test_source_toggles_save(self, page, api_stub):
        api_stub.on("PUT", "/api/search-config", {"status": "saved"})
        self.open_settings(page, api_stub)

        self._source_toggle(page, "LinkedIn").click()
        settings_card(page, "Job Sources").get_by_role("button", name="Save Sources").click()

        call = api_stub.assert_called("PUT", "/api/search-config")
        assert "linkedin" not in call["body"]["enabled_sources"]
        assert "indeed" in call["body"]["enabled_sources"]
        # Save Sources must send that one key. The same endpoint is also
        # written by Search Preferences with the whole prefs object, and a
        # merged payload here would clobber the user's saved queries.
        assert list(call["body"]) == ["enabled_sources"]
        expect(page.get_by_text("Job sources saved.")).to_be_visible()

    def test_disabled_sources_cannot_be_toggled_on(self, page, api_stub):
        self.open_settings(page, api_stub)

        expect(page.get_by_text("UK only — disabled")).to_be_visible()
        expect(page.get_by_text("Needs Fargate (dormant)")).to_be_visible()
        expect(self._source_toggle(page, "Adzuna")).to_be_disabled()
        expect(self._source_toggle(page, "Glassdoor")).to_be_disabled()

    def test_search_config_update(self, page, api_stub):
        """Changing the min match score sends it.

        "The next run uses it" is not checked here -- that is the pipeline's
        read of the saved row, and config drift between the two is a known
        live issue (memory: config_source_of_truth_gap).
        """
        api_stub.on("PUT", "/api/search-config", {"status": "saved"})
        self.open_settings(page, api_stub)

        card = settings_card(page, "Search Preferences")
        card.locator('input[type="range"]').fill("70")
        card.get_by_role("button", name="Save Changes").click()

        call = api_stub.assert_called("PUT", "/api/search-config")
        assert call["body"]["min_match_score"] == 70

    def test_profile_update(self, page, api_stub):
        api_stub.on("PUT", "/api/profile", {**PROFILE, "full_name": "Renamed User"})
        self.open_settings(page, api_stub)

        card = settings_card(page, "Profile")
        card.get_by_placeholder("Utkarsh Singh").fill("Renamed User")
        card.get_by_placeholder("Dublin, Ireland").first.fill("Cork, Ireland")
        card.get_by_role("button", name="Save Changes").click()

        call = api_stub.assert_called("PUT", "/api/profile")
        # Settings keeps the field as `name` in local state and sends it under
        # that key; ProfileUpdateRequest declares both `name` and `full_name`
        # (app.py:381) and treats the second as an alias. Accepting either
        # keeps this test on the behaviour that matters -- the typed name
        # arrives -- rather than on which of the two aliases is in play.
        assert (call["body"].get("name") or call["body"].get("full_name")) == "Renamed User"
        assert call["body"]["location"] == "Cork, Ireland"
        # The backend is extra="forbid"; these keys are read-only and echoing
        # any of them back is a 422 in the user's face.
        for forbidden in ("email", "id", "profile_complete", "onboarding_completed_at"):
            assert forbidden not in call["body"], f"{forbidden} must be stripped before PUT"

    def test_resume_upload(self, page, api_stub, tmp_path):
        api_stub.on(
            "POST",
            "/api/resumes/upload",
            {
                "resume_id": "res-1",
                "sections": {"summary": "..."},
                "tailorable": True,
                "converted_from_pdf": False,
                "extracted_profile": {},
            },
        )
        self.open_settings(page, api_stub)

        source = tmp_path / "master.tex"
        source.write_text(r"\documentclass{article}\begin{document}hello\end{document}")
        page.locator('input[type="file"]').set_input_files(str(source))
        settings_card(page, "Resumes").get_by_role("button", name="Upload & Parse").click()

        api_stub.assert_called("POST", "/api/resumes/upload")
        expect(page.get_by_text("Resume uploaded and parsed successfully.")).to_be_visible()
        # The list must be re-read, or a successful upload looks like it vanished.
        wait_until(
            page,
            lambda: len(api_stub.matching("GET", "/api/resumes")) >= 2,
            message="the resume list was never refetched after a successful upload",
        )

    def test_gdpr_export_downloads_zip(self, page, api_stub):
        """The export must reach the user as a file the browser saves.

        The bytes are a fixture, so the archive's *contents* are not checked --
        only that GET /api/gdpr/export is issued and its body is handed to the
        user as a .zip download.
        """
        register_dashboard_routes(api_stub)
        api_stub.on("GET", "/api/gdpr/export", b"PK\x03\x04stub-zip-bytes")
        sign_in(page)
        page.wait_for_url("**/", timeout=15_000)
        page.goto("/data-export")
        button = page.get_by_role("button", name="Export My Data")
        expect(button).to_be_visible()

        with page.expect_download(timeout=20_000) as download:
            button.click()

        assert download.value.suggested_filename.endswith(".zip")
        api_stub.assert_called("GET", "/api/gdpr/export")
        expect(page.get_by_text("Your data has been downloaded successfully.")).to_be_visible()

    @pytest.mark.skip(
        reason="Account deletion is irreversible and has no dry-run. A stubbed DELETE "
        "would assert only that the button calls the endpoint, while the part that "
        "matters -- that every row and S3 object for the user is really gone -- needs a "
        "real Supabase project and bucket. Needs the staging environment with a "
        "disposable user (docs/runbooks/2026-04-30-staging-environment.md); until that "
        "exists this belongs in the integration suite, not here."
    )
    def test_gdpr_delete_account(self):
        ...


class TestNoConsoleErrors:
    """Every route must load clean in a production bundle.

    This is the check the previous ad-hoc script filtered into uselessness: it
    dropped any message containing "404", which is most of what a broken build
    reports. Nothing is filtered here beyond the favicon and DNS failures for
    the deliberately-unresolvable stub Supabase host.
    """

    def test_main_routes_load_without_console_errors(self, page, api_stub, console_errors):
        register_dashboard_routes(api_stub)
        api_stub.on("GET", "/api/resumes", {"resumes": []})
        api_stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}", workspace_job())
        api_stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}/timeline", [])

        sign_in(page)
        expect(page.get_by_role("heading", name="Job Dashboard")).to_be_visible()

        for path, ready in (
            ("/add-job", "Job Description"),
            ("/settings", "Job Sources"),
            ("/artifacts", None),
            ("/privacy", None),
            (f"/jobs/{JOB_ID}", "Application Timeline"),
        ):
            page.goto(path)
            page.wait_for_load_state("networkidle")
            if ready:
                expect(page.get_by_text(ready).first).to_be_visible()

        assert console_errors == [], f"console errors across routes: {console_errors}"
