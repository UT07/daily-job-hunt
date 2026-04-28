import pytest
import httpx
from unittest.mock import patch, MagicMock
from shared.platform_metadata.ashby import fetch_ashby, AshbyFetchError


# ---------------------------------------------------------------------------
# Fake response fixture — mirrors a real Ashby GraphQL response shape
# (captured 2026-04-28, see docs/superpowers/research/2026-04-28-ashby-graphql-shape.md)
# ---------------------------------------------------------------------------

_FAKE_GQL_RESPONSE = {
    "data": {
        "jobPosting": {
            "id": "145ff46b-1441-4773-bcd3-c8c90baa598a",
            "title": "Senior Engineer",
            "applicationForm": {
                "sections": [
                    {
                        "title": "Basic Info",
                        "descriptionHtml": None,
                        "fieldEntries": [
                            {
                                "id": "abc__systemfield_name",
                                "isRequired": True,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "_systemfield_name",
                                    "title": "Full Name",
                                    "type": "String",
                                    "__autoSerializationID": "StringField",
                                },
                            },
                            {
                                "id": "abc__systemfield_email",
                                "isRequired": True,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "_systemfield_email",
                                    "title": "Email",
                                    "type": "Email",
                                    "__autoSerializationID": "EmailField",
                                },
                            },
                            {
                                "id": "abc__systemfield_resume",
                                "isRequired": True,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "_systemfield_resume",
                                    "title": "Resume",
                                    "type": "File",
                                    "__autoSerializationID": "FileField",
                                },
                            },
                            {
                                "id": "abc_cover_letter_entry",
                                "isRequired": False,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "custom_cover_letter",
                                    "title": "Cover Letter",
                                    "type": "LongText",
                                    "__autoSerializationID": "LongTextField",
                                },
                            },
                        ],
                    },
                    {
                        "title": "Screening",
                        "descriptionHtml": None,
                        "fieldEntries": [
                            {
                                "id": "abc_q_auth",
                                "isRequired": True,
                                "descriptionHtml": "Please answer honestly.",
                                "field": {
                                    "path": "q_authorized",
                                    "title": "Are you authorized to work in Ireland?",
                                    "type": "Boolean",
                                    "__autoSerializationID": "BooleanField",
                                },
                            },
                            {
                                "id": "abc_q_pronouns",
                                "isRequired": False,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "q_pronouns",
                                    "title": "What pronouns do you use?",
                                    "type": "ValueSelect",
                                    "selectableValues": [
                                        {"label": "He/Him", "value": "He/Him"},
                                        {"label": "She/Her", "value": "She/Her"},
                                        {"label": "They/Them", "value": "They/Them"},
                                    ],
                                    "__autoSerializationID": "ValueSelectField",
                                },
                            },
                            {
                                "id": "abc_q_source",
                                "isRequired": False,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "q_source",
                                    "title": "How did you hear about us? (select all that apply)",
                                    "type": "MultiValueSelect",
                                    "selectableValues": [
                                        {"label": "LinkedIn", "value": "LinkedIn"},
                                        {"label": "Glassdoor", "value": "Glassdoor"},
                                        {"label": "Referral", "value": "Referral"},
                                    ],
                                    "__autoSerializationID": "MultiValueSelectField",
                                },
                            },
                        ],
                    },
                ]
            },
        }
    }
}

_NULL_POSTING_RESPONSE = {"data": {"jobPosting": None}}


def _mock_response(status=200, json_data=None):
    m = MagicMock(spec=httpx.Response)
    m.status_code = status
    m.json.return_value = json_data if json_data is not None else _FAKE_GQL_RESPONSE
    if status >= 400:
        m.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=m
        )
    else:
        m.raise_for_status.return_value = None
    return m


