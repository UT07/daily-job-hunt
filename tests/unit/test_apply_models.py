import pytest
from pydantic import ValidationError
from shared.apply_models import (
    PlatformInfo, CustomQuestion, CustomAnswer, ApplyPreviewResponse,
)


class TestPlatformInfo:
    def test_valid_greenhouse(self):
        p = PlatformInfo(platform="greenhouse", board_token="airbnb", posting_id="7649441")
        assert p.platform == "greenhouse"

    def test_invalid_platform_rejected(self):
        with pytest.raises(ValidationError):
            PlatformInfo(platform="lever", board_token="x", posting_id="y")


class TestCustomQuestion:
    def test_valid_select(self):
        q = CustomQuestion(id="question_1", label="Gender", type="select", required=True,
                           options=["Male", "Female"], category="eeo")
        assert q.options == ["Male", "Female"]

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            CustomQuestion(id="q1", label="x", type="multi_value_single_select",
                           required=True)

    def test_ai_answer_can_be_bool(self):
        q = CustomQuestion(id="q1", label="Confirm", type="checkbox", required=True,
                           ai_answer=False, requires_user_action=True, category="confirmation")
        assert q.ai_answer is False


class TestApplyPreviewResponse:
    def test_eligible_payload_validates(self):
        payload = {
            "eligible": True,
            "profile_complete": True,
            "missing_required_fields": [],
            "job": {"title": "X", "company": "Y", "location": "Z", "apply_url": "https://..."},
            "platform": "greenhouse",
            "platform_metadata": {"board_token": "airbnb", "posting_id": "7649441"},
            "resume": {"s3_url": "https://...", "filename": "r.pdf",
                       "resume_version": 1, "s3_key": "users/u/resume.pdf",
                       "is_default": False},
            "profile": {"first_name": "Jane", "last_name": "Doe", "email": "j@x.com"},
            "cover_letter": {"text": "...", "editable": True, "max_length": 10000,
                             "source": "tailored", "include_by_default": True},
            "custom_questions": [],
            "already_applied": False,
            "cache_hit": False,
        }
        r = ApplyPreviewResponse(**payload)
        assert r.eligible is True

    def test_ineligible_minimal_payload(self):
        payload = {
            "eligible": False, "reason": "no_resume",
            "profile_complete": True, "missing_required_fields": [],
            "job": {}, "platform": "greenhouse", "platform_metadata": {},
            "resume": {}, "profile": {}, "cover_letter": {},
            "custom_questions": [], "already_applied": False, "cache_hit": False,
        }
        r = ApplyPreviewResponse(**payload)
        assert r.reason == "no_resume"
