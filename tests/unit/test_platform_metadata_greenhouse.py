import pytest
import httpx
from unittest.mock import patch, MagicMock
from shared.platform_metadata.greenhouse import fetch_greenhouse, GreenhouseFetchError


_FAKE_RESPONSE = {
    "id": 7649441,
    "title": "Senior Engineer",
    "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/7649441",
    "questions": [
        {"label": "First Name", "required": True, "description": None,
         "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Resume/CV", "required": True, "description": None,
         "fields": [{"name": "resume", "type": "input_file", "values": []}]},
        {"label": "Cover Letter", "required": False, "description": None,
         "fields": [{"name": "cover_letter", "type": "input_file", "values": []}]},
        {"label": "Why Airbnb?", "required": True, "description": None,
         "fields": [{"name": "question_1", "type": "textarea", "values": []}]},
        {"label": "Gender", "required": True, "description": "EEO disclosure...",
         "fields": [{"name": "question_2", "type": "multi_value_single_select",
                     "values": [
                         {"label": "Male", "value": 1},
                         {"label": "Decline to Self Identify", "value": 2},
                     ]}]},
    ],
}


def _mock_response(status=200, json_data=None):
    m = MagicMock(spec=httpx.Response)
    m.status_code = status
    m.json.return_value = json_data if json_data is not None else _FAKE_RESPONSE
    if status >= 400:
        m.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=m
        )
    else:
        m.raise_for_status.return_value = None
    return m


class TestFetchGreenhouse:
    def test_returns_normalized_questions(self):
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        assert result["platform"] == "greenhouse"
        assert result["job_title"] == "Senior Engineer"
        assert len(result["questions"]) == 5
        assert result["cover_letter_field_present"] is True
        assert result["cover_letter_required"] is False
        assert result["cover_letter_max_length"] == 10000

    def test_question_field_normalization(self):
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        gender_q = next(q for q in result["questions"] if q["label"] == "Gender")
        # Type normalized from Greenhouse's "multi_value_single_select" to spec's "select"
        assert gender_q["type"] == "select"
        assert gender_q["required"] is True
        assert gender_q["description"] == "EEO disclosure..."
        assert gender_q["options"] == ["Male", "Decline to Self Identify"]
        assert gender_q["field_name"] == "question_2"

    def test_yes_no_questions_normalized(self):
        # 2-option multi_value_single_select with "Yes"/"No" shape -> spec type "yes_no"
        yn_response = {**_FAKE_RESPONSE, "questions": [
            {"label": "Are you authorized?", "required": True, "description": None,
             "fields": [{"name": "question_yn", "type": "multi_value_single_select",
                         "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 2}]}]},
        ]}
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=yn_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        assert result["questions"][0]["type"] == "yes_no"

    def test_compliance_array_merged_with_eeo_category(self):
        compliance_response = {**_FAKE_RESPONSE, "compliance": [{
            "type": "race_ethnicity",
            "description": None,
            "questions": [{
                "label": "Hispanic or Latino?", "required": False, "description": None,
                "fields": [{"name": "compliance_race_1", "type": "multi_value_single_select",
                            "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 2},
                                       {"label": "Decline", "value": 3}]}]
            }],
        }]}
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=compliance_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        compliance_q = next(q for q in result["questions"] if "Hispanic" in q["label"])
        assert compliance_q["category"] == "eeo"
        assert compliance_q["field_name"] == "compliance_race_1"

    def test_demographic_questions_merged_with_eeo_category(self):
        demo_response = {**_FAKE_RESPONSE, "demographic_questions": {
            "description": None,
            "questions": [{
                "label": "Pronouns", "required": False, "description": "Voluntary",
                "fields": [{"name": "demo_pronouns", "type": "input_text", "values": []}],
            }],
        }}
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=demo_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        demo_q = next(q for q in result["questions"] if q["field_name"] == "demo_pronouns")
        assert demo_q["category"] == "eeo"
        assert demo_q["type"] == "text"  # input_text → text

    def test_compliance_block_description_propagates_to_questions(self):
        # Real Greenhouse compliance blocks put the EEO disclosure at the BLOCK
        # level. Each question's own description=null. The fetcher must propagate
        # the block description down so the AI prompt sees the voluntary-disclosure
        # context. (Verified shape against Discord posting 7343909.)
        compliance_response = {**_FAKE_RESPONSE, "compliance": [{
            "type": "eeoc",
            "description": "<p>Voluntary Self-Identification of Disability — Form CC-305</p>",
            "questions": [{
                "label": "DisabilityStatus", "required": False, "description": None,
                "fields": [{"name": "disability_status", "type": "multi_value_single_select",
                            "values": [{"label": "I do not want to answer", "value": "3"}]}]
            }],
        }]}
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(json_data=compliance_response)
            MockClient.return_value.__enter__.return_value = client

            result = fetch_greenhouse("airbnb", "7649441")

        disability_q = next(q for q in result["questions"] if q["field_name"] == "disability_status")
        # Question's own description was null; block description propagated down
        assert "Voluntary Self-Identification" in (disability_q["description"] or "")

    def test_constructs_correct_url(self):
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            fetch_greenhouse("airbnb", "7649441")

            client.get.assert_called_once_with(
                "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs/7649441",
                params={"questions": "true"},
            )

    def test_404_raises_job_not_available(self):
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response(status=404)
            MockClient.return_value.__enter__.return_value = client

            with pytest.raises(GreenhouseFetchError) as exc:
                fetch_greenhouse("airbnb", "7649441")

            assert exc.value.reason == "job_no_longer_available"

    def test_does_not_follow_redirects(self):
        with patch("shared.platform_metadata.greenhouse.httpx.Client") as MockClient:
            client = MagicMock()
            client.get.return_value = _mock_response()
            MockClient.return_value.__enter__.return_value = client

            fetch_greenhouse("airbnb", "7649441")

            MockClient.assert_called_once()
            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs.get("follow_redirects") is False
            assert call_kwargs.get("timeout") is not None
