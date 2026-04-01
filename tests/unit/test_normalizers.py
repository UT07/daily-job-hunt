"""Unit tests for lambdas/scrapers/normalizers.py."""
import hashlib

from normalizers import (
    normalize_adzuna,
    normalize_generic_web,
    normalize_hn,
    normalize_job,
    normalize_linkedin,
)


class TestNormalizeJob:
    """Tests for the normalize_job function."""

    def test_happy_path(self):
        raw = {
            "title": "Software Engineer",
            "company": "Acme Corp",
            "description": "Build great things.",
            "location": "Dublin, Ireland",
            "url": "https://acme.com/jobs/1",
        }
        result = normalize_job(raw, source="linkedin", query_hash="qhash1")

        assert result is not None
        assert result["title"] == "Software Engineer"
        assert result["company"] == "Acme Corp"
        assert result["description"] == "Build great things."
        assert result["location"] == "Dublin, Ireland"
        assert result["apply_url"] == "https://acme.com/jobs/1"
        assert result["source"] == "linkedin"
        assert result["query_hash"] == "qhash1"
        assert "job_hash" in result

    def test_missing_title_returns_none(self):
        raw = {"company": "Acme Corp", "description": "Build things."}
        assert normalize_job(raw, source="linkedin") is None

    def test_missing_company_returns_none(self):
        raw = {"title": "Software Engineer", "description": "Build things."}
        assert normalize_job(raw, source="linkedin") is None

    def test_empty_dict_returns_none(self):
        assert normalize_job({}, source="linkedin") is None

    def test_html_entities_unescaped_in_title(self):
        raw = {"title": "Senior &amp; Lead Engineer", "company": "Acme &amp; Co"}
        result = normalize_job(raw, source="hn_hiring")
        assert result["title"] == "Senior & Lead Engineer"
        assert result["company"] == "Acme & Co"

    def test_html_entities_unescaped_in_description(self):
        raw = {
            "title": "Engineer",
            "company": "Corp",
            "description": "Salary &gt; 80k &amp; benefits included",
        }
        result = normalize_job(raw, source="adzuna")
        assert "&amp;" not in result["description"]
        assert "&gt;" not in result["description"]
        assert ">" in result["description"]

    def test_html_tags_stripped_from_description(self):
        raw = {
            "title": "Engineer",
            "company": "Corp",
            "description": "<p>Build <strong>great</strong> things.</p>",
        }
        result = normalize_job(raw, source="linkedin")
        assert "<p>" not in result["description"]
        assert "<strong>" not in result["description"]
        assert "Build" in result["description"]
        assert "great" in result["description"]

    def test_long_title_truncated_to_500(self):
        raw = {"title": "A" * 600, "company": "Acme"}
        result = normalize_job(raw, source="linkedin")
        assert len(result["title"]) == 500

    def test_long_company_truncated_to_200(self):
        raw = {"title": "Engineer", "company": "B" * 300}
        result = normalize_job(raw, source="linkedin")
        assert len(result["company"]) == 200

    def test_long_description_truncated_to_10000(self):
        raw = {"title": "Engineer", "company": "Corp", "description": "C" * 12000}
        result = normalize_job(raw, source="linkedin")
        assert len(result["description"]) == 10000

    def test_unicode_title_and_company(self):
        raw = {"title": "Ingénieur Logiciel", "company": "Société Générale"}
        result = normalize_job(raw, source="generic")
        assert result["title"] == "Ingénieur Logiciel"
        assert result["company"] == "Société Générale"

    def test_same_input_same_hash(self):
        raw = {"title": "Engineer", "company": "Corp", "description": "Work hard."}
        result1 = normalize_job(raw, source="linkedin")
        result2 = normalize_job(raw, source="linkedin")
        assert result1["job_hash"] == result2["job_hash"]

    def test_different_input_different_hash(self):
        raw1 = {"title": "Engineer", "company": "CorpA", "description": "Work."}
        raw2 = {"title": "Engineer", "company": "CorpB", "description": "Work."}
        result1 = normalize_job(raw1, source="linkedin")
        result2 = normalize_job(raw2, source="linkedin")
        assert result1["job_hash"] != result2["job_hash"]

    def test_hash_is_md5_of_company_title_description(self):
        raw = {"title": "Engineer", "company": "Corp", "description": "Work hard."}
        result = normalize_job(raw, source="linkedin")
        expected_hash = hashlib.md5(
            "corp|engineer|work hard.".encode()
        ).hexdigest()
        assert result["job_hash"] == expected_hash

    def test_alternative_field_positionName(self):
        raw = {"positionName": "Data Scientist", "company": "DataCo"}
        result = normalize_job(raw, source="indeed")
        assert result["title"] == "Data Scientist"

    def test_alternative_field_companyName(self):
        raw = {"title": "Analyst", "companyName": "FinanceCo"}
        result = normalize_job(raw, source="linkedin")
        assert result["company"] == "FinanceCo"

    def test_alternative_field_text_for_description(self):
        raw = {"title": "Engineer", "company": "Corp", "text": "Build systems."}
        result = normalize_job(raw, source="hn_hiring")
        assert result["description"] == "Build systems."

    def test_alternative_field_city_for_location(self):
        raw = {"title": "Engineer", "company": "Corp", "city": "Cork"}
        result = normalize_job(raw, source="indeed")
        assert result["location"] == "Cork"

    def test_alternative_field_applyUrl(self):
        raw = {"title": "Engineer", "company": "Corp", "applyUrl": "https://apply.example.com"}
        result = normalize_job(raw, source="indeed")
        assert result["apply_url"] == "https://apply.example.com"

    def test_alternative_field_apply_url_underscore(self):
        raw = {"title": "Engineer", "company": "Corp", "apply_url": "https://apply2.example.com"}
        result = normalize_job(raw, source="jobs_ie")
        assert result["apply_url"] == "https://apply2.example.com"

    def test_experience_level_and_job_type_preserved(self):
        raw = {
            "title": "Engineer",
            "company": "Corp",
            "experienceLevel": "senior",
            "jobType": "full_time",
        }
        result = normalize_job(raw, source="linkedin")
        assert result["experience_level"] == "senior"
        assert result["job_type"] == "full_time"

    def test_experience_level_underscore_variant(self):
        raw = {
            "title": "Engineer",
            "company": "Corp",
            "experience_level": "mid",
            "job_type": "contract",
        }
        result = normalize_job(raw, source="linkedin")
        assert result["experience_level"] == "mid"
        assert result["job_type"] == "contract"

    def test_query_hash_defaults_to_empty_string(self):
        raw = {"title": "Engineer", "company": "Corp"}
        result = normalize_job(raw, source="linkedin")
        assert result["query_hash"] == ""

    def test_whitespace_only_title_returns_none(self):
        raw = {"title": "   ", "company": "Corp"}
        assert normalize_job(raw, source="linkedin") is None

    def test_whitespace_only_company_returns_none(self):
        raw = {"title": "Engineer", "company": "   "}
        assert normalize_job(raw, source="linkedin") is None


