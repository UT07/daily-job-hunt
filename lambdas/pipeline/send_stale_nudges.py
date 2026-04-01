"""Weekly stale job nudge: email jobs with status='New' older than 7 days."""
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from ai_helper import get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)

import boto3
ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def handler(event, context):
    user_id = event.get("user_id", "default")
    db = get_supabase()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    result = db.table("jobs").select("title, company, match_score, first_seen, apply_url") \
        .eq("user_id", user_id) \
        .eq("application_status", "New") \
        .lt("first_seen", cutoff) \
        .order("match_score", desc=True) \
        .limit(15) \
        .execute()

    stale_jobs = result.data or []
    if not stale_jobs:
        logger.info("[stale_nudge] No stale jobs found")
        return {"sent": False, "count": 0}

    # Build email
    rows = ""
    for j in stale_jobs:
        score = j.get("match_score", 0)
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(j["first_seen"].replace("Z", "+00:00"))).days
        apply_link = f'<a href="{j.get("apply_url", "#")}">Apply</a>' if j.get("apply_url") else ""
        rows += f"<tr><td>{j['title']}</td><td>{j['company']}</td><td>{score}</td><td>{days}d ago</td><td>{apply_link}</td></tr>\n"

    html = f"""<html><body>
<h2>🔔 You have {len(stale_jobs)} stale job{'' if len(stale_jobs) == 1 else 's'}</h2>
<p>These matched jobs have been sitting in "New" status for over 7 days. Time to apply or dismiss!</p>
<table border="1" cellpadding="6" style="border-collapse:collapse;font-family:monospace;font-size:13px">
<tr style="background:#fbbf24"><th>Title</th><th>Company</th><th>Score</th><th>Age</th><th>Action</th></tr>
{rows}
</table>
<p style="margin-top:16px;font-size:12px;color:#888">— NaukriBaba Pipeline</p>
</body></html>"""

    try:
        gmail_user = get_param("/naukribaba/GMAIL_USER")
        gmail_pass = get_param("/naukribaba/GMAIL_APP_PASSWORD")
        msg = MIMEText(html, "html")
        msg["Subject"] = f"[NaukriBaba] {len(stale_jobs)} jobs waiting for you"
        msg["From"] = gmail_user
        msg["To"] = gmail_user
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)
        logger.info(f"[stale_nudge] Sent nudge for {len(stale_jobs)} stale jobs")
        return {"sent": True, "count": len(stale_jobs)}
    except Exception as e:
        logger.error(f"[stale_nudge] Email failed: {e}")
        return {"sent": False, "error": str(e)}
