"""notify_error must distinguish a finding from a failure.

On 2026-09-28 a healthy run emailed "NaukriBaba Pipeline Error:
self_improve_medium_risk" for adjustments that had applied successfully, plus a
scraper_health_check warning — and two concurrent runs doubled them. Routing
findings through the failure channel trains you to ignore the channel that is
supposed to page you.
"""
from unittest.mock import patch

import pytest

import notify_error


@pytest.mark.parametrize("step", [
    "self_improve_medium_risk", "self_improve_high_risk",
    "scraper_health_check", "provider_health_check",
])
def test_findings_are_not_emailed(step, monkeypatch):
    monkeypatch.delenv("NOTIFY_INFO_STEPS", raising=False)
    with patch("notify_error.get_param") as gp, patch("smtplib.SMTP_SSL") as smtp:
        result = notify_error.handler({"step": step, "error": "9 adjustments applied"}, None)
    assert result["notified"] is False
    assert result["reason"] == "informational"
    smtp.assert_not_called()
    gp.assert_not_called(), "must not even fetch credentials for a finding"


@pytest.mark.parametrize("step", [
    "tailor_resume", "score_batch", "merge_dedup", "compile_latex", "unknown",
])
def test_real_failures_still_page(step):
    """The inversion must not silence genuine step failures."""
    with patch("notify_error.get_param", return_value="x"), \
         patch("smtplib.SMTP_SSL") as smtp:
        result = notify_error.handler({"step": step, "error": "boom"}, None)
    assert result["notified"] is True
    smtp.assert_called_once()


def test_env_override_restores_emailing_findings(monkeypatch):
    monkeypatch.setenv("NOTIFY_INFO_STEPS", "email")
    with patch("notify_error.get_param", return_value="x"), \
         patch("smtplib.SMTP_SSL") as smtp:
        result = notify_error.handler(
            {"step": "self_improve_medium_risk", "error": "9 applied"}, None)
    assert result["notified"] is True
    smtp.assert_called_once()


@pytest.mark.parametrize("step,expected", [
    ("self_improve_medium_risk", True),
    ("scraper_health_check", True),
    ("tailor_resume", False),
    ("score_batch", False),
    ("", False),
])
def test_classification(step, expected):
    assert notify_error._is_informational(step) is expected
