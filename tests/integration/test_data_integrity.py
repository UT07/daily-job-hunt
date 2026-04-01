"""Integration Tests — Data Integrity (C2).

Validates that scraper output matches the jobs_raw schema, normalizer
output is correct for each source, job_hash uniqueness holds, and
score_batch creates valid job records.

All Supabase calls are mocked. These tests verify data shape and integrity,
not network connectivity.

Run: python -m pytest tests/integration/ -v
"""
import json
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# jobs_raw schema definition (from db/migrations/003_phase2e_tables.sql)
# ---------------------------------------------------------------------------

JOBS_RAW_REQUIRED_FIELDS = {"job_hash", "title", "company", "source"}
JOBS_RAW_OPTIONAL_FIELDS = {
    "description", "location", "apply_url",
    "experience_level", "job_type", "query_hash",
}
JOBS_RAW_ALL_FIELDS = JOBS_RAW_REQUIRED_FIELDS | JOBS_RAW_OPTIONAL_FIELDS

# jobs table required fields (from score_batch insert)
JOBS_TABLE_REQUIRED_FIELDS = {
    "job_id", "user_id", "job_hash", "title", "company",
    "match_score", "ats_score", "hiring_manager_score", "tech_recruiter_score",
    "first_seen",
}
JOBS_TABLE_OPTIONAL_FIELDS = {
    "description", "location", "apply_url", "source",
    "key_matches", "gaps", "match_reasoning",
}


# ---------------------------------------------------------------------------
# C2.1: Scraper output matches jobs_raw schema
# ---------------------------------------------------------------------------

class TestScraperOutputSchema:
    """Verify normalizer output conforms to jobs_raw table schema."""

    @pytest.mark.integration
    def test_normalize_job_returns_all_required_fields(self):
        """normalize_job must return all required fields for a valid input."""
        from normalizers import normalize_job

        raw = {
            "title": "Software Engineer",
            "company": "TestCorp",
            "description": "A great job doing great things.",
            "location": "Dublin",
            "url": "https://example.com/jobs/1",
        }
        result = normalize_job(raw, source="linkedin", query_hash="q1")

        assert result is not None
        for field in JOBS_RAW_REQUIRED_FIELDS:
            assert field in result, f"Missing required field: {field}"
            assert result[field], f"Required field '{field}' is empty"

    @pytest.mark.integration
    def test_normalize_job_returns_none_for_missing_title(self):
        """Jobs with no title should be discarded (return None)."""
        from normalizers import normalize_job

        raw = {"company": "TestCorp", "description": "..."}
        result = normalize_job(raw, source="linkedin")
        assert result is None

    @pytest.mark.integration
    def test_normalize_job_returns_none_for_missing_company(self):
        """Jobs with no company should be discarded (return None)."""
        from normalizers import normalize_job

        raw = {"title": "Engineer", "description": "..."}
        result = normalize_job(raw, source="linkedin")
        assert result is None

    @pytest.mark.integration
    def test_normalize_job_strips_html_from_description(self):
        """HTML tags in description should be stripped to newlines."""
        from normalizers import normalize_job

        raw = {
            "title": "Engineer",
            "company": "Corp",
            "description": "<p>Hello</p><br><b>World</b>",
        }
        result = normalize_job(raw, source="test")
        assert "<p>" not in result["description"]
        assert "<b>" not in result["description"]

    @pytest.mark.integration
    def test_normalize_job_truncates_long_fields(self):
        """Fields should be truncated to schema limits."""
        from normalizers import normalize_job

        raw = {
            "title": "A" * 1000,
            "company": "B" * 500,
            "description": "C" * 20000,
            "location": "D" * 500,
            "url": "https://example.com/" + "E" * 2000,
        }
        result = normalize_job(raw, source="test")
        assert len(result["title"]) <= 500
        assert len(result["company"]) <= 200
        assert len(result["description"]) <= 10000
        assert len(result["location"]) <= 200
        assert len(result["apply_url"]) <= 1000

    @pytest.mark.integration
    def test_normalize_job_unescapes_html_entities(self):
        """HTML entities like &amp; should be unescaped."""
        from normalizers import normalize_job

        raw = {
            "title": "R&amp;D Engineer",
            "company": "Test &amp; Co",
            "description": "Work with &lt;code&gt;",
        }
        result = normalize_job(raw, source="test")
        assert result["title"] == "R&D Engineer"
        assert result["company"] == "Test & Co"


