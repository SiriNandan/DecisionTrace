"""Sends the invitation email. Needs SMTP_HOST (and usually SMTP_USER / SMTP_PASSWORD / SMTP_FROM); without it nothing is sent."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("decisiontrace.mail")


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST", "").strip())


def send_invite(to: str, name: str, lender: str, role_name: str, key: str, expires: str | None) -> str:
    """Returns "sent", "not_configured" or "failed". The key is never logged."""
    if not configured():
        return "not_configured"
    base = os.environ.get("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "no-reply@clearly.local"
    msg = EmailMessage()
    msg["Subject"] = f"You've been invited to Clearly by {lender}"
    msg["From"], msg["To"] = sender, to
    msg.set_content(
        f"Hello {name},\n\n{lender} has given you access to Clearly as: {role_name}.\n\n"
        f"Your access key (keep it private; it is shown only in this email):\n\n    {key}\n\n"
        f"To sign in, open {base}/?p=signin and paste the key under \"I'm a lender\".\n"
        + (f"The key works until {expires[:10]}.\n" if expires else "")
        + "\nIf you weren't expecting this, ignore this email and tell your administrator.\n"
    )
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=10) as s:
            if os.environ.get("SMTP_STARTTLS", "1") != "0":
                s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return "sent"
    except (OSError, smtplib.SMTPException) as e:
        log.warning("invite email failed: %s", type(e).__name__)
        return "failed"
