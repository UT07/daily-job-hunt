"""Unit tests for GradIreland scraper — multi-strategy resilience.

Tests each parsing strategy independently and the URL path fallback logic.
"""
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import requests


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

# JSON-LD structured data (Strategy 1)
_HTML_JSON_LD = """
<html><head>
<script type="application/ld+json">
[
  {
    "@type": "JobPosting",
    "title": "Graduate Software Engineer",
    "hiringOrganization": {"@type": "Organization", "name": "TechCorp"},
    "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Dublin"}},
    "url": "https://gradireland.com/graduate-jobs/techcorp/graduate-software-engineer",
    "datePosted": "2026-04-01",
    "description": "We are looking for a talented graduate engineer to join our growing team in Dublin."
  },
  {
    "@type": "JobPosting",
    "title": "Data Analyst Intern",
    "hiringOrganization": {"@type": "Organization", "name": "DataHouse"},
    "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Cork"}}],
    "url": "https://gradireland.com/graduate-jobs/datahouse/data-analyst-intern",
    "datePosted": "2026-03-28",
    "description": "Entry-level data analyst role."
  }
]
</script>
</head><body><p>Some content</p></body></html>
"""

# JSON-LD with @graph wrapper
_HTML_JSON_LD_GRAPH = """
<html><head>
<script type="application/ld+json">
{
  "@graph": [
    {
      "@type": "JobPosting",
      "title": "Cloud Engineer",
      "hiringOrganization": {"name": "CloudInc"},
      "jobLocation": {"address": {"addressLocality": "Galway"}},
      "url": "https://gradireland.com/graduate-jobs/cloudinc/cloud-engineer"
    }
  ]
}
</script>
</head><body></body></html>
"""

# JSON-LD with string hiringOrganization
_HTML_JSON_LD_STRING_ORG = """
<html><head>
<script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "Backend Developer",
  "hiringOrganization": "StringOrg Ltd",
  "url": "https://gradireland.com/jobs/stringorg/backend-dev"
}
</script>
</head><body></body></html>
"""

# Drupal views-row pattern (Strategy 2)
_HTML_DRUPAL_VIEWS = """
<html><body>
<div class="view-content">
  <div class="views-row views-row-1">
    <div class="views-field views-field-title">
      <span class="field-content"><a href="/graduate-jobs/acme/python-developer">Python Developer</a></span>
    </div>
    <div class="views-field views-field-field-company">
      <span class="field-content">Acme Corp</span>
    </div>
    <div class="views-field views-field-field-location">
      <span class="field-content">Dublin</span>
    </div>
  </div>
  <div class="views-row views-row-2">
    <div class="views-field views-field-title">
      <span class="field-content"><a href="/graduate-jobs/betacorp/devops-engineer">DevOps Engineer</a></span>
    </div>
    <div class="views-field views-field-field-company">
      <span class="field-content">BetaCorp</span>
    </div>
    <div class="views-field views-field-field-location">
      <span class="field-content">Limerick</span>
    </div>
  </div>
</div>
</body></html>
"""

# Drupal field--name-title pattern (alternate Drupal markup)
_HTML_DRUPAL_FIELDS = """
<html><body>
<h2 class="field--name-title"><a href="/graduate-jobs/megacorp/frontend-dev">Frontend Developer</a></h2>
<div class="field--name-field-company">MegaCorp</div>
<div class="field--name-field-location">Dublin</div>

<h2 class="field--name-title"><a href="/graduate-jobs/startup-io/backend-dev">Backend Developer</a></h2>
<div class="field--name-field-company">Startup IO</div>
<div class="field--name-field-location">Remote</div>
</body></html>
"""

# Article / card-based pattern (Strategy 3)
_HTML_ARTICLE_CARDS = """
<html><body>
<article class="job-card">
  <h2><a href="/graduate-jobs/acme/ml-engineer">ML Engineer</a></h2>
  <span class="company">Acme AI</span>
  <span class="location">Dublin</span>
</article>
<article class="job-card">
  <h2><a href="/graduate-jobs/betacorp/sre-role">Site Reliability Engineer</a></h2>
  <span class="company">BetaCorp</span>
  <span class="location">Cork</span>
</article>
</body></html>
"""

