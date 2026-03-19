"""Email notification after each pipeline run.

Sends a summary email via Gmail SMTP (free, no third-party service needed).
Includes the Excel tracker as an attachment and S3 presigned URLs for assets.

Setup:
1. Go to https://myaccount.google.com/apppasswords
2. Create an App Password for "Mail" (requires 2FA enabled)
3. Set these secrets in GitHub Actions:
   - GMAIL_ADDRESS: your Gmail address
   - GMAIL_APP_PASSWORD: the 16-character app password
4. Or set them as environment variables for local runs
"""

from __future__ import annotations
import logging
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import date
from pathlib import Path
from typing import List, Optional
from scrapers.base import Job

logger = logging.getLogger(__name__)


def send_summary_email(
    matched_jobs: List[Job],
    raw_count: int,
    unique_count: int,
    gmail_address: str,
    gmail_app_password: str,
    recipient: str = None,
    tracker_path: str = None,
    tracker_url: str = None,
) -> bool:
    """Send a daily summary email with matched jobs, assets, and Excel tracker.

    Args:
        matched_jobs: List of matched Job objects
        raw_count: Total raw jobs scraped
        unique_count: Unique jobs after dedup
        gmail_address: Gmail address to send from
        gmail_app_password: Gmail App Password (not regular password)
        recipient: Email to send to (defaults to gmail_address)
        tracker_path: Local path to Excel tracker file (attached to email)
        tracker_url: S3 presigned URL for the tracker (included in email body)

    Returns:
        True if email sent successfully
    """
    recipient = recipient or gmail_address
    today = date.today().isoformat()

    all_85 = sum(
        1 for j in matched_jobs
        if j.ats_score >= 85 and j.hiring_manager_score >= 85 and j.tech_recruiter_score >= 85
    )

    subject = f"Job Hunt: {len(matched_jobs)} matches ({all_85} ready) — {today}"

    # Count assets
    resumes_count = sum(1 for j in matched_jobs if j.resume_s3_url or j.tailored_pdf_path)
    cls_count = sum(1 for j in matched_jobs if j.cover_letter_s3_url or j.cover_letter_pdf_path)

    # --- HTML body ---
    html = f"""
    <html>
    <body style="font-family: -apple-system, Arial, sans-serif; max-width: 800px; margin: 0 auto;">
    <h2 style="color: #1F4E79;">Daily Job Hunt — {today}</h2>

    <table style="border-collapse: collapse; margin-bottom: 20px;">
        <tr><td style="padding: 4px 12px; color: #666;">Scraped:</td><td><strong>{raw_count}</strong></td></tr>
        <tr><td style="padding: 4px 12px; color: #666;">Unique:</td><td><strong>{unique_count}</strong></td></tr>
        <tr><td style="padding: 4px 12px; color: #666;">Matched:</td><td><strong>{len(matched_jobs)}</strong></td></tr>
        <tr><td style="padding: 4px 12px; color: #666;">All 3 scores 85+:</td><td><strong style="color: {'#2E7D32' if all_85 > 0 else '#C62828'};">{all_85}</strong></td></tr>
        <tr><td style="padding: 4px 12px; color: #666;">Resumes generated:</td><td><strong>{resumes_count}</strong></td></tr>
        <tr><td style="padding: 4px 12px; color: #666;">Cover letters:</td><td><strong>{cls_count}</strong></td></tr>
    </table>
    """

    # Tracker download link
    if tracker_url:
        html += f"""
    <p style="margin-bottom: 16px;">
        <a href="{tracker_url}" style="background: #1F4E79; color: white; padding: 10px 20px;
           text-decoration: none; border-radius: 4px; font-weight: bold;">
           Download Full Tracker (Excel)
        </a>
        <span style="color: #999; font-size: 12px; margin-left: 8px;">Link expires in 30 days</span>
    </p>
    """
    elif tracker_path and Path(tracker_path).exists():
        html += """
    <p style="margin-bottom: 16px; color: #666; font-size: 13px;">
        Excel tracker attached to this email.
    </p>
    """

    if matched_jobs:
        # Sort by initial_match_score (true job fit) for display
        display_jobs = sorted(matched_jobs, key=lambda j: j.initial_match_score or j.match_score, reverse=True)

        html += """
        <h3 style="color: #1F4E79;">Top Matches</h3>
        <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
        <tr style="background: #1F4E79; color: white;">
            <th style="padding: 8px; text-align: left;">Match</th>
            <th style="padding: 8px; text-align: left;">Tailored</th>
            <th style="padding: 8px; text-align: left;">Title</th>
            <th style="padding: 8px; text-align: left;">Company</th>
            <th style="padding: 8px; text-align: left;">Assets</th>
            <th style="padding: 8px; text-align: left;">Apply</th>
        </tr>
        """

        for i, job in enumerate(display_jobs[:15]):
            bg = "#f2f2f2" if i % 2 == 0 else "#ffffff"

            # Initial match score (true job fit — different per job)
            init_score = job.initial_match_score or job.match_score
            init_color = "#2E7D32" if init_score >= 80 else "#F57F17" if init_score >= 65 else "#C62828"

            # Final tailored score (post-improvement — usually 85+)
            final_ats = int(job.ats_score)
            final_hm = int(job.hiring_manager_score)
            final_tr = int(job.tech_recruiter_score)

            # Apply link
            apply_link = f'<a href="{job.apply_url}" style="color: #0563C1;">Apply</a>' if job.apply_url else "—"

            # Asset links (S3 presigned URLs)
            asset_links = []
            if job.resume_s3_url:
                asset_links.append(f'<a href="{job.resume_s3_url}" style="color: #0563C1; font-size: 11px;">Resume</a>')
            if job.cover_letter_s3_url:
                asset_links.append(f'<a href="{job.cover_letter_s3_url}" style="color: #0563C1; font-size: 11px;">Cover Letter</a>')
            if not asset_links:
                if job.tailored_pdf_path:
                    asset_links.append('<span style="color: #999; font-size: 11px;">In tracker</span>')
                else:
                    asset_links.append('<span style="color: #ccc; font-size: 11px;">—</span>')
            assets_html = " | ".join(asset_links)

            # LinkedIn contacts
            contact_links = ""
            if job.linkedin_contacts:
                try:
                    contacts = json.loads(job.linkedin_contacts)
                    for c in contacts[:2]:
                        url = c.get("search_url", "")
                        role = c.get("role", "")
                        if url:
                            contact_links += f'<br><a href="{url}" style="color: #0563C1; font-size: 10px;">{role}</a>'
                except json.JSONDecodeError:
                    pass

            html += f"""
            <tr style="background: {bg};">
                <td style="padding: 8px; color: {init_color}; font-weight: bold;">{init_score}</td>
                <td style="padding: 8px; font-size: 11px;">{final_ats}/{final_hm}/{final_tr}</td>
                <td style="padding: 8px;">{job.title}</td>
                <td style="padding: 8px;">{job.company}</td>
                <td style="padding: 8px;">{assets_html}</td>
                <td style="padding: 8px;">{apply_link}{contact_links}</td>
            </tr>
            """

        html += "</table>"

        html += """
        <p style="margin-top: 8px; color: #999; font-size: 11px;">
            <strong>Match</strong> = initial job fit score (how well the job matches your profile).
            <strong>Tailored</strong> = ATS/HM/TR scores after resume tailoring.
        </p>
        """

    html += """
    <p style="margin-top: 20px; color: #999; font-size: 12px;">
        Generated by your Job Automation Pipeline. Full tracker attached / linked above.
    </p>
    </body>
    </html>
    """

    # --- Plain text fallback ---
    plain = f"Daily Job Hunt — {today}\n\n"
    plain += f"Scraped: {raw_count} | Unique: {unique_count} | Matched: {len(matched_jobs)} | All 85+: {all_85}\n"
    plain += f"Resumes: {resumes_count} | Cover Letters: {cls_count}\n\n"
    if tracker_url:
        plain += f"Tracker: {tracker_url}\n\n"
    for job in matched_jobs[:15]:
        init = job.initial_match_score or job.match_score
        plain += f"[{init}] {job.title} @ {job.company} — {job.apply_url}\n"
        if job.resume_s3_url:
            plain += f"  Resume: {job.resume_s3_url}\n"
        if job.cover_letter_s3_url:
            plain += f"  Cover Letter: {job.cover_letter_s3_url}\n"

    # --- Build email with mixed content (HTML + attachments) ---
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = recipient

    # HTML/text alternative part
    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(plain, "plain"))
    alt_part.attach(MIMEText(html, "html"))
    msg.attach(alt_part)

    # Attach Excel tracker
    if tracker_path and Path(tracker_path).exists():
        try:
            tracker_file = Path(tracker_path)
            attachment = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with open(tracker_file, "rb") as f:
                attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=f"job_tracker_{today}.xlsx",
            )
            msg.attach(attachment)
            logger.info(f"[EMAIL] Attached tracker: {tracker_file.name}")
        except Exception as e:
            logger.warning(f"[EMAIL] Failed to attach tracker: {e}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_app_password)
            server.send_message(msg)
        logger.info(f"[EMAIL] Summary sent to {recipient} (with attachments)")
        return True
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send: {e}")
        return False
