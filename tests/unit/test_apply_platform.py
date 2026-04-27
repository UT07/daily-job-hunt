"""Tests for shared.apply_platform.classify_apply_platform.

The classifier is informational — must never raise, must return None for
anything not on the known-platform list.
"""
import pytest

from shared.apply_platform import classify_apply_platform


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/acme/jobs/12345", "greenhouse"),
    ("https://jobs.lever.co/acme-co/abc-123", "lever"),
    ("https://acme.wd5.myworkdayjobs.com/External/job/Dublin/Engineer_R-12345", "workday"),
    ("https://jobs.ashbyhq.com/acme/abc-uuid-123", "ashby"),
    ("https://jobs.smartrecruiters.com/Acme/123-engineer", "smartrecruiters"),
    ("https://apply.workable.com/acme/j/ABC123/", "workable"),
    ("https://acme.taleo.net/careersection/jobdetail.ftl?job=12345", "taleo"),
    ("https://acme.icims.com/jobs/12345/engineer/job", "icims"),
    ("https://acme.jobs.personio.com/job/123456", "personio"),
    ("https://www.linkedin.com/jobs/view/12345?easy_apply=true", "linkedin_easy_apply"),
])
def test_known_platforms(url, expected):
    assert classify_apply_platform(url) == expected


def test_unknown_url_returns_none():
    assert classify_apply_platform("https://jobs.ie/job/12345") is None
    assert classify_apply_platform("https://www.indeed.com/viewjob?jk=abc") is None
    assert classify_apply_platform("https://acme.com/careers/123") is None


def test_empty_or_none_returns_none():
    assert classify_apply_platform("") is None
    assert classify_apply_platform(None) is None


def test_malformed_url_returns_none():
    """Classifier must never raise on garbage input."""
    assert classify_apply_platform("not a url") is None
    assert classify_apply_platform(12345) is None  # type: ignore