# ---------------------------------------------------------------------------
# C2.2: Per-source normalizer tests
# ---------------------------------------------------------------------------

class TestLinkedInNormalizer:
    """Verify normalize_linkedin handles LinkedIn scraper output."""

    @pytest.mark.integration
    def test_normalizes_standard_linkedin_item(self):
        from normalizers import normalize_linkedin

        items = [{
            "title": "Backend Engineer",
            "companyName": "TechCorp",
            "descriptionText": "Build APIs with Python.",
            "location": "Dublin, Ireland",
            "link": "https://linkedin.com/jobs/123",
            "experienceLevel": "Mid-Senior",
            "employmentType": "Full-time",
        }]
        results = normalize_linkedin(items, query_hash="q1")
        assert len(results) == 1
        assert results[0]["source"] == "linkedin"
        assert results[0]["title"] == "Backend Engineer"
        assert results[0]["company"] == "TechCorp"

    @pytest.mark.integration
    def test_skips_items_without_title(self):
        from normalizers import normalize_linkedin

        items = [
            {"companyName": "Corp", "descriptionText": "..."},
        ]
        results = normalize_linkedin(items, query_hash="q1")
        assert len(results) == 0


class TestIndeedNormalizer:
    """Verify normalize_indeed handles Indeed scraper output."""

    @pytest.mark.integration
    def test_normalizes_indeed_item_with_positionName(self):
        from normalizers import normalize_indeed

        items = [{
            "positionName": "Full Stack Developer",
            "company": "StartupXYZ",
            "description": "React + Node.js role.",
            "location": "Remote",
            "url": "https://indeed.com/jobs/456",
            "jobType": "Full-time",
        }]
        results = normalize_indeed(items, query_hash="q2")
        assert len(results) == 1
        assert results[0]["source"] == "indeed"
        assert results[0]["title"] == "Full Stack Developer"


class TestGlassdoorNormalizer:
    """Verify normalize_glassdoor handles Glassdoor scraper output."""

    @pytest.mark.integration
    def test_normalizes_glassdoor_nested_company(self):
        from normalizers import normalize_glassdoor

        items = [{
            "title": "DevOps Engineer",
            "company": {"companyName": "FinServLtd"},
            "description_text": "Kubernetes and Terraform.",
            "location_city": "Dublin",
            "location_state": "Leinster",
            "location_country": "Ireland",
            "jobUrl": "https://glassdoor.com/jobs/789",
        }]
        results = normalize_glassdoor(items, query_hash="q3")
        assert len(results) == 1
        assert results[0]["source"] == "glassdoor"
        assert results[0]["company"] == "FinServLtd"
        assert "Dublin" in results[0]["location"]

    @pytest.mark.integration
    def test_handles_string_company_field(self):
        """Some Glassdoor items have company as a string, not dict."""
        from normalizers import normalize_glassdoor

        items = [{
            "title": "Engineer",
            "company": "PlainStringCo",
            "description_text": "Description.",
            "location_city": "Cork",
            "jobUrl": "https://glassdoor.com/jobs/101",
        }]
        results = normalize_glassdoor(items, query_hash="q3")
        assert len(results) == 1
        assert results[0]["company"] == "PlainStringCo"


class TestAdzunaNormalizer:
    """Verify normalize_adzuna handles Adzuna API response."""

    @pytest.mark.integration
    def test_normalizes_adzuna_nested_fields(self):
        from normalizers import normalize_adzuna

        items = [{
            "title": "Data Engineer",
            "company": {"display_name": "DataCo"},
            "description": "Build data pipelines with Spark.",
            "location": {"display_name": "Dublin, Leinster"},
            "redirect_url": "https://adzuna.ie/jobs/222",
        }]
        results = normalize_adzuna(items, query_hash="q4")
        assert len(results) == 1
        assert results[0]["source"] == "adzuna"
        assert results[0]["company"] == "DataCo"


