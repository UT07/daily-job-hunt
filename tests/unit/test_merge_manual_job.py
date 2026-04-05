"""Tests for merge_manual_job and canonical-hash-based dedup in app.py."""
from app import merge_manual_job


class TestMergeManualJob:
    def test_manual_jd_dedup_prefers_manual(self):
        existing = {
            "source": "linkedin",
            "description": "Short desc",
            "title": "Eng",
            "company": "Co",
            "location": "Dublin",
            "apply_url": "https://linkedin.com/jobs/123",
        }
        manual = {
            "source": "manual",
            "description": "Full detailed JD",
            "title": "Eng",
            "company": "Co",
        }
        merged = merge_manual_job(existing, manual)
        assert merged["source"] == "manual"
        assert merged["description"] == "Full detailed JD"

    def test_preserves_existing_fields_when_manual_empty(self):
        existing = {
            "source": "linkedin",
            "description": "Existing desc",
            "title": "Backend Eng",
            "company": "Acme Inc",
            "location": "Dublin",
            "apply_url": "https://linkedin.com/jobs/456",
        }
        manual = {
            "source": "manual",
            "description": "Better description from paste",
        }
        merged = merge_manual_job(existing, manual)
        # Manual wins for source and description
        assert merged["source"] == "manual"
        assert merged["description"] == "Better description from paste"
        # Existing preserved for fields not in manual
        assert merged["title"] == "Backend Eng"
        assert merged["company"] == "Acme Inc"
        assert merged["location"] == "Dublin"
        assert merged["apply_url"] == "https://linkedin.com/jobs/456"

    def test_manual_overwrites_all_provided_fields(self):
        existing = {
            "source": "indeed",
            "description": "Old",
            "title": "Old Title",
            "company": "Old Co",
            "location": "London",
            "apply_url": "https://indeed.com/j/1",
        }
        manual = {
            "source": "manual",
            "description": "New full JD",
            "title": "New Title",
            "company": "New Co",
            "location": "Dublin",
            "apply_url": "https://company.com/careers",
        }
        merged = merge_manual_job(existing, manual)
        assert merged["source"] == "manual"
        assert merged["description"] == "New full JD"
        assert merged["title"] == "New Title"
        assert merged["company"] == "New Co"
        assert merged["location"] == "Dublin"
        assert merged["apply_url"] == "https://company.com/careers"

    def test_does_not_drop_extra_existing_fields(self):
        """Fields not in the merge list (e.g. job_id, match_score) are kept."""
        existing = {
            "job_id": "abc123",
            "source": "linkedin",
            "description": "desc",
            "title": "Eng",
            "company": "Co",
            "match_score": 85,
            "ats_score": 78,
        }
        manual = {"source": "manual", "description": "Better desc"}
        merged = merge_manual_job(existing, manual)
        assert merged["job_id"] == "abc123"
        assert merged["match_score"] == 85
        assert merged["ats_score"] == 78

    def test_empty_manual_string_does_not_overwrite(self):
        """Empty string in manual should NOT overwrite existing (falsy check)."""
        existing = {
            "source": "indeed",
            "description": "Good desc",
            "title": "Eng",
            "company": "Co",
        }
        manual = {"source": "", "description": ""}
        merged = merge_manual_job(existing, manual)
        # Empty strings are falsy, so existing values should be preserved
        assert merged["source"] == "indeed"
        assert merged["description"] == "Good desc"
