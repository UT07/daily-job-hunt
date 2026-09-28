import logging
import os
import smtplib
from email.mime.text import MIMEText

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lazy SSM client — boto3.client at module load forces AWS_DEFAULT_REGION
# on every importer (including unit tests + runtime-import smoke). Same
# pattern as ai_helper.py shipped in PR #23.
_ssm = None


def _get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def get_param(name):
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


# Steps that report a FINDING, not a failure. The state machine routes these
# through the same notify path as real errors, so on 2026-09-28 a normal run
# emailed "NaukriBaba Pipeline Error: self_improve_medium_risk" for adjustments
# that had been applied successfully, plus a scraper_health_check warning --
# and two concurrent runs doubled them. The content is useful; an ERROR email
# per finding per run is not, and it trains you to ignore the channel that is
# supposed to page you.
#
# These are already visible in the step's own output (SelfImprove returns
# unhealthy_scrapers and new_adjustments), in CloudWatch, and in the daily
# summary send_email.py already delivers -- so suppressing the extra email
# loses no information. Set NOTIFY_INFO_STEPS=email to restore the old
# behaviour.
INFORMATIONAL_STEP_PREFIXES = ("self_improve",)
INFORMATIONAL_STEP_SUFFIXES = ("_health_check",)


def _is_informational(step: str) -> bool:
    return (step.startswith(INFORMATIONAL_STEP_PREFIXES)
            or step.endswith(INFORMATIONAL_STEP_SUFFIXES))


def handler(event, context):
    user_id = event.get("user_id", "")
    error_msg = event.get("error", "Unknown error")
    step = event.get("step", "unknown")

    informational = _is_informational(step)
    if informational:
        logger.info(f"[notify_error] FINDING Step={step}, Detail={error_msg}")
        if os.environ.get("NOTIFY_INFO_STEPS", "log").lower() != "email":
            return {"notified": False, "reason": "informational", "step": step}
    else:
        logger.error(f"[notify_error] Step={step}, Error={error_msg}")

    # Send email to admin
    try:
        gmail_user = get_param("/naukribaba/GMAIL_USER")
        gmail_pass = get_param("/naukribaba/GMAIL_APP_PASSWORD")

        kind = "finding" if informational else "error"
        msg = MIMEText(f"Pipeline {kind} in step '{step}':\n\n{error_msg}\n\nUser: {user_id}")
        msg["Subject"] = (f"NaukriBaba Pipeline {'Finding' if informational else 'Error'}: {step}")
        msg["From"] = gmail_user
        msg["To"] = gmail_user  # Send to admin

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)

        return {"notified": True}
    except Exception as e:
        logger.error(f"[notify_error] Failed to send: {e}")
        return {"notified": False, "error": str(e)}
