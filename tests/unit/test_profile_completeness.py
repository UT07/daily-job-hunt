# tests/unit/test_profile_completeness.py
"""Unit tests for shared.profile_completeness."""


def _complete():
    return {
        "first_name": "Utkarsh", "last_name": "Singh",
        "email": "u@example.com", "phone": "+353851234567",
        "linkedin": "https://linkedin.com/in/u",
        "visa_status": "stamp-1g",
        "work_authorizations": {"IE": "stamp1g"},
        "notice_period_text": "2 weeks",
    }


def test_complete_profile_returns_empty_list():
    from shared.profile_completeness import check_profile_completeness
    assert check_profile_completeness(_complete()) == []


def test_missing_single_field_reported():
    from shared.profile_completeness import check_profile_completeness
    p = _complete(); del p["phone"]
    assert check_profile_completeness(p) == ["phone"]


def test_multiple_missing_preserves_order():
    from shared.profile_completeness import check_profile_completeness
    assert check_profile_completeness({}) == [
        "first_name", "last_name", "email", "phone", "linkedin",
        "visa_status", "work_authorizations",
        "notice_period_text",
    ]


def test_default_referral_source_no_longer_required():
    """Regression: default_referral_source had no UI to set it, so requiring
    it made profile_complete structurally impossible for every user. See
    shared/profile_completeness.py header comment for the follow-up plan."""
    from shared.profile_completeness import REQUIRED_FIELDS
    assert "default_referral_source" not in REQUIRED_FIELDS


def test_empty_work_authorizations_dict_treated_as_missing():
    from shared.profile_completeness import check_profile_completeness
    p = _complete(); p["work_authorizations"] = {}
    assert "work_authorizations" in check_profile_completeness(p)


def test_whitespace_only_string_treated_as_missing():
    from shared.profile_completeness import check_profile_completeness
    p = _complete(); p["phone"] = "   "
    assert "phone" in check_profile_completeness(p)


def test_none_profile_returns_all_required():
    from shared.profile_completeness import check_profile_completeness, REQUIRED_FIELDS
    assert len(check_profile_completeness(None)) == len(REQUIRED_FIELDS)
