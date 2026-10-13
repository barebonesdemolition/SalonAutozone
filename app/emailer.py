"""Sending email through Brevo's HTTPS API (no SMTP ports needed)."""
from __future__ import annotations

import html
import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def email_enabled() -> bool:
    cfg = get_settings()
    return bool(cfg.BREVO_API_KEY and cfg.EMAIL_FROM)


async def send_email(to_email: str, to_name: str, subject: str, text: str, html_body: str) -> bool:
    """Send one email. Returns True when Brevo accepted it. Never raises."""
    cfg = get_settings()
    if not email_enabled() or not to_email:
        return False
    payload = {
        "sender": {"name": cfg.EMAIL_FROM_NAME or "SalonAutoZone", "email": cfg.EMAIL_FROM},
        "to": [{"email": to_email, "name": (to_name or to_email)[:70]}],
        "subject": subject,
        "textContent": text,
        "htmlContent": html_body,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(BREVO_URL, json=payload,
                                  headers={"api-key": cfg.BREVO_API_KEY, "accept": "application/json"})
        if r.status_code >= 300:
            log.warning("Brevo refused the email (%s): %s", r.status_code, r.text[:300])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("Could not reach Brevo: %s", exc)
        return False


def code_email(first_name: str, code: str, minutes: int) -> tuple[str, str, str]:
    """Subject, plain text and HTML for a password reset code."""
    name = first_name or "there"
    subject = f"Your SalonAutoZone code: {code}"
    text = (f"Hello {name},\n\nYour code to reset your SalonAutoZone password is: {code}\n\n"
            f"It expires in {minutes} minutes. If you didn't ask for it, ignore this email; "
            "your password stays the same.\n\nSalonAutoZone")
    e = html.escape
    body = f"""<div style="font-family:Arial,sans-serif;max-width:460px;margin:0 auto;padding:24px;color:#1c2b2b">
<p style="font-size:16px">Hello {e(name)},</p>
<p style="font-size:16px">Your code to reset your SalonAutoZone password is:</p>
<p style="font-size:34px;font-weight:bold;letter-spacing:8px;background:#e3f3f1;color:#0e2a2b;padding:14px 0;text-align:center;border-radius:10px">{e(code)}</p>
<p style="font-size:14px;color:#5d6f6e">It expires in {minutes} minutes. If you didn't ask for it, ignore this email; your password stays the same.</p>
<p style="font-size:14px;color:#5d6f6e">SalonAutoZone</p></div>"""
    return subject, text, body
