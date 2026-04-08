"""Playwright UI E2E test — tests the actual frontend in a real browser.

Checks: pages load, no console errors, key elements render, navigation works.
Run with: python tests/e2e/test_ui_playwright.py
"""

import sys
import time

from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"
# We need to inject auth — Supabase stores JWT in localStorage
JWT_SECRET = "CYDI93+ZN8WFBDdTmtioU74bS92a6ynC0PHbQyZkDiexyoceSZeoe8cnbPeDu5fj6xZ+0W5fHgy5W3YaTDAStg=="
USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"


def make_token():
    import jwt
    from datetime import datetime, timedelta

    return jwt.encode(
        {
            "sub": USER_ID,
            "role": "authenticated",
            "iss": "supabase",
            "aud": "authenticated",
            "email": "254utkarsh@gmail.com",
            "iat": int(datetime.now().timestamp()),
            "exp": int((datetime.now() + timedelta(hours=24)).timestamp()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


class Results:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.console_errors = []

    def ok(self, name):
        self.passed += 1
        print(f"  ✓ {name}")

    def fail(self, name, detail):
        self.failed += 1
        self.errors.append((name, detail))
        print(f"  ✗ {name} — {detail}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'=' * 60}")
        print(f"UI TESTS: {self.passed}/{total} passed, {self.failed} failed")
        if self.console_errors:
            print(f"\nBROWSER CONSOLE ERRORS ({len(self.console_errors)}):")
            for msg in self.console_errors[:10]:
                print(f"  ! {msg[:120]}")
        if self.errors:
            print("\nFAILURES:")
            for name, detail in self.errors:
                print(f"  ✗ {name}: {detail}")
        print(f"{'=' * 60}")
        return self.failed == 0


def run_tests():
    results = Results()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # Capture console errors
        page.on("console", lambda msg: results.console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" and "favicon" not in msg.text else None)

        # ================================================================
        # 1. LOGIN PAGE / AUTH
        # ================================================================
        print("\n=== 1. LOGIN / AUTH ===")
        page.goto(FRONTEND_URL)
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/naukri_01_landing.png")

        # Check if we're on the login page or redirected
        url = page.url
        if "login" in url.lower() or page.locator("text=Sign in").count() > 0 or page.locator("text=Log in").count() > 0:
            results.ok("Login page renders")

            # Inject auth token using Supabase's exact localStorage key format
            token = make_token()
            page.evaluate(f"""() => {{
                const session = {{
                    "currentSession": {{
                        "access_token": "{token}",
                        "token_type": "bearer",
                        "expires_in": 86400,
                        "expires_at": {int(time.time()) + 86400},
                        "refresh_token": "fake-refresh-token",
                        "user": {{
                            "id": "{USER_ID}",
                            "aud": "authenticated",
                            "role": "authenticated",
                            "email": "254utkarsh@gmail.com",
                            "email_confirmed_at": "2026-03-25T16:25:03.591143Z",
                            "created_at": "2026-03-25T16:25:03.591143Z",
                            "updated_at": "2026-03-25T16:25:03.591143Z"
                        }}
                    }},
                    "expiresAt": {int(time.time()) + 86400}
                }};
                // Supabase v2 uses this key format
                localStorage.setItem('sb-fzxdkvurtsqcflqidqto-auth-token', JSON.stringify(session));
                // Also try the v1 format
                localStorage.setItem('supabase.auth.token', JSON.stringify({{currentSession: session.currentSession}}));
            }}""")
            page.reload()
            page.wait_for_load_state("networkidle")
            time.sleep(2)
        else:
            results.ok("Already past login (or no auth gate)")

        # ================================================================
        # 2. DASHBOARD
        # ================================================================
        print("\n=== 2. DASHBOARD ===")
        page.goto(f"{FRONTEND_URL}/dashboard")
        page.wait_for_load_state("networkidle")
        time.sleep(3)
        page.screenshot(path="/tmp/naukri_02_dashboard.png")

        # Check key dashboard elements
        if page.locator("text=Job Dashboard").count() > 0 or page.locator("text=TOTAL JOBS").count() > 0:
            results.ok("Dashboard page renders")
        else:
            results.fail("Dashboard render", f"Key text not found, URL={page.url}")

        # Pipeline status bar
        if page.locator("text=Pipeline").count() > 0:
            results.ok("Pipeline status bar visible")
        else:
            results.fail("Pipeline status", "Not visible")

        # Tier tabs
        tier_tabs = page.locator("text=Must Apply").count() + page.locator("text=Strong Match").count()
        if tier_tabs > 0:
            results.ok("Tier filter tabs visible")
        else:
            results.fail("Tier tabs", "Not found")

        # Stats bar
        if page.locator("text=TOTAL JOBS").count() > 0:
            results.ok("Stats bar visible")
        else:
            results.fail("Stats bar", "Not found")

        # Job table
        job_rows = page.locator("table tbody tr").count() if page.locator("table").count() > 0 else 0
        if job_rows > 0:
            results.ok(f"Job table — {job_rows} rows visible")
        else:
            results.fail("Job table", "No rows visible")

        # Click a tier tab
        s_tab = page.locator("text=Must Apply").first
        if s_tab.count() > 0:
            s_tab.click()
            time.sleep(2)
            page.screenshot(path="/tmp/naukri_03_s_tier.png")
            results.ok("S-tier tab clickable")

        # ================================================================
        # 3. SETTINGS PAGE
        # ================================================================
        print("\n=== 3. SETTINGS ===")
        page.goto(f"{FRONTEND_URL}/settings")
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        page.screenshot(path="/tmp/naukri_04_settings.png")

        if page.locator("text=Job Sources").count() > 0:
            results.ok("Settings page renders — Job Sources visible")
        else:
            results.fail("Settings render", "Job Sources section not found")

        # Check for disabled scrapers
        if page.locator("text=UK only").count() > 0 or page.locator("text=disabled").count() > 0:
            results.ok("Adzuna shown as disabled")
        else:
            results.fail("Adzuna disabled state", "Not showing disabled indicator")

        # Check Greenhouse/Ashby present
        if page.locator("text=Greenhouse").count() > 0:
            results.ok("Greenhouse listed in sources")
        else:
            results.fail("Greenhouse source", "Not listed")

        # ================================================================
        # 4. JOB WORKSPACE
        # ================================================================
        print("\n=== 4. JOB WORKSPACE ===")

        # Navigate back to dashboard and click a job
        page.goto(f"{FRONTEND_URL}/dashboard")
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        # Click first job link
        first_job_link = page.locator("table tbody tr a").first
        if first_job_link.count() > 0:
            first_job_link.click()
            page.wait_for_load_state("networkidle")
            time.sleep(3)
            page.screenshot(path="/tmp/naukri_05_workspace.png")

            url = page.url
            if "/jobs/" in url:
                results.ok(f"Job workspace opened — {url.split('/jobs/')[-1][:20]}")

                # Check tabs
                tabs = ["Overview", "Research", "Resume", "Editor", "Cover Letter", "Contacts", "Interview Prep"]
                for tab_name in tabs:
                    if page.locator(f"text={tab_name}").count() > 0:
                        results.ok(f"Tab visible: {tab_name}")
                    else:
                        results.fail(f"Tab: {tab_name}", "Not found")

                # Click Research tab
                research_tab = page.locator("text=Research").first
                if research_tab.count() > 0:
                    research_tab.click()
                    time.sleep(1)
                    page.screenshot(path="/tmp/naukri_06_research.png")
                    if page.locator("text=Quick Links").count() > 0 or page.locator("text=Generate Research").count() > 0:
                        results.ok("Research tab has content")
                    else:
                        results.fail("Research tab", "No content rendered")

                # Click Contacts tab
                contacts_tab = page.locator("text=Contacts").first
                if contacts_tab.count() > 0:
                    contacts_tab.click()
                    time.sleep(1)
                    page.screenshot(path="/tmp/naukri_07_contacts.png")
                    results.ok("Contacts tab clickable")

                # Click Interview Prep tab
                prep_tab = page.locator("text=Interview Prep").first
                if prep_tab.count() > 0:
                    prep_tab.click()
                    time.sleep(1)
                    page.screenshot(path="/tmp/naukri_08_prep.png")
                    if page.locator("text=Generate Prep").count() > 0 or page.locator("text=STAR").count() > 0:
                        results.ok("Prep tab has content")
                    else:
                        results.fail("Prep tab", "No content rendered")
            else:
                results.fail("Job workspace navigation", f"URL doesn't contain /jobs/: {url}")
        else:
            results.fail("Job table click", "No job links found in table")

        # ================================================================
        # 5. ADD JOB PAGE
        # ================================================================
        print("\n=== 5. ADD JOB ===")
        page.goto(f"{FRONTEND_URL}/add-job")
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        page.screenshot(path="/tmp/naukri_09_addjob.png")

        if page.locator("text=Add Job").count() > 0 or page.locator("textarea").count() > 0:
            results.ok("Add Job page renders")
        else:
            results.fail("Add Job render", "Page content not found")

        # ================================================================
        # 6. CONSOLE ERROR SUMMARY
        # ================================================================
        print("\n=== 6. BROWSER CONSOLE ===")
        real_errors = [e for e in results.console_errors if "favicon" not in e and "websocket" not in e.lower() and "HMR" not in e and "404" not in e]
        if len(real_errors) == 0:
            results.ok("No critical console errors")
        else:
            results.fail("Console errors", f"{len(real_errors)} errors found")

        browser.close()

    return results.summary()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
