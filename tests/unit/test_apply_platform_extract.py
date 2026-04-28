import pytest
from shared.apply_platform import extract_platform_ids


class TestExtractPlatformIds:
    def test_greenhouse_standard_url(self):
        url = "https://boards.greenhouse.io/airbnb/jobs/7649441"
        assert extract_platform_ids(url) == {
            "platform": "greenhouse",
            "board_token": "airbnb",
            "posting_id": "7649441",
        }

    def test_greenhouse_with_query_string(self):
        url = "https://boards.greenhouse.io/airbnb/jobs/7649441?gh_src=abc"
        assert extract_platform_ids(url) == {
            "platform": "greenhouse",
            "board_token": "airbnb",
            "posting_id": "7649441",
        }

    def test_greenhouse_embed_url(self):
        url = "https://boards.greenhouse.io/embed/job_app?for=airbnb&token=7649441"
        result = extract_platform_ids(url)
        assert result is not None
        assert result["platform"] == "greenhouse"
        assert result["board_token"] == "airbnb"
        assert result["posting_id"] == "7649441"

    def test_ashby_standard_url(self):
        url = "https://jobs.ashbyhq.com/openai/145ff46b-1441-4773-bcd3-c8c90baa598a"
        assert extract_platform_ids(url) == {
            "platform": "ashby",
            "board_token": "openai",
            "posting_id": "145ff46b-1441-4773-bcd3-c8c90baa598a",
        }

    def test_ashby_with_application_suffix(self):
        url = "https://jobs.ashbyhq.com/openai/145ff46b-1441-4773-bcd3-c8c90baa598a/application"
        result = extract_platform_ids(url)
        assert result is not None
        assert result["board_token"] == "openai"
        assert result["posting_id"] == "145ff46b-1441-4773-bcd3-c8c90baa598a"

    def test_unsupported_platform_returns_none(self):
        assert extract_platform_ids("https://jobs.lever.co/foo/bar") is None
        assert extract_platform_ids("https://linkedin.com/jobs/view/12345") is None

    def test_none_input_returns_none(self):
        assert extract_platform_ids(None) is None
        assert extract_platform_ids("") is None
        assert extract_platform_ids(123) is None  # type: ignore[arg-type]

    def test_greenhouse_embed_url_reversed_query_order(self):
        # Spec requires both query orders to work
        url = "https://boards.greenhouse.io/embed/job_app?token=7649441&for=airbnb"
        result = extract_platform_ids(url)
        assert result is not None
        assert result["platform"] == "greenhouse"
        assert result["board_token"] == "airbnb"
        assert result["posting_id"] == "7649441"

    def test_malformed_greenhouse_url_returns_none(self):
        assert extract_platform_ids("https://boards.greenhouse.io/airbnb") is None
        assert extract_platform_ids("https://boards.greenhouse.io/") is None
