import os
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

EMAIL_SENDER   = os.getenv("EMAIL_SENDER")    # your Gmail address
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")  # your Gmail app password
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")  # where to send alerts (can be same as sender)

IST = timezone(timedelta(hours=5, minutes=30))


def _send_email(subject: str, html_body: str):
    """Core SMTP sender via Gmail."""
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        logger.error("EMAIL_SENDER or EMAIL_PASSWORD not set — skipping email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER or EMAIL_SENDER
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER or EMAIL_SENDER, msg.as_string())
        logger.info(f"Email sent: {subject}")
    except Exception as e:
        logger.error(f"Email send failed: {e}")


def _format_job_html(job: dict) -> str:
    """Format a single job as an HTML block."""
    score   = job.get("score", 0)
    urgency = job.get("urgency", "low")

    if score >= 9:
        score_emoji = "🔥🔥"
    elif score >= 8:
        score_emoji = "🔥"
    elif score >= 7:
        score_emoji = "⚡"
    else:
        score_emoji = "💡"

    urgency_label = {"high": "Apply Today", "medium": "Apply Soon", "low": "Review"}.get(urgency, "Review")
    urgency_color = {"high": "#d32f2f", "medium": "#f57c00", "low": "#388e3c"}.get(urgency, "#555")

    highlights = job.get("highlights", "")
    highlight_html = ""
    if highlights:
        for h in highlights.split(", ")[:3]:
            highlight_html += f"<li>✅ {h}</li>"

    red_flags = job.get("red_flags", "")
    red_flag_html = ""
    if red_flags and red_flags != "None":
        for rf in red_flags.split(", ")[:2]:
            red_flag_html += f"<li>⚠️ {rf}</li>"

    salary_line = f"<p>💰 {job.get('salary', '')}</p>" if job.get("salary") else ""

    return f"""
    <div style="border:1px solid #ddd; border-left: 4px solid {urgency_color};
                border-radius:6px; padding:16px; margin-bottom:20px; font-family:sans-serif;">
        <h2 style="margin:0 0 4px 0;">{score_emoji} {job.get('title', 'N/A')}</h2>
        <p style="margin:2px 0; color:#555;">🏢 {job.get('company', 'N/A')}</p>
        <p style="margin:2px 0; color:#555;">📍 {job.get('location', 'N/A')}</p>
        {salary_line}
        <p style="margin:6px 0;">
            📊 Score: <strong>{score}/10</strong> —
            <span style="color:{urgency_color}; font-weight:bold;">{urgency_label}</span>
        </p>
        {"<p><strong>Why it matches:</strong></p><ul>" + highlight_html + "</ul>" if highlight_html else ""}
        {"<p><strong>Watch out:</strong></p><ul>" + red_flag_html + "</ul>" if red_flag_html else ""}
        <p>
            <a href="{job.get('url', '#')}"
               style="background:{urgency_color}; color:white; padding:8px 16px;
                      border-radius:4px; text-decoration:none; font-weight:bold;">
               Apply Here
            </a>
        </p>
        <p style="color:#aaa; font-size:12px;">Source: {job.get('source', 'unknown')}</p>
    </div>
    """


def notify_urgent_jobs(urgent_jobs: list[dict], chat_id: str = ""):
    """Send one email containing all urgent (score 8+) job alerts."""
    if not urgent_jobs:
        return

    now = datetime.now(IST)
    date_str = now.strftime("%-d %b %Y")
    time_str = now.strftime("%H:%M")

    jobs_html = "".join(_format_job_html(job) for job in urgent_jobs)

    html = f"""
    <div style="font-family:sans-serif; max-width:700px; margin:auto;">
        <h1 style="color:#d32f2f;">🚨 JobRadar — {len(urgent_jobs)} Urgent Alert(s)</h1>
        <p style="color:#555;">{date_str} · {time_str} IST</p>
        {jobs_html}
    </div>
    """

    subject = f"🚨 JobRadar: {len(urgent_jobs)} urgent job(s) — {date_str}"
    _send_email(subject, html)


def notify_digest_jobs(digest_jobs: list[dict], chat_id: str = ""):
    """Send a digest email with top 15 medium-priority jobs (score 5-7)."""
    if not digest_jobs:
        return

    top_jobs = sorted(digest_jobs, key=lambda j: j.get("score", 0), reverse=True)[:15]

    now = datetime.now(IST)
    date_str = now.strftime("%-d %b %Y")
    time_str = now.strftime("%H:%M")

    jobs_html = "".join(_format_job_html(job) for job in top_jobs)

    html = f"""
    <div style="font-family:sans-serif; max-width:700px; margin:auto;">
        <h1 style="color:#f57c00;">💡 JobRadar Digest — Top {len(top_jobs)} to Review</h1>
        <p style="color:#555;">{date_str} · {time_str} IST</p>
        {jobs_html}
    </div>
    """

    subject = f"💡 JobRadar Digest: {len(top_jobs)} jobs to review — {date_str}"
    _send_email(subject, html)


def send_session_divider(
    total_raw: int,
    passed:    int,
    scored:    int,
    urgent:    int,
    chat_id:   str = "",
):
    """Send a run summary email at the end of every pipeline run."""
    now = datetime.now(IST)
    date_str = now.strftime("%-d %b %Y")
    time_str = now.strftime("%H:%M")

    digest  = scored - urgent
    status  = "✅ No urgent alerts this run." if urgent == 0 else f"🚨 {urgent} urgent job(s) sent separately."

    html = f"""
    <div style="font-family:sans-serif; max-width:700px; margin:auto;
                border:1px solid #ddd; border-radius:6px; padding:24px;">
        <h2 style="margin:0 0 12px 0;">📊 JobRadar Run Summary</h2>
        <p style="color:#555;">{date_str} · {time_str} IST</p>
        <table style="border-collapse:collapse; width:100%; margin:16px 0;">
            <tr style="background:#f5f5f5;">
                <td style="padding:8px 12px;">📥 Fetched</td>
                <td style="padding:8px 12px; font-weight:bold;">{total_raw} jobs</td>
            </tr>
            <tr>
                <td style="padding:8px 12px;">🔍 Passed filter</td>
                <td style="padding:8px 12px; font-weight:bold;">{passed} jobs</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:8px 12px;">🤖 AI scored</td>
                <td style="padding:8px 12px; font-weight:bold;">{scored} jobs</td>
            </tr>
            <tr>
                <td style="padding:8px 12px;">🔥 Urgent</td>
                <td style="padding:8px 12px; font-weight:bold; color:#d32f2f;">{urgent} jobs</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:8px 12px;">💡 To review</td>
                <td style="padding:8px 12px; font-weight:bold;">{digest} jobs</td>
            </tr>
        </table>
        <p>{status}</p>
    </div>
    """

    subject = f"📊 JobRadar: {urgent} urgent · {digest} to review — {date_str}"
    _send_email(subject, html)


# ── Unused stubs kept so nothing else breaks if imported ─────────────────────
async def send_run_summary(total_raw: int, passed_filter: int, scored: int, urgent: int):
    pass