class TestFetchAshby:
    def test_returns_normalized_questions(self):
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_ashby("ashby", "145ff46b-1441-4773-bcd3-c8c90baa598a")

        assert result["platform"] == "ashby"
        assert result["job_title"] == "Senior Engineer"
        # 7 fieldEntries across 2 sections
        assert len(result["questions"]) == 7
        assert result["cover_letter_field_present"] is True
        assert result["cover_letter_required"] is False
        assert result["cover_letter_max_length"] == 5000

    def test_question_field_normalization(self):
        """Ashby types → spec vocabulary mapping."""
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_ashby("ashby", "145ff46b-1441-4773-bcd3-c8c90baa598a")

        qs = {q["field_name"]: q for q in result["questions"]}

        # String → text
        assert qs["_systemfield_name"]["type"] == "text"
        assert qs["_systemfield_name"]["required"] is True

        # Email → text
        assert qs["_systemfield_email"]["type"] == "text"

        # File → file
        assert qs["_systemfield_resume"]["type"] == "file"

        # LongText → textarea (cover letter)
        assert qs["custom_cover_letter"]["type"] == "textarea"

        # Boolean → yes_no
        assert qs["q_authorized"]["type"] == "yes_no"
        assert qs["q_authorized"]["required"] is True

        # ValueSelect → select, options populated
        assert qs["q_pronouns"]["type"] == "select"
        assert qs["q_pronouns"]["options"] == ["He/Him", "She/Her", "They/Them"]

        # MultiValueSelect → multi_select
        assert qs["q_source"]["type"] == "multi_select"
        assert qs["q_source"]["options"] == ["LinkedIn", "Glassdoor", "Referral"]

    def test_description_html_propagated(self):
        """descriptionHtml on a fieldEntry surfaces as description in normalized output."""
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_ashby("ashby", "145ff46b-1441-4773-bcd3-c8c90baa598a")

        auth_q = next(q for q in result["questions"] if q["field_name"] == "q_authorized")
        assert auth_q["description"] == "Please answer honestly."

    def test_null_posting_raises_job_not_available(self):
        """jobPosting=null in response → job_no_longer_available."""
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response(json_data=_NULL_POSTING_RESPONSE)
            MockClient.return_value.__enter__.return_value = client

            with pytest.raises(AshbyFetchError) as exc:
                fetch_ashby("ashby", "00000000-0000-0000-0000-000000000000")

            assert exc.value.reason == "job_no_longer_available"

    def test_http_error_raises_ashby_api_error(self):
        """Non-404 HTTP error → ashby_api_error."""
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response(status=500)
            MockClient.return_value.__enter__.return_value = client

            with pytest.raises(AshbyFetchError) as exc:
                fetch_ashby("ashby", "145ff46b-1441-4773-bcd3-c8c90baa598a")

            assert exc.value.reason == "ashby_api_error"

    def test_timeout_raises_ashby_timeout(self):
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.side_effect = httpx.TimeoutException("timed out")
            MockClient.return_value.__enter__.return_value = client

            with pytest.raises(AshbyFetchError) as exc:
                fetch_ashby("ashby", "145ff46b-1441-4773-bcd3-c8c90baa598a")

            assert exc.value.reason == "ashby_timeout"

    def test_does_not_follow_redirects(self):
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            fetch_ashby("ashby", "145ff46b-1441-4773-bcd3-c8c90baa598a")

            MockClient.assert_called_once()
            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs.get("follow_redirects") is False
            assert call_kwargs.get("timeout") is not None

    def test_constructs_correct_graphql_request(self):
        """POST is sent to correct URL with correct operationName and variables."""
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            fetch_ashby("myorg", "abc-123")

            call_args = client.post.call_args
            url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
            assert "jobs.ashbyhq.com/api/non-user-graphql" in url

            # payload is passed as json= kwarg
            payload = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else None)
            assert payload is not None
            assert payload["operationName"] == "ApiJobPosting"
            assert payload["variables"]["organizationHostedJobsPageName"] == "myorg"
            assert payload["variables"]["jobPostingId"] == "abc-123"

    def test_cover_letter_required_when_marked(self):
        """If cover letter field is required=True, cover_letter_required is True."""
        required_cl = {
            "data": {
                "jobPosting": {
                    "id": "test",
                    "title": "Test Job",
                    "applicationForm": {
                        "sections": [{
                            "title": None,
                            "descriptionHtml": None,
                            "fieldEntries": [{
                                "id": "abc_cl",
                                "isRequired": True,
                                "descriptionHtml": None,
                                "field": {
                                    "path": "_systemfield_cover_letter",
                                    "title": "Cover Letter",
                                    "type": "File",
                                    "__autoSerializationID": "FileField",
                                },
                            }],
                        }],
                    },
                }
            }
        }
        with patch("shared.platform_metadata.ashby.httpx.Client") as MockClient:
            client = MagicMock()
            client.post.return_value = _mock_response(json_data=required_cl)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_ashby("org", "job-id")

        assert result["cover_letter_field_present"] is True
        assert result["cover_letter_required"] is True
