import html
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
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
    user_id = event["user_id"]
    matched_count = event.get("matched_count", 0)

    if matched_count == 0:
        return {"sent": False, "reason": "no_matches"}

    db = get_supabase()

    # Get user email
    user = db.table("users").select("email, name").eq("id", user_id).execute()
    if not user.data:
        return {"sent": False, "reason": "no_user"}
    user_email = user.data[0]["email"]
    user_name = user.data[0].get("name", "")

    # Get today's matched jobs
    today = datetime.utcnow().date().isoformat()
    jobs = db.table("jobs").select("*").eq("user_id", user_id) \
        .gte("first_seen", today).order("match_score", desc=True).limit(15).execute()

    if not jobs.data:
        return {"sent": False, "reason": "no_jobs_today"}

    # Format HTML email
    email_body = format_email_html(jobs.data, user_name)

    # Send via Gmail SMTP
    gmail_user = get_param("/naukribaba/GMAIL_USER")
    gmail_pass = get_param("/naukribaba/GMAIL_APP_PASSWORD")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"NaukriBaba: {len(jobs.data)} new job matches"
    msg["From"] = gmail_user
    msg["To"] = user_email
    msg.attach(MIMEText(email_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)

    logger.info(f"[send_email] Sent {len(jobs.data)} jobs to {user_email}")
    return {"sent": True, "jobs_count": len(jobs.data)}


def format_email_html(jobs, user_name):
    rows = ""
    for j in jobs:
        score = j.get("match_score", 0)
        if score >= 85:
            score_color = "#22c55e"
        elif score >= 70:
            score_color = "#f59e0b"
        else:
            score_color = "#ef4444"

        title = html.escape(j.get("title", ""))
        company = html.escape(j.get("company", ""))
        source = html.escape(j.get("source", ""))

        resume_url = j.get("resume_s3_url", "")
        resume_link = f'<a href="{html.escape(resume_url)}">Resume</a>' if resume_url else "-"

        rows += f"""<tr>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{title}</b><br><small>{company}</small></td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:center"><span style="color:{score_color};font-weight:bold">{score}</span></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{source}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{resume_link}</td>
        </tr>"""

    safe_name = html.escape(user_name)
    return f"""<html><body style="font-family:system-ui;max-width:600px;margin:0 auto">
    <h2>Hey {safe_name}, {len(jobs)} new matches today!</h2>
    <table style="width:100%;border-collapse:collapse">
        <tr style="background:#f8f9fa"><th style="padding:8px;text-align:left">Job</th><th>Score</th><th>Source</th><th>Resume</th></tr>
        {rows}
    </table>
    <p style="color:#666;font-size:12px">NaukriBaba &mdash; Your AI Job Hunt Assistant</p>
    </body></html>"""
