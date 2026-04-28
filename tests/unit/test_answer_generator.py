from unittest.mock import MagicMock
from shared.answer_generator import generate_answer, DEFAULT_CANDIDATE_CONTEXT


_PROFILE = {
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phone": "+1-555-0100",
    "linkedin": "https://linkedin.com/in/janedoe",
    "github": "https://github.com/janedoe",
    "website": "https://janedoe.dev",
    "location": "Dublin, Ireland",
    "visa_status": "stamp1g",
    "work_authorizations": {"IE": "stamp1g", "US": "requires_sponsorship"},
    "candidate_context": "8yr full-stack engineer. Python, AWS, React.",
    "salary_expectation_notes": "€80-100k OTE",
    "notice_period_text": "2 weeks",
    "default_referral_source": "LinkedIn",
}
_JOB = {
    "title": "Senior Backend Engineer",
    "company": "Airbnb",
    "location": "Paris, France",
    "description": "Build the backend for travel experiences. Python, distributed systems...",
    "key_matches": ["Python", "FastAPI", "AWS"],
}
_RESUME_TEXT = "Senior Software Engineer..."
_COVER_LETTER = "I am excited..."


class TestGenerateAnswer:
    def test_standard_field_first_name(self):
        q = {"label": "First Name", "field_name": "first_name", "type": "text",
             "required": True, "options": [], "description": None}
        fake_ai = MagicMock()
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)
        assert result["answer"] == "Jane"
        assert result["category"] == "standard"
        fake_ai.assert_not_called()

    def test_standard_field_email(self):
        q = {"label": "Email", "field_name": "email", "type": "text",
             "required": True, "options": [], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "jane@example.com"

    def test_resume_file_field_returns_marker(self):
        q = {"label": "Resume/CV", "field_name": "resume", "type": "file",
             "required": True, "options": [], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "<resume_pdf>"
        assert result["category"] == "file"

    def test_eeo_select_picks_decline_option(self):
        q = {"label": "Gender", "field_name": "question_1", "type": "select",
             "required": True,
             "options": ["Male", "Female", "Non-binary", "Decline to Self Identify"],
             "description": "Voluntary self-identification..."}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "Decline to Self Identify"
        assert result["category"] == "eeo"

    def test_eeo_pre_tagged_category_honored(self):
        # When fetcher pre-tags category="eeo" (compliance/demographic), respect it
        q = {"label": "Pronouns", "field_name": "demo_pronouns", "type": "text",
             "required": False, "options": [], "description": None, "category": "eeo"}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["category"] == "eeo"

    def test_confirmation_requires_user_action(self):
        q = {"label": "I confirm the information above is accurate",
             "field_name": "question_3", "type": "checkbox",
             "required": True, "options": [], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["category"] == "confirmation"
        assert result["requires_user_action"] is True
        assert result["answer"] is False

    def test_marketing_returns_false(self):
        q = {"label": "Subscribe to our marketing newsletter?",
             "field_name": "question_4", "type": "yes_no",
             "required": False, "options": ["Yes", "No"], "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] in ("No", False)
        assert result["category"] == "marketing"

    def test_referral_fuzzy_matches_user_default(self):
        q = {"label": "How did you hear about this position?",
             "field_name": "question_5", "type": "select",
             "required": True,
             "options": ["LinkedIn", "Company website", "Friend referral", "Job board"],
             "description": None}
        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, MagicMock())
        assert result["answer"] == "LinkedIn"
        assert result["category"] == "referral"

    def test_custom_question_calls_ai_with_rich_prompt(self):
        q = {"label": "Why are you interested in working at Airbnb?",
             "field_name": "question_6", "type": "textarea",
             "required": True, "options": [], "description": None}
        fake_ai = MagicMock(return_value={"content": "I'm passionate about travel.",
                                            "provider": "qwen", "model": "qwen2-72b"})

        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)

        assert result["category"] == "custom"
        assert result["answer"] == "I'm passionate about travel."
        fake_ai.assert_called_once()
        kwargs = fake_ai.call_args.kwargs
        # Spec §7.3 step 9 hard requirements:
        assert kwargs["temperature"] == 0.3
        assert kwargs["max_tokens"] == 300
        assert kwargs["cache_hours"] == 24 * 7

        prompt = kwargs["prompt"]
        # Verify the prompt contains all spec-required fields:
        assert "Jane Doe" in prompt
        assert "Airbnb" in prompt
        assert "Senior Backend Engineer" in prompt
        assert "8yr full-stack" in prompt  # candidate_context
        assert "stamp1g" in prompt or "IE" in prompt  # work_authorizations
        assert "€80-100k" in prompt  # salary_expectation_notes
        assert "2 weeks" in prompt  # notice_period_text
        assert "Python" in prompt  # key_matches

    def test_custom_falls_back_to_default_candidate_context_when_empty(self):
        profile_no_context = {**_PROFILE, "candidate_context": ""}
        q = {"label": "Tell us about yourself", "field_name": "question_7",
             "type": "textarea", "required": True, "options": [], "description": None}
        fake_ai = MagicMock(return_value={"content": "ok", "provider": "p", "model": "m"})

        generate_answer(q, profile_no_context, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)

        prompt = fake_ai.call_args.kwargs["prompt"]
        assert "MSc in Cloud Computing" in prompt  # from DEFAULT_CANDIDATE_CONTEXT
        assert "AWS Solutions Architect" in prompt

    def test_custom_select_fuzzy_matches_ai_response(self):
        q = {"label": "Years of experience?", "field_name": "question_8",
             "type": "select", "required": True,
             "options": ["0-2 years", "3-5 years", "6-10 years", "10+ years"],
             "description": None}
        fake_ai = MagicMock(return_value={"content": "8 years", "provider": "p", "model": "m"})

        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)

        # AI returned "8 years" — must be fuzzy-matched to "6-10 years"
        assert result["answer"] == "6-10 years"

    def test_yes_no_unparseable_ai_response_defaults_to_yes(self):
        # Spec §7.3 step 9: yes_no with non-yes/no AI response defaults to "Yes"
        q = {"label": "Are you authorized to work in Ireland?", "field_name": "question_9",
             "type": "yes_no", "required": True, "options": ["Yes", "No"], "description": None}
        fake_ai = MagicMock(return_value={"content": "I have a Stamp 1G visa which permits...",
                                            "provider": "p", "model": "m"})

        result = generate_answer(q, _PROFILE, _JOB, _RESUME_TEXT, _COVER_LETTER, fake_ai)
        assert result["answer"] == "Yes"

    def test_default_candidate_context_constant_present(self):
        # Sanity: spec defines this verbatim; must be importable
        assert "MSc in Cloud Computing" in DEFAULT_CANDIDATE_CONTEXT
        assert "AWS Solutions Architect Professional" in DEFAULT_CANDIDATE_CONTEXT