class TestNormalizeLinkedIn:
    """Tests for normalize_linkedin."""

    def test_happy_path(self):
        items = [
            {
                "title": "Backend Engineer",
                "companyName": "TechStart",
                "description": "Build APIs.",
                "location": "Dublin",
                "url": "https://linkedin.com/jobs/1",
                "experienceLevel": "MID_SENIOR_LEVEL",
                "contractType": "FULL_TIME",
            }
        ]
        results = normalize_linkedin(items, query_hash="q1")
        assert len(results) == 1
        assert results[0]["title"] == "Backend Engineer"
        assert results[0]["company"] == "TechStart"
        assert results[0]["source"] == "linkedin"
        assert results[0]["experience_level"] == "MID_SENIOR_LEVEL"
        assert results[0]["job_type"] == "FULL_TIME"

    def test_empty_list(self):
        assert normalize_linkedin([], query_hash="q1") == []

    def test_skips_invalid_items_missing_title(self):
        items = [
            {"companyName": "OnlyCompany"},
            {"title": "Good Job", "companyName": "GoodCorp"},
        ]
        results = normalize_linkedin(items, query_hash="q1")
        assert len(results) == 1
        assert results[0]["title"] == "Good Job"

    def test_uses_link_field_as_fallback_for_url(self):
        items = [
            {
                "title": "Engineer",
                "companyName": "Corp",
                "link": "https://linkedin.com/jobs/fallback",
            }
        ]
        results = normalize_linkedin(items, query_hash="q1")
        assert results[0]["apply_url"] == "https://linkedin.com/jobs/fallback"

    def test_uses_descriptionHtml_as_fallback(self):
        items = [
            {
                "title": "Engineer",
                "companyName": "Corp",
                "descriptionHtml": "<p>Build things.</p>",
            }
        ]
        results = normalize_linkedin(items, query_hash="q1")
        assert "Build things." in results[0]["description"]


