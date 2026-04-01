"""Daily follow-up reminder: email jobs with status='Applied' and no change in 7+ days."""
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import boto3
from ai_helper import get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)
ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def handler(event, context):
    user_id = event.get("user_id", "default")
    db = get_supabase()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    result = db.table("jobs").select("title, company, match_score, apply_url, updated_at") \
        .eq("user_id", user_id) \
        .eq("application_status", "Applied") \
        .lt("updated_at", cutoff) \
        .order("updated_at", desc=False) \
        .limit(10) \
        .execute()

    followup_jobs = result.data or []
    if not followup_jobs:
        logger.info("[followup] No follow-up reminders needed")
        return {"sent": False, "count": 0}

    rows = ""
    for j in followup_jobs:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(j["updated_at"].replace("Z", "+00:00"))).days
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
