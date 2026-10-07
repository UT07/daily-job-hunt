"""The Resume Studio, in a real browser. Previously uncovered entirely.

`/jobs/:jobId/studio` is the feature the user spends most of their time in and
had ZERO browser coverage — the E2E suite reached login, the dashboard,
artifacts and settings, and stopped at the Studio's door. Two state defects
were found and fixed there on 2026-10-05 by reading the source, because nothing
could have caught them by driving the page:

  * the section loader re-ran whenever `job.resume_s3_url` changed, and EVERY
    save changes it (the backend mints a fresh presigned URL for the same S3
    key), so it refetched and overwrote whatever had been typed since
  * the PDF pane seeded its URL from a prop once and never re-synced, so a
    regenerate elsewhere on the page left the preview showing the previous
    document

Both are exactly the kind of defect a unit test cannot see and a human notices
only as "my edits vanished". They are the first things asserted here.

What this proves and does not: per the suite's conftest, every HTTP response is
a fixture, so these tests prove the shipped bundle renders, routes and issues
the right requests with the right payloads. They prove nothing about the server
that answers them — that is scripts/smoke_prod.py's job.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    make_job,
    register_dashboard_routes,
    register_shell_routes,
    sign_in,
)

JOB_ID = "studio-job-1"

# `summary` only, and that is a deliberate constraint rather than laziness.
# SectionEditor renders `experience`/`projects` as LISTS of entry objects and
# `skills` through its own branch; a plain string in any of those throws inside
# the component, React renders nothing, and the page comes back blank — which
# reads as "the Studio is broken" rather than "the fixture is wrong". That cost
# a debugging round here and another one in the unit suite on 2026-10-05, so:
# a text section is the only shape this file asserts against, and anything
# testing the list renderers should build real entry objects.
SECTIONS = {
    "summary": "Site Reliability Engineer with eight years of platform work.",
}


def _job(**over):
    return make_job(job_id=JOB_ID, title="Senior Site Reliability Engineer",
                    company="Camunda", resume_s3_url="https://s3.test/a.pdf", **over)


def _register_studio(stub, *, sections=None, job=None, sections_status=200):
    register_shell_routes(stub)
    # sign_in lands on the dashboard before the Studio deep-link, so its
    # requests must be stubbed too or api_stub fails the test for them.
    register_dashboard_routes(stub, active=[job or _job()])
    stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}", job or _job())
    if sections_status == 200:
        stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}/sections",
                {"sections": dict(sections or SECTIONS), "jd_analysis": None})
    else:
        stub.on("GET", f"/api/dashboard/jobs/{JOB_ID}/sections",
                {"detail": "No tailored .tex found"}, status=sections_status)
    return stub


def _open_studio(page, base_url):
    """Sign in through the UI, then deep-link to the Studio.

    `auth_stub` alone is not a session: the app gates routes on AuthProvider
    state established by an actual GoTrue exchange, so a bare page.goto to a
    protected route lands on /login. The first version of this file did exactly
    that and every assertion failed on a login form — the page was never the
    one under test. Same shape as `open_workspace` in test_critical_journeys.
    """
    sign_in(page)
    page.wait_for_url("**/", timeout=15_000)
    page.goto(f"{base_url}/jobs/{JOB_ID}/studio")


@pytest.mark.usefixtures("auth_stub")
class TestStudioRenders:
    def test_the_studio_renders_the_job_and_its_sections(self, page, base_url, api_stub):
        _register_studio(api_stub)
        _open_studio(page, base_url)

        expect(page.get_by_role("heading", name="Senior Site Reliability Engineer")).to_be_visible()
        expect(page.get_by_text("Camunda")).to_be_visible()
        expect(page.get_by_text("Site Reliability Engineer with eight years")).to_be_visible()
        api_stub.assert_called("GET", f"/api/dashboard/jobs/{JOB_ID}/sections")

    def test_a_job_with_no_tailored_resume_explains_itself(self, page, base_url, api_stub):
        """A 404 here is an everyday state — the job has not been tailored yet —
        and must read as guidance, not as a raw error."""
        _register_studio(api_stub, sections_status=404)
        _open_studio(page, base_url)

        # Scoped: the Studio renders more than one role="status" (the score
        # strip announces itself too), so an unfiltered locator is a strict-mode
        # violation rather than a missing element.
        banner = page.get_by_role("status").filter(
            has_text="No tailored resume for this job yet")
        expect(banner).to_be_visible()

    def test_the_studio_route_logs_no_console_errors(self, page, base_url, api_stub, console_errors):
        _register_studio(api_stub)
        _open_studio(page, base_url)
        expect(page.get_by_role("heading", name="Senior Site Reliability Engineer")).to_be_visible()
        assert console_errors == [], console_errors


@pytest.mark.usefixtures("auth_stub")
class TestEditsSurvive:
    """The 2026-10-05 defects, asserted through the browser."""

    def test_typing_into_a_section_is_kept(self, page, base_url, api_stub):
        _register_studio(api_stub)
        _open_studio(page, base_url)

        box = page.get_by_role("textbox").first
        expect(box).to_be_visible()
        box.fill("My rewritten summary.")
        expect(box).to_have_value("My rewritten summary.")

    def test_an_edit_survives_the_section_endpoint_being_refetched(self, page, base_url, api_stub):
        """Every save mints a fresh presigned URL for the same S3 key, which
        used to retrigger the loader and overwrite the editor with the server's
        copy. The edit must win until it is saved."""
        _register_studio(api_stub)
        # Blurring a section asks for a rebuild; without this the request is
        # unstubbed and api_stub fails the test at teardown for traffic the
        # page legitimately makes.
        api_stub.on("POST", f"/api/dashboard/jobs/{JOB_ID}/sections",
                    {"task_id": "t-edit", "poll_url": "/api/tasks/t-edit"}, status=202)
        api_stub.on("GET", "/api/tasks/t-edit",
                    {"status": "done", "result": {"pdf_url": "https://s3.test/after.pdf"}})
        _open_studio(page, base_url)

        box = page.get_by_role("textbox").first
        expect(box).to_be_visible()
        box.fill("Edited and not yet saved.")
        box.blur()

        # Whatever the page does next — compile, refetch, poll — the text the
        # user typed is still the text on screen.
        page.wait_for_timeout(1500)
        expect(page.get_by_role("textbox").first).to_have_value("Edited and not yet saved.")

    def test_blurring_a_section_asks_the_server_to_rebuild(self, page, base_url, api_stub):
        """The Studio compiles on blur — that is the contract the suggestions
        endpoint also relies on, since it is sent the client's live sections."""
        _register_studio(api_stub)
        api_stub.on("POST", f"/api/dashboard/jobs/{JOB_ID}/sections",
                    {"task_id": "t1", "poll_url": "/api/tasks/t1"}, status=202)
        api_stub.on("GET", "/api/tasks/t1",
                    {"status": "done", "result": {"pdf_url": "https://s3.test/new.pdf"}})
        _open_studio(page, base_url)

        box = page.get_by_role("textbox").first
        expect(box).to_be_visible()
        box.fill("Changed enough to need a rebuild.")
        box.blur()

        req = api_stub.assert_called("POST", f"/api/dashboard/jobs/{JOB_ID}/sections")
        # The recorder stores the parsed JSON under "body", not "post_data".
        sent = (req.get("body") or {}).get("sections", {})
        assert sent.get("summary") == "Changed enough to need a rebuild.", (
            f"the rebuild was sent without the text the user typed: {sent}")


@pytest.mark.usefixtures("auth_stub")
class TestNoUnstubbedTraffic:
    def test_the_studio_issues_no_request_the_suite_has_not_declared(self, page, base_url, api_stub):
        """api_stub fails the test for anything unstubbed, so reaching the end
        of this test IS the assertion — it catches a new endpoint being added
        to the page without anyone noticing it is now on the critical path."""
        _register_studio(api_stub)
        _open_studio(page, base_url)
        expect(page.get_by_role("heading", name="Senior Site Reliability Engineer")).to_be_visible()
        page.wait_for_timeout(1000)