class TestHNNormalizer:
    """Verify normalize_hn handles HN Who Is Hiring output."""

    @pytest.mark.integration
    def test_normalizes_hn_item(self):
        from normalizers import normalize_hn

        items = [{
            "title": "Senior Go Engineer",
            "company": "HN Startup",
            "description": "Remote Go developer needed.",
            "location": "Remote",
            "url": "https://hn-startup.com/jobs",
        }]
        results = normalize_hn(items, query_hash="q5")
        assert len(results) == 1
        assert results[0]["source"] == "hn_hiring"


class TestYCNormalizer:
    """YC jobs go through normalize_generic_web."""

    @pytest.mark.integration
    def test_normalizes_yc_via_generic_web(self):
        from normalizers import normalize_generic_web

        items = [{
            "title": "Founding Engineer",
            "company": "YC Startup W24",
            "description": "Join our seed-stage startup.",
            "location": "San Francisco, CA",
            "url": "https://ycstartup.com/jobs",
        }]
        results = normalize_generic_web(items, source="yc", query_hash="q6")
        assert len(results) == 1
        assert results[0]["source"] == "yc"


# ---------------------------------------------------------------------------
# C2.3: job_hash uniqueness across sources
# ---------------------------------------------------------------------------

class TestJobHashUniqueness:
    """Verify job_hash is deterministic and unique across different sources."""

    @pytest.mark.integration
    def test_same_job_same_hash(self):
        """Same company+title+description should produce the same hash."""
        from normalizers import normalize_job

        raw = {"title": "Engineer", "company": "Corp", "description": "Build things."}
        h1 = normalize_job(raw, source="linkedin")["job_hash"]
        h2 = normalize_job(raw, source="linkedin")["job_hash"]
        assert h1 == h2

    @pytest.mark.integration
    def test_different_source_same_hash_for_same_job(self):
        """Same job posted on different boards should produce the same hash
        (hash is based on company+title+description, not source)."""
        from normalizers import normalize_job

        raw = {"title": "Engineer", "company": "Corp", "description": "Build things."}
        h_linkedin = normalize_job(raw, source="linkedin")["job_hash"]
        h_indeed = normalize_job(raw, source="indeed")["job_hash"]
        assert h_linkedin == h_indeed

    @pytest.mark.integration
    def test_different_jobs_different_hashes(self):
        """Different job titles at same company should produce different hashes."""
        from normalizers import normalize_job

        h1 = normalize_job(
            {"title": "Frontend Engineer", "company": "Corp", "description": "React role."},
            source="linkedin",
        )["job_hash"]
        h2 = normalize_job(
            {"title": "Backend Engineer", "company": "Corp", "description": "Python role."},
            source="linkedin",
        )["job_hash"]
        assert h1 != h2

    @pytest.mark.integration
    def test_hash_is_md5_hex(self):
        """job_hash should be a valid 32-char MD5 hex digest."""
        from normalizers import normalize_job

        result = normalize_job(
            {"title": "Eng", "company": "Co", "description": "Test"},
            source="test",
        )
        h = result["job_hash"]
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    @pytest.mark.integration
    def test_hash_uses_lowercase_for_case_insensitive_dedup(self):
        """Hash should normalize case for deduplication."""
        from normalizers import normalize_job

        h1 = normalize_job(
            {"title": "ENGINEER", "company": "CORP", "description": "BUILD THINGS."},
            source="test",
        )["job_hash"]
        h2 = normalize_job(
            {"title": "engineer", "company": "corp", "description": "build things."},
            source="test",
        )["job_hash"]
        assert h1 == h2


# ---------------------------------------------------------------------------
# C2.4: score_batch creates valid job records
# ---------------------------------------------------------------------------