# Generic link extraction (Strategy 4/5)
_HTML_GENERIC_LINKS = """
<html><body>
<div>
  <a href="/graduate-jobs/acme/java-developer">Java Developer at Acme</a>
  <a href="/jobs/betacorp/react-developer">React Developer at BetaCorp</a>
</div>
</body></html>
"""

# Page with no jobs at all
_HTML_EMPTY = """
<html><body>
<h1>No results found</h1>
<p>Try broadening your search.</p>
</body></html>
"""

# Page with navigation-only links (should be filtered)
_HTML_NAV_ONLY = """
<html><body>
<a href="/graduate-jobs/browse">Browse all jobs</a>
<a href="/about">About</a>
<a href="/contact">Contact</a>
<a href="/jobs/register">Register</a>
</body></html>
"""


# ---------------------------------------------------------------------------
# Strategy 1: JSON-LD parsing
# ---------------------------------------------------------------------------

class TestJsonLdParsing:
    """Tests for JSON-LD structured data extraction."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_json_ld_extracts_jobs(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_json_ld(_HTML_JSON_LD, "Ireland")
        assert len(jobs) == 2
        assert jobs[0].title == "Graduate Software Engineer"
        assert jobs[0].company == "TechCorp"
        assert jobs[0].location == "Dublin"
        assert jobs[0].source == "gradireland"
        assert "talented graduate" in jobs[0].description
        assert jobs[1].title == "Data Analyst Intern"
        assert jobs[1].company == "DataHouse"
        assert jobs[1].location == "Cork"

    def test_json_ld_graph_wrapper(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_json_ld(_HTML_JSON_LD_GRAPH, "Ireland")
        assert len(jobs) == 1
        assert jobs[0].title == "Cloud Engineer"
        assert jobs[0].company == "CloudInc"
        assert jobs[0].location == "Galway"

    def test_json_ld_string_org(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_json_ld(_HTML_JSON_LD_STRING_ORG, "Ireland")
        assert len(jobs) == 1
        assert jobs[0].company == "StringOrg Ltd"

    def test_json_ld_empty_page(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_json_ld(_HTML_EMPTY, "Ireland")
        assert jobs == []

    def test_json_ld_invalid_json(self):
        html = '<script type="application/ld+json">{invalid json</script>'
        scraper = self._make_scraper()
        jobs = scraper._parse_json_ld(html, "Ireland")
        assert jobs == []

    def test_json_ld_non_job_posting(self):
        html = '<script type="application/ld+json">{"@type": "Organization", "name": "Foo"}</script>'
        scraper = self._make_scraper()
        jobs = scraper._parse_json_ld(html, "Ireland")
        assert jobs == []


# ---------------------------------------------------------------------------
# Strategy 2: Drupal views-row parsing
# ---------------------------------------------------------------------------

class TestDrupalViewsParsing:
    """Tests for Drupal views-row extraction."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_views_row_extracts_jobs(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_drupal_views(_HTML_DRUPAL_VIEWS, "Ireland")
        assert len(jobs) == 2
        assert jobs[0].title == "Python Developer"
        assert jobs[0].company == "Acme Corp"
        assert jobs[0].location == "Dublin"
        assert jobs[1].title == "DevOps Engineer"
        assert jobs[1].company == "BetaCorp"
        assert jobs[1].location == "Limerick"

    def test_views_row_empty_page(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_drupal_views(_HTML_EMPTY, "Ireland")
        assert jobs == []


# ---------------------------------------------------------------------------
# Strategy 3: Article / card-based extraction
# ---------------------------------------------------------------------------

class TestArticleCardParsing:
    """Tests for article/card-based extraction."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_article_cards_extract_jobs(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_article_cards(_HTML_ARTICLE_CARDS, "Ireland")
        assert len(jobs) == 2
        assert jobs[0].title == "ML Engineer"
        assert jobs[1].title == "Site Reliability Engineer"

    def test_article_cards_empty_page(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_article_cards(_HTML_EMPTY, "Ireland")
        assert jobs == []


# ---------------------------------------------------------------------------
# Strategy 4: Href-based job link extraction
# ---------------------------------------------------------------------------

class TestJobLinkParsing:
    """Tests for href-based job link extraction."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_job_links_extract(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_job_links(_HTML_GENERIC_LINKS, "Ireland")
        assert len(jobs) == 2
        titles = [j.title for j in jobs]
        assert "Java Developer at Acme" in titles
        assert "React Developer at BetaCorp" in titles

    def test_job_links_dedup(self):
        """Duplicate hrefs should be collapsed to one job."""
        html = """
        <a href="/graduate-jobs/acme/java-dev">Java Developer</a>
        <a href="/graduate-jobs/acme/java-dev">Java Developer</a>
        """
        scraper = self._make_scraper()
        jobs = scraper._parse_job_links(html, "Ireland")
        assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Navigation link filtering
# ---------------------------------------------------------------------------

class TestSkipFiltering:
    """Tests that navigation/non-job links are filtered out."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_nav_links_filtered(self):
        scraper = self._make_scraper()
        jobs = scraper._parse_job_links(_HTML_NAV_ONLY, "Ireland")
        assert jobs == []

    def test_is_skip_title(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        assert GradIrelandScraper._is_skip_title("Sign In") is True
        assert GradIrelandScraper._is_skip_title("Read More") is True
        assert GradIrelandScraper._is_skip_title("Software Engineer at Acme") is False


# ---------------------------------------------------------------------------
# _company_from_url
# ---------------------------------------------------------------------------

class TestCompanyFromUrl:
    """Tests for company name extraction from URL slugs."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_standard_url(self):
        scraper = self._make_scraper()
        assert scraper._company_from_url(
            "https://gradireland.com/graduate-jobs/acme-corp/python-dev"
        ) == "Acme Corp"

    def test_jobs_path(self):
        scraper = self._make_scraper()
        assert scraper._company_from_url(
            "https://gradireland.com/jobs/tech-solutions/backend-role"
        ) == "Tech Solutions"

    def test_careers_path(self):
        scraper = self._make_scraper()
        assert scraper._company_from_url(
            "https://gradireland.com/careers/big-bank/analyst-position"
        ) == "Big Bank"

    def test_numeric_slug_skipped(self):
        scraper = self._make_scraper()
        assert scraper._company_from_url(
            "https://gradireland.com/jobs/12345"
        ) == ""

    def test_job_title_slug_skipped(self):
        """Slugs containing job-title words should be skipped."""
        scraper = self._make_scraper()
        assert scraper._company_from_url(
            "https://gradireland.com/graduate-jobs/software-engineer-dublin"
        ) == ""


# ---------------------------------------------------------------------------
# URL path fallback logic
# ---------------------------------------------------------------------------

class TestSearchPathFallback:
    """Tests that the scraper tries multiple URL paths."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    @patch("scrapers.gradireland_scraper.requests.get")
    def test_primary_path_404_falls_to_next(self, mock_get):
        """If /graduate-jobs returns 404, try /jobs."""
        scraper = self._make_scraper()

        call_urls = []

        def side_effect(url, **kwargs):
            call_urls.append(url)
            resp = MagicMock()
            if "/graduate-jobs" in url:
                resp.status_code = 404
                resp.text = ""
            elif "/jobs" in url:
                resp.status_code = 200
                resp.text = _HTML_JSON_LD
            else:
                resp.status_code = 404
                resp.text = ""
            return resp

        mock_get.side_effect = side_effect

        jobs = scraper._search_requests("software engineer", "", 1)

        # Should have tried /graduate-jobs first, then /jobs
        assert any("/graduate-jobs" in u for u in call_urls)
        assert any("/jobs" in u for u in call_urls)
        assert len(jobs) == 2

    @patch("scrapers.gradireland_scraper.requests.get")
    def test_primary_path_works_no_fallback(self, mock_get):
        """If /graduate-jobs works, don't try other paths."""
        scraper = self._make_scraper()

        call_urls = []

        def side_effect(url, **kwargs):
            call_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.text = _HTML_JSON_LD
            return resp

        mock_get.side_effect = side_effect

        jobs = scraper._search_requests("software engineer", "", 1)

        assert len(jobs) == 2
        # Should only have called the primary path
        assert all("/graduate-jobs" in u for u in call_urls)

    @patch("scrapers.gradireland_scraper.requests.get")
    def test_all_paths_fail_returns_empty(self, mock_get):
        """If all paths return 404, return empty list."""
        scraper = self._make_scraper()

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 404
            resp.text = ""
            return resp

        mock_get.side_effect = side_effect

        jobs = scraper._search_requests("software engineer", "", 1)
        assert jobs == []


# ---------------------------------------------------------------------------
# Full parse_listings strategy cascade
# ---------------------------------------------------------------------------

class TestParseListingsCascade:
    """Tests that _parse_listings tries strategies in order."""

    def _make_scraper(self):
        from scrapers.gradireland_scraper import GradIrelandScraper
        return GradIrelandScraper(max_pages=1)

    def test_json_ld_preferred_over_html(self):
        """When JSON-LD exists, HTML strategies are not tried."""
        scraper = self._make_scraper()
        jobs = scraper._parse_listings(_HTML_JSON_LD, "Ireland")
        assert len(jobs) == 2
        assert all(j.source == "gradireland" for j in jobs)

    def test_views_row_used_when_no_json_ld(self):
        """When no JSON-LD, Drupal views-row is tried."""
        scraper = self._make_scraper()
        jobs = scraper._parse_listings(_HTML_DRUPAL_VIEWS, "Ireland")
        assert len(jobs) == 2

    def test_article_cards_used_when_no_views(self):
        """When no JSON-LD or views-row, article cards are tried."""
        scraper = self._make_scraper()
        jobs = scraper._parse_listings(_HTML_ARTICLE_CARDS, "Ireland")
        assert len(jobs) == 2

    def test_generic_links_used_as_fallback(self):
        """When nothing else works, generic link extraction is tried."""
        scraper = self._make_scraper()
        jobs = scraper._parse_listings(_HTML_GENERIC_LINKS, "Ireland")
        assert len(jobs) >= 1

    def test_empty_page_returns_empty(self):
        """A page with no job-like content returns []."""
        scraper = self._make_scraper()
        jobs = scraper._parse_listings(_HTML_EMPTY, "Ireland")
        assert jobs == []


# ---------------------------------------------------------------------------
# Zero-yield warning (not crash)
# ---------------------------------------------------------------------------

class TestZeroYieldHandling:
    """Tests that 0-job results log a warning but don't crash."""

    @patch("scrapers.gradireland_scraper.requests.get")
    def test_zero_jobs_warns_not_crashes(self, mock_get):
        from scrapers.gradireland_scraper import GradIrelandScraper

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = _HTML_EMPTY
            return resp

        mock_get.side_effect = side_effect

        scraper = GradIrelandScraper(max_pages=1)
        jobs = scraper.search("software engineer", "Dublin")

        # Should return empty but not raise
        assert jobs == []

    @patch("scrapers.gradireland_scraper.requests.get")
    def test_bot_protection_stops_gracefully(self, mock_get):
        from scrapers.gradireland_scraper import GradIrelandScraper

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body>Access Denied - Bot Protection</body></html>"
            return resp

        mock_get.side_effect = side_effect

        scraper = GradIrelandScraper(max_pages=1)
        jobs = scraper.search("data scientist")
        assert jobs == []

    @patch("scrapers.gradireland_scraper.requests.get")
    def test_network_error_returns_empty(self, mock_get):
        from scrapers.gradireland_scraper import GradIrelandScraper

        mock_get.side_effect = requests.ConnectionError("Connection refused")

        scraper = GradIrelandScraper(max_pages=1)
        jobs = scraper.search("devops engineer")
        assert jobs == []
