"""Journey 13: GDPR data export — the ZIP must contain THIS user's data."""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.e2e_live._live import ARTIFACTS, api_call, assert_status, ensure_signed_in, read_zip, require


def test_data_export_zip(live):
    require(live.state, "onboarded", "onboarding did not complete")
    ensure_signed_in(live)
    page = live.page
    live.goto("/data-export")
    expect(page.get_by_role("heading", name="Data & Privacy")).to_be_visible()
    with live.rec.step("Export My Data -> GET /api/gdpr/export -> ZIP download"):
        with page.expect_download(timeout=120_000) as dl:
            resp = api_call(live, "GET", r"^/api/gdpr/export$",
                            lambda: page.get_by_role("button", name="Export My Data").click(), timeout_s=120)
        assert_status(resp, 200, "export")
        path = ARTIFACTS / "export.zip"
        dl.value.save_as(path)
        expect(page.get_by_text("Your data has been downloaded successfully.")).to_be_visible()
    with live.rec.step("ZIP contains this user's profile, résumé, search config and jobs"):
        files = read_zip(path.read_bytes())
        for name in ("profile.json", "resumes.json", "search_config.json", "jobs.json", "runs.json"):
            assert name in files, f"{name} missing from export; got {sorted(files)}"
        profile = json.loads(files["profile.json"])
        assert profile.get("id") == live.account["id"], "export is not this user's profile"
        current = live.api.json("/api/profile")["full_name"]
        assert profile.get("name") == current, (profile.get("name"), current)
        resumes = json.loads(files["resumes.json"])
        assert len(resumes) >= 1 and any("documentclass" in (r.get("tex_content") or "") for r in resumes), \
            "résumé missing from export"
        cfgj = json.loads(files["search_config.json"])
        assert "Site Reliability Engineer" in json.dumps(cfgj), "search config missing queries"
        jobs = json.loads(files["jobs.json"])
        if live.state.get("job_id"):
            assert any(j.get("job_id") == live.state["job_id"] for j in jobs), \
                f"the tailored job is missing from jobs.json ({len(jobs)} jobs exported)"
        live.rec.note(f"export: {len(jobs)} job(s), {len(resumes)} résumé(s), files={sorted(files)}")