class TestScoreBatchJobRecords:
    """Verify score_batch Lambda inserts records with all required fields."""

    SAMPLE_JOB_RAW = {
        "job_hash": "abc123",
        "title": "Python Developer",
        "company": "TestCorp",
        "description": "Build Python APIs.",
        "location": "Dublin",
        "apply_url": "https://testcorp.com/jobs/1",
        "source": "linkedin",
    }

    SAMPLE_RESUME = {"tex_content": r"\documentclass{article}\begin{document}Skills: Python\end{document}"}

    VALID_AI_RESPONSE = json.dumps({
        "match_score": 85,
        "ats_score": 82,
        "hiring_manager_score": 86,
        "tech_recruiter_score": 87,
        "reasoning": "Good Python match.",
        "key_matches": ["Python", "API"],
        "gaps": ["AWS experience"],
    })

    @pytest.mark.integration
    def test_insert_payload_has_all_required_fields(self):
        """The job record inserted into Supabase must have all required fields."""
        insert_calls = []

        # Build mock Supabase
        mock_db = MagicMock()

        raw_chain = MagicMock()
        raw_chain.select.return_value = raw_chain
        raw_chain.in_.return_value = raw_chain
        raw_result = MagicMock()
        raw_result.data = [self.SAMPLE_JOB_RAW]
        raw_chain.execute.return_value = raw_result

        resume_chain = MagicMock()
        resume_chain.select.return_value = resume_chain
        resume_chain.eq.return_value = resume_chain
        resume_chain.order.return_value = resume_chain
        resume_chain.limit.return_value = resume_chain
        resume_result = MagicMock()
        resume_result.data = [self.SAMPLE_RESUME]
        resume_chain.execute.return_value = resume_result

        insert_chain = MagicMock()

        def capture_insert(data):
            insert_calls.append(data)
            return insert_chain

        insert_chain.insert = capture_insert
        insert_chain.execute.return_value = MagicMock()

        def table_router(name):
            if name == "jobs_raw":
                return raw_chain
            elif name == "user_resumes":
                return resume_chain
            elif name == "jobs":
                return insert_chain
            return MagicMock()

        mock_db.table.side_effect = table_router

        with patch("score_batch.get_supabase", return_value=mock_db), \
             patch("score_batch.ai_complete_cached", return_value=self.VALID_AI_RESPONSE):
            import score_batch
            result = score_batch.handler(
                {"user_id": "user-1", "new_job_hashes": ["abc123"], "min_match_score": 60},
                None,
            )

        assert result["matched_count"] == 1
        assert len(insert_calls) == 1

        record = insert_calls[0]
        for field in JOBS_TABLE_REQUIRED_FIELDS:
            assert field in record, f"Missing required field in job record: {field}"

    @pytest.mark.integration
    def test_insert_payload_scores_are_numeric(self):
        """Score fields in the inserted record must be numeric."""
        insert_calls = []

        mock_db = MagicMock()
        raw_chain = MagicMock()
        raw_chain.select.return_value = raw_chain
        raw_chain.in_.return_value = raw_chain
        raw_result = MagicMock()
        raw_result.data = [self.SAMPLE_JOB_RAW]
        raw_chain.execute.return_value = raw_result

        resume_chain = MagicMock()
        resume_chain.select.return_value = resume_chain
        resume_chain.eq.return_value = resume_chain
        resume_chain.order.return_value = resume_chain
        resume_chain.limit.return_value = resume_chain
        resume_result = MagicMock()
        resume_result.data = [self.SAMPLE_RESUME]
        resume_chain.execute.return_value = resume_result

        insert_chain = MagicMock()

        def capture_insert(data):
            insert_calls.append(data)
            return insert_chain

        insert_chain.insert = capture_insert
        insert_chain.execute.return_value = MagicMock()

        def table_router(name):
            if name == "jobs_raw":
                return raw_chain
            elif name == "user_resumes":
                return resume_chain
            elif name == "jobs":
                return insert_chain
            return MagicMock()

        mock_db.table.side_effect = table_router

        with patch("score_batch.get_supabase", return_value=mock_db), \
             patch("score_batch.ai_complete_cached", return_value=self.VALID_AI_RESPONSE):
            import score_batch
            score_batch.handler(
                {"user_id": "user-1", "new_job_hashes": ["abc123"], "min_match_score": 60},
                None,
            )

        record = insert_calls[0]
        for score_field in ("match_score", "ats_score", "hiring_manager_score", "tech_recruiter_score"):
            val = record[score_field]
            assert isinstance(val, (int, float)), f"{score_field} should be numeric, got {type(val)}"
            assert 0 <= val <= 100, f"{score_field}={val} out of [0, 100]"

    @pytest.mark.integration
    def test_insert_payload_key_matches_and_gaps_are_lists(self):
        """key_matches and gaps must be lists."""
        insert_calls = []

        mock_db = MagicMock()
        raw_chain = MagicMock()
        raw_chain.select.return_value = raw_chain
        raw_chain.in_.return_value = raw_chain
        raw_result = MagicMock()
        raw_result.data = [self.SAMPLE_JOB_RAW]
        raw_chain.execute.return_value = raw_result

        resume_chain = MagicMock()
        resume_chain.select.return_value = resume_chain
        resume_chain.eq.return_value = resume_chain
        resume_chain.order.return_value = resume_chain
        resume_chain.limit.return_value = resume_chain
        resume_result = MagicMock()
        resume_result.data = [self.SAMPLE_RESUME]
        resume_chain.execute.return_value = resume_result

        insert_chain = MagicMock()

        def capture_insert(data):
            insert_calls.append(data)
            return insert_chain

        insert_chain.insert = capture_insert
        insert_chain.execute.return_value = MagicMock()

        def table_router(name):
            if name == "jobs_raw":
                return raw_chain
            elif name == "user_resumes":
                return resume_chain
            elif name == "jobs":
                return insert_chain
            return MagicMock()

        mock_db.table.side_effect = table_router

        with patch("score_batch.get_supabase", return_value=mock_db), \
             patch("score_batch.ai_complete_cached", return_value=self.VALID_AI_RESPONSE):
            import score_batch
            score_batch.handler(
                {"user_id": "user-1", "new_job_hashes": ["abc123"], "min_match_score": 60},
                None,
            )

        record = insert_calls[0]
        assert isinstance(record["key_matches"], list), "key_matches must be a list"
        assert isinstance(record["gaps"], list), "gaps must be a list"
        assert "Python" in record["key_matches"]
        assert "AWS experience" in record["gaps"]

    @pytest.mark.integration
    def test_db_insert_failure_does_not_crash(self):
        """If Supabase insert fails, score_batch should continue without crashing."""
        mock_db = MagicMock()
        raw_chain = MagicMock()
        raw_chain.select.return_value = raw_chain
        raw_chain.in_.return_value = raw_chain
        raw_result = MagicMock()
        raw_result.data = [self.SAMPLE_JOB_RAW]
        raw_chain.execute.return_value = raw_result

        resume_chain = MagicMock()
        resume_chain.select.return_value = resume_chain
        resume_chain.eq.return_value = resume_chain
        resume_chain.order.return_value = resume_chain
        resume_chain.limit.return_value = resume_chain
        resume_result = MagicMock()
        resume_result.data = [self.SAMPLE_RESUME]
        resume_chain.execute.return_value = resume_result

        insert_chain = MagicMock()
        insert_chain.insert.return_value = insert_chain
        insert_chain.execute.side_effect = Exception("DB write failed")

        def table_router(name):
            if name == "jobs_raw":
                return raw_chain
            elif name == "user_resumes":
                return resume_chain
            elif name == "jobs":
                return insert_chain
            return MagicMock()

        mock_db.table.side_effect = table_router

        with patch("score_batch.get_supabase", return_value=mock_db), \
             patch("score_batch.ai_complete_cached", return_value=self.VALID_AI_RESPONSE):
            import score_batch
            result = score_batch.handler(
                {"user_id": "user-1", "new_job_hashes": ["abc123"], "min_match_score": 60},
                None,
            )

        # Should return empty matches (insert failed) but not raise
        assert result["matched_count"] == 0
