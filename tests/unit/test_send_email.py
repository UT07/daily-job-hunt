"""Unit tests for send_email Lambda."""
from unittest.mock import patch, MagicMock


def _make_supabase(user_data=None, jobs_data=None):
    """Build a mock Supabase client for send_email tests."""
    mock_client = MagicMock()

    user_result = MagicMock()
    user_result.data = user_data if user_data is not None else []

    jobs_result = MagicMock()
    jobs_result.data = jobs_data if jobs_data is not None else []

    user_chain = MagicMock()
    user_chain.select.return_value = user_chain
    user_chain.eq.return_value = user_chain
    user_chain.execute.return_value = user_result

    jobs_chain = MagicMock()
    jobs_chain.select.return_value = jobs_chain
    jobs_chain.eq.return_value = jobs_chain
    jobs_chain.gte.return_value = jobs_chain
    jobs_chain.order.return_value = jobs_chain
    jobs_chain.limit.return_value = jobs_chain
    jobs_chain.execute.return_value = jobs_result

    def table_side_effect(name):
        if name == "users":
            return user_chain
        elif name == "jobs":
            return jobs_chain
        return MagicMock()

    mock_client.table.side_effect = table_side_effect
    return mock_client


SAMPLE_JOB = {
    "title": "Python Engineer",
    "company": "Acme",
    "match_score": 88,
    "source": "linkedin",
    "resume_s3_url": "https://s3.example.com/resume.pdf",
}


def test_zero_matches_does_not_send():
    """When matched_count is 0, email is not sent."""
    with patch("send_email.get_supabase") as mock_get_db, \
         patch("send_email.smtplib") as mock_smtp:
        import send_email
        result = send_email.handler(
            {"user_id": "user-1", "matched_count": 0},
            None,
        )

    assert result == {"sent": False, "reason": "no_matches"}
    mock_get_db.assert_not_called()
    mock_smtp.SMTP_SSL.assert_not_called()


def test_html_escaping_script_tag_in_title():
    """A job title containing <script> is HTML-escaped in the email body."""
    malicious_job = {
        "title": "<script>alert('xss')</script>",
        "company": "Evil Corp",
        "match_score": 72,
        "source": "hn",
        "resume_s3_url": "",
    }
    db = _make_supabase(
        user_data=[{"email": "user@example.com", "full_name": "Test User"}],
        jobs_data=[malicious_job],
    )

    mock_smtp_instance = MagicMock()
    mock_smtp_ctx = MagicMock()
    mock_smtp_ctx.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_ctx.__exit__ = MagicMock(return_value=False)

    with patch("send_email.get_supabase", return_value=db), \
         patch("send_email.get_param", return_value="test-value"), \
         patch("send_email.smtplib") as mock_smtplib:
        mock_smtplib.SMTP_SSL.return_value = mock_smtp_ctx

        import send_email
        result = send_email.handler(
            {"user_id": "user-1", "matched_count": 1},
            None,
        )

    assert result["sent"] is True

    # Verify the sent message does NOT contain a raw <script> tag
    sent_message = mock_smtp_instance.send_message.call_args[0][0]
    email_body = sent_message.get_payload(0).get_payload()

    assert "<script>" not in email_body
    assert "&lt;script&gt;" in email_body