class TestNormalizeAdzuna:
    """Tests for normalize_adzuna."""

    def test_nested_company_and_location_dicts(self):
        items = [
            {
                "title": "Python Developer",
                "company": {"display_name": "Adzuna Corp"},
                "description": "Python role.",
                "location": {"display_name": "London, UK"},
                "redirect_url": "https://adzuna.com/jobs/9",
            }
        ]
        results = normalize_adzuna(items, query_hash="q2")
        assert len(results) == 1
        assert results[0]["company"] == "Adzuna Corp"
        assert results[0]["location"] == "London, UK"
        assert results[0]["apply_url"] == "https://adzuna.com/jobs/9"
        assert results[0]["source"] == "adzuna"

    def test_missing_nested_company_returns_none(self):
        items = [
            {
                "title": "Developer",
                "company": None,
                "location": {"display_name": "Dublin"},
            }
        ]
        results = normalize_adzuna(items, query_hash="q2")
        assert results == []

    def test_missing_nested_location_returns_empty_location(self):
        items = [
            {
                "title": "Developer",
                "company": {"display_name": "Corp"},
                "location": None,
            }
        ]
        results = normalize_adzuna(items, query_hash="q2")
        assert len(results) == 1
        assert results[0]["location"] == ""

    def test_empty_list(self):
        assert normalize_adzuna([], query_hash="q2") == []

    def test_skips_invalid_items(self):
        items = [
            {"company": {"display_name": "Corp"}},  # no title
            {"title": "Good Job", "company": {"display_name": "ValidCorp"}},
        ]
        results = normalize_adzuna(items, query_hash="q2")
        assert len(results) == 1
        assert results[0]["title"] == "Good Job"


class TestNormalizeHN:
    """Tests for normalize_hn."""

    def test_basic_normalization(self):
        items = [
            {
                "title": "HN Hiring | Backend Engineer",
                "company": "StartupX",
                "description": "Remote-friendly Rust shop.",
                "url": "https://startupx.com/jobs",
            }
        ]
        results = normalize_hn(items, query_hash="q3")
        assert len(results) == 1
        assert results[0]["source"] == "hn_hiring"
        assert results[0]["title"] == "HN Hiring | Backend Engineer"
        assert results[0]["company"] == "StartupX"

    def test_empty_list(self):
        assert normalize_hn([], query_hash="q3") == []

    def test_skips_invalid_items(self):
        items = [
            {"description": "No title or company"},
            {"title": "Valid", "company": "ValidCorp"},
        ]
        results = normalize_hn(items, query_hash="q3")
        assert len(results) == 1
        assert results[0]["title"] == "Valid"


class TestNormalizeGenericWeb:
    """Tests for normalize_generic_web."""

    def test_source_param_passed_through(self):
        items = [{"title": "QA Engineer", "company": "IrishJobs Corp"}]
        results = normalize_generic_web(items, source="irishjobs", query_hash="q4")
        assert len(results) == 1
        assert results[0]["source"] == "irishjobs"

    def test_different_sources(self):
        items = [{"title": "Graduate Engineer", "company": "GradCo"}]
        for source in ("gradireland", "irishjobs", "jobs_ie"):
            results = normalize_generic_web(items, source=source, query_hash="q4")
            assert results[0]["source"] == source

    def test_empty_list(self):
        assert normalize_generic_web([], source="irishjobs", query_hash="q4") == []

    def test_skips_invalid_items(self):
        items = [
            {"description": "No title or company"},
            {"title": "Developer", "company": "Corp"},
        ]
        results = normalize_generic_web(items, source="jobs_ie", query_hash="q4")
        assert len(results) == 1
        assert results[0]["company"] == "Corp"
