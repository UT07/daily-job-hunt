import logging
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


# Process-level caches — same rationale and shape as ai_helper.get_param /
# ai_helper.get_supabase: both used to redo their full work on EVERY call, a
# live SSM GetParameter round trip with KMS decryption, plus a fresh
# create_client() on top of two of those. Memoized in place rather than by
# importing ai_helper's copies: that would resolve here (same CodeUri) but is a
# wider refactor than this change, and the scrapers cannot do it at all.
# reset_caches() below is the escape hatch for a rotated parameter or a
# per-call test.
_param_cache: dict[str, str] = {}
_supabase = None


def get_param(name):
    # `not in` rather than a falsy check: a parameter that is legitimately the
    # empty string must stay cached, not be re-fetched on every call forever.
    if name not in _param_cache:
        _param_cache[name] = _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return _param_cache[name]


def get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client
        _supabase = create_client(
            get_param("/naukribaba/SUPABASE_URL"),
            get_param("/naukribaba/SUPABASE_SERVICE_KEY"),
        )
    return _supabase


def reset_caches():
    """Drop the memoized SSM parameters and Supabase client.

    Mirrors ai_helper.reset_caches(). Leaves the boto3 client alone — that one
    is a connection holder, not a cached value.
    """
    global _supabase
    _param_cache.clear()
    _supabase = None


def handler(event, context):
    user_id = event.get("user_id", "")
    error_msg = event.get("error", "Unknown error")
    step = event.get("step", "unknown")

    logger.error(f"[notify_error] Step={step}, Error={error_msg}")

    # Send error email to admin
    try:
        gmail_user = get_param("/naukribaba/GMAIL_USER")
        gmail_pass = get_param("/naukribaba/GMAIL_APP_PASSWORD")

        msg = MIMEText(f"Pipeline error in step '{step}':\n\n{error_msg}\n\nUser: {user_id}")
        msg["Subject"] = f"NaukriBaba Pipeline Error: {step}"
        msg["From"] = gmail_user
        msg["To"] = gmail_user  # Send to admin

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)

        return {"notified": True}
    except Exception as e:
        logger.error(f"[notify_error] Failed to send: {e}")
        return {"notified": False, "error": str(e)}
