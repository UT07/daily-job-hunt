import pytest
from shared.question_classifier import classify_question


class TestClassifyQuestion:
    @pytest.mark.parametrize("label,description,expected", [
        ("Gender", None, "eeo"),
        ("Ethnicity", None, "eeo"),
        ("Veteran Status", None, "eeo"),
        ("Disability self-identification", None, "eeo"),
        ("Race / Ethnicity (US)", None, "eeo"),
        ("Please self-identify your gender", None, "eeo"),
        ("I confirm the information above is accurate", None, "confirmation"),
        ("I certify that I have read and understand the company's policies", None, "confirmation"),
        ("Do you acknowledge our terms?", None, "confirmation"),
        ("Subscribe to our marketing newsletter?", None, "marketing"),
        ("Receive promotional updates about new openings?", None, "marketing"),
        ("How did you hear about this position?", None, "referral"),
        ("Referral source", None, "referral"),
        ("Why are you interested in working at Airbnb?", None, "custom"),
        ("Tell us about a project you led", None, "custom"),
        ("Are you legally authorized to work in the US?", None, "custom"),
    ])
    def test_classification(self, label, description, expected):
        assert classify_question(label, description) == expected

    def test_eeo_via_description(self):
        # Some platforms put "self-identify" in description, not label
        assert classify_question("Please answer voluntarily",
                                 "This question helps us measure diversity and is voluntary self-identification.") == "eeo"

    def test_empty_label_returns_custom(self):
        assert classify_question("", None) == "custom"

    def test_case_insensitive(self):
        assert classify_question("GENDER", None) == "eeo"
        assert classify_question("How Did You Hear About Us?", None) == "referral"
