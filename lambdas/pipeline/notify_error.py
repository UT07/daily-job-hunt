import logging
import smtplib
from email.mime.text import MIMEText

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


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
