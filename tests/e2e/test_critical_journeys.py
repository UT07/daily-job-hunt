"""Playwright E2E test stubs for critical user journeys.

Run with: npx playwright test (after setup)
These are documented test cases -- implement when frontend is in CI.

Each class covers a distinct user journey through the NaukriBaba web app.
Test names are descriptive enough to serve as a living specification of
expected behavior.
"""
import pytest

# Mark all as skip until Playwright is set up in CI
pytestmark = [
    pytest.mark.skip(reason="Playwright E2E -- implement when frontend is in CI"),
    pytest.mark.e2e,
]


class TestLoginFlow:
    """Supabase Auth login/signup via the frontend."""

    def test_login_with_valid_credentials(self):
        # TODO: Navigate to /login, enter valid email+password, verify redirect
        ...

    def test_login_with_invalid_credentials_shows_error(self):
        # TODO: Enter wrong password, verify error toast appears
        ...

    def test_redirect_to_dashboard_after_login(self):
        # TODO: After successful login, URL should be /dashboard
        ...

    def test_logout_redirects_to_login(self):
        # TODO: Click logout, verify redirect to /login and token cleared
        ...

    def test_signup_creates_account_and_redirects(self):
        # TODO: Fill signup form, verify account created, redirect to onboarding
        ...


class TestDashboard:
    """Main dashboard -- job list, filters, and views."""

    def test_dashboard_loads_jobs(self):
        # TODO: After login, dashboard shows job cards from Supabase
        ...

    def test_filter_by_status(self):
        # TODO: Select "Applied" status filter, verify only applied jobs visible
        ...

    def test_filter_by_source(self):
        # TODO: Select "LinkedIn" source filter, verify only LinkedIn jobs visible
        ...

    def test_card_view_toggle(self):
        # TODO: Toggle between card and table view, verify layout changes
        ...

    def test_pagination(self):
        # TODO: With 20+ jobs, verify page 2 loads different jobs
        ...

    def test_sort_by_match_score(self):
        # TODO: Click match score column, verify descending sort order
        ...

    def test_empty_state_shown_for_new_user(self):
        # TODO: New user with zero jobs sees onboarding prompt, not empty table
        ...


class TestPipelineRun:
    """Triggering and monitoring a pipeline run from the UI."""

    def test_run_pipeline_button_starts_execution(self):
        # TODO: Click "Run Pipeline", verify Step Functions execution starts
        # TODO: Verify run appears in runs history with status "running"
        ...

    def test_pipeline_status_updates_while_running(self):
        # TODO: While pipeline runs, status badge shows progress
        # TODO: Verify polling or websocket updates the UI
        ...

    def test_dashboard_refreshes_after_pipeline_completes(self):
        # TODO: After run completes, new jobs appear in dashboard
        # TODO: Verify run history shows "completed" status
        ...

    def test_pipeline_failure_shows_error_state(self):
        # TODO: If pipeline fails, UI shows error message with details
        ...


class TestAddJob:
    """Manual job addition via paste-JD flow."""

    def test_paste_jd_and_score(self):
        # TODO: Paste a job description text, click Score
        # TODO: Verify 3-perspective scores appear (ATS, HM, TR)
        # TODO: Verify key_matches and gaps are displayed
        ...

    def test_tailor_resume_shows_progress(self):
        # TODO: After scoring, click "Tailor Resume"
        # TODO: Verify progress indicator appears during tailoring
        # TODO: Verify PDF preview loads when done
        ...

    def test_paste_jd_with_url(self):
        # TODO: Paste a URL instead of text, verify scraping and scoring
        ...

    def test_empty_jd_shows_validation_error(self):
        # TODO: Submit empty textarea, verify validation message
        ...


class TestJobWorkspace:
    """Individual job detail page -- tabs and actions."""

    def test_overview_tab_shows_details(self):
        # TODO: Open a job, verify title, company, scores, description visible
        ...

    def test_resume_tab_shows_pdf_preview(self):
        # TODO: Click Resume tab, verify PDF preview iframe or embed loads
        # TODO: Verify download button works
        ...

    def test_cover_letter_tab_shows_pdf(self):
        # TODO: Click Cover Letter tab, verify PDF preview loads
        ...

    def test_inline_editing(self):
        # TODO: Edit a resume section inline, verify save triggers re-compile
        # TODO: Verify updated PDF appears
        ...

    def test_contacts_tab_shows_linkedin_contacts(self):
        # TODO: Click Contacts tab, verify LinkedIn profiles listed
        ...

    def test_status_change_persists(self):
        # TODO: Change application_status to "Applied", reload, verify persisted
        ...


class TestSettings:
    """User settings and profile management."""

    def test_source_toggles_save(self):
        # TODO: Toggle LinkedIn source off, save, verify persisted in search config
        ...

    def test_profile_update(self):
        # TODO: Update name and location, save, verify reflected in profile
        ...

    def test_resume_upload(self):
        # TODO: Upload a new resume file, verify it appears in resume list
        ...

    def test_search_config_update(self):
        # TODO: Change min_match_score to 70, save, verify next run uses it
        ...

    def test_gdpr_export_downloads_zip(self):
        # TODO: Click "Export My Data", verify ZIP file downloads
        # TODO: Verify ZIP contains profile.json, jobs.json, resumes.json
        ...

    def test_gdpr_delete_account(self):
        # TODO: Click "Delete Account", confirm, verify account removed
        # TODO: Verify redirect to login page
        ...
