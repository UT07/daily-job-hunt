"""Daily follow-up reminder: email jobs with status='Applied' and no change in 7+ days."""
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import boto3
from ai_helper import get_supabase

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


def _parse_ts(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fetch_followup_jobs(db, user_id: str, cutoff_iso: str, limit: int = 10) -> list[dict]:
    """Find jobs stuck in 'Applied' for 7+ days with no status change.

    ``jobs`` has no ``updated_at`` column (it never did — see
    supabase/migrations/00000000000000_initial_schema.sql) and its
    ``last_seen`` column tracks when a *scraper* last re-found the posting,
    which is unrelated to when the *user* applied or last changed the
    status — using it would nudge on stale postings that were applied to
    yesterday, and stay silent on genuinely stale applications that happen
    to still be listed. The actual source of truth for "when did this job's
    status last change" is ``application_timeline`` (see app.py's
    ``add_timeline_event``, which inserts a timeline row *and* syncs
    ``jobs.application_status`` on every change), so the most recent
    ``status='Applied'`` timeline row's ``created_at`` is "when they
    applied". Jobs marked Applied before that table had a row for them
    (or via some path that didn't log one) fall back to ``first_seen``,
    which is always <= the true apply time — so the fallback can only ever
    nudge a little early, never silently never.
    """
    applied_result = db.table("jobs").select("job_id, title, company, match_score, apply_url, first_seen") \
        .eq("user_id", user_id) \
        .eq("application_status", "Applied") \
        .execute()
    applied_jobs = applied_result.data or []
    if not applied_jobs:
        return []

    job_ids = [j["job_id"] for j in applied_jobs]
    timeline_result = db.table("application_timeline").select("job_id, created_at") \
        .eq("user_id", user_id) \
        .eq("status", "Applied") \
        .in_("job_id", job_ids) \
        .order("created_at", desc=True) \
        .execute()

    applied_at_by_job: dict[str, str] = {}
    for row in (timeline_result.data or []):
        # Sorted desc, so the first row seen per job_id is the most recent.
        applied_at_by_job.setdefault(row["job_id"], row["created_at"])

    followup = []
    for job in applied_jobs:
        applied_at = applied_at_by_job.get(job["job_id"]) or job.get("first_seen")
        if not applied_at or applied_at >= cutoff_iso:
            continue
        job = dict(job)
        job["_applied_at"] = applied_at
        followup.append(job)

    followup.sort(key=lambda j: j["_applied_at"])
    return followup[:limit]


def handler(event, context):
    user_id = event.get("user_id", "default")
    db = get_supabase()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    followup_jobs = _fetch_followup_jobs(db, user_id, cutoff)
    if not followup_jobs:
        logger.info("[followup] No follow-up reminders needed")
        return {"sent": False, "count": 0}

    rows = ""
    for j in followup_jobs:
        days = (datetime.now(timezone.utc) - _parse_ts(j["_applied_at"])).days
        rows += f"<tr><td>{j['title']}</td><td>{j['company']}</td><td>{days}d since applied</td></tr>\n"

    html = f"""<html><body>
<h2>📬 Follow-up reminder: {len(followup_jobs)} application{'' if len(followup_jobs) == 1 else 's'}</h2>
<p>You applied to these jobs 7+ days ago with no status update. Consider following up!</p>
<table border="1" cellpadding="6" style="border-collapse:collapse;font-family:monospace;font-size:13px">
<tr style="background:#fbbf24"><th>Title</th><th>Company</th><th>Since Applied</th></tr>
{rows}
</table>
<p><b>Tips:</b> Send a brief follow-up email to the recruiter or hiring manager. Reference your application and reiterate interest.</p>
<p style="margin-top:16px;font-size:12px;color:#888">— NaukriBaba Pipeline</p>
</body></html>"""

    try:
        gmail_user = get_param("/naukribaba/GMAIL_USER")
        gmail_pass = get_param("/naukribaba/GMAIL_APP_PASSWORD")
        msg = MIMEText(html, "html")
        msg["Subject"] = f"[NaukriBaba] Follow up on {len(followup_jobs)} application{'s' if len(followup_jobs) > 1 else ''}"
        msg["From"] = gmail_user
        msg["To"] = gmail_user
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)
        logger.info(f"[followup] Sent reminder for {len(followup_jobs)} jobs")
        return {"sent": True, "count": len(followup_jobs)}
    except Exception as e:
        logger.error(f"[followup] Email failed: {e}")
        return {"sent": False, "error": str(e)}
