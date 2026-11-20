"""Branded HTML emails (welcome + subscription receipt) sent from EMAIL_SENDER_C over SMTP."""

import asyncio
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from ..config import get_settings

logger = logging.getLogger("app.email")

BRAND = "Kandidatex"
ACCENT = "#1d6bff"
ACCENT_DARK = "#0a1b3d"


def _layout(preheader: str, title: str, body_html: str, button_label: str, button_url: str) -> str:
    """Table-based layout with inline CSS so it renders in Gmail, Outlook and phone clients."""
    year = datetime.now().year
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#f3f4f8;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f8;padding:32px 12px;">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(17,24,39,0.08);">
    <tr><td style="background:{ACCENT_DARK};padding:30px 40px;">
      <div style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:0.2px;">Kandidate<span style="color:#2f80ff;">x</span></div>
      <div style="font-size:13px;color:#3fe0d0;margin-top:4px;">Smarter, safer remote interviews</div>
    </td></tr>
    <tr><td style="height:4px;line-height:4px;font-size:0;background:{ACCENT};background-image:linear-gradient(90deg,#14d9c5,{ACCENT});">&nbsp;</td></tr>
    <tr><td style="padding:36px 40px 12px 40px;color:#111827;">
      <h1 style="margin:0 0 16px 0;font-size:24px;line-height:1.3;color:#111827;">{escape(title)}</h1>
      {body_html}
    </td></tr>
    <tr><td align="left" style="padding:8px 40px 40px 40px;">
      <a href="{escape(button_url, quote=True)}" style="display:inline-block;background:{ACCENT};color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;padding:14px 28px;border-radius:10px;">{escape(button_label)}</a>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:24px 40px;border-top:1px solid #e5e7eb;color:#6b7280;font-size:12px;line-height:1.6;">
      Questions? Just reply to this email and our team will help.<br>
      &copy; {year} {BRAND}. All rights reserved.
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


def _p(text: str) -> str:
    return f'<p style="margin:0 0 16px 0;font-size:15px;line-height:1.65;color:#374151;">{text}</p>'


def _row(label: str, value: str) -> str:
    return (
        '<tr>'
        f'<td style="padding:10px 0;font-size:14px;color:#6b7280;border-bottom:1px solid #eef0f4;">{escape(label)}</td>'
        f'<td align="right" style="padding:10px 0;font-size:14px;font-weight:600;color:#111827;border-bottom:1px solid #eef0f4;">{escape(value)}</td>'
        '</tr>'
    )


def _table(rows: list[tuple[str, str]]) -> str:
    body = "".join(_row(label, value) for label, value in rows)
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:8px 0 24px 0;background:#f9fafb;border-radius:12px;padding:6px 20px;">'
        f"{body}</table>"
    )


def render_welcome(name: str, plan_name: str, interviews: int) -> tuple[str, str, str]:
    """Returns (subject, html, plain text)."""
    first = (name.strip().split() or ["there"])[0]
    url = get_settings().app_url
    subject = f"Welcome to {BRAND}, {first}!"
    body = (
        _p(f"Hi {escape(first)},")
        + _p(
            f"Welcome aboard, and thank you for choosing {BRAND}. Your <strong>{escape(plan_name)}</strong> plan is "
            f"live, so you can start monitoring interviews right away."
        )
        + _p(f"You can run up to <strong>{interviews} interviews</strong> during your plan, each with live camera, screen and "
             "readiness checks, so you can focus on the candidate.")
        + _p("<strong>Get started in three steps:</strong><br>"
             "1. Create an interview and share the interview ID.<br>"
             "2. The candidate joins from their phone and completes calibration.<br>"
             "3. Watch the live feed and review the timeline and recording afterwards.")
    )
    text = (
        f"Hi {first},\n\nWelcome to {BRAND}! Your {plan_name} plan is live and includes {interviews} interviews."
        f"\n\n1. Create an interview and share the ID.\n2. The candidate joins and completes calibration.\n"
        f"3. Watch live and review the recording afterwards.\n\nOpen your dashboard: {url}\n"
    )
    return subject, _layout(f"Your {plan_name} plan is live.", f"Welcome to {BRAND}", body, "Open your dashboard", url), text


def render_subscription(
    name: str,
    plan_name: str,
    amount_display: str,
    interviews: int,
    payment_id: str,
    paid_on: datetime,
    valid_until: datetime | None,
) -> tuple[str, str, str]:
    first = (name.strip().split() or ["there"])[0]
    url = get_settings().app_url
    valid = valid_until.strftime("%d %b %Y") if valid_until else "30 days"
    rows = [
        ("Plan", plan_name),
        ("Amount paid", amount_display),
        ("Billing", "One-time payment, no auto-renewal"),
        ("Interviews included", str(interviews)),
        ("Valid until", valid),
        ("Payment ID", payment_id),
        ("Paid on", paid_on.strftime("%d %b %Y")),
    ]
    subject = f"Your {BRAND} payment is confirmed"
    body = (
        _p(f"Hi {escape(first)},")
        + _p(f"Thank you! We received your payment and your plan is active. Here is your receipt:")
        + _table(rows)
        + _p(f"Your plan includes <strong>{interviews} interviews</strong> and is valid until {valid}. "
             "You can see your remaining interviews and payment history in your dashboard at any time.")
    )
    text = (
        f"Hi {first},\n\nThank you! Your {BRAND} subscription is active.\n\n"
        + "\n".join(f"{label}: {value}" for label, value in rows)
        + f"\n\nYour {interviews} interviews are valid until {valid}.\nDashboard: {url}\n"
    )
    html = _layout("We received your payment.", "Payment confirmed", body, "View my plan", url)
    return subject, html, text


def _send_sync(to: str, subject: str, html: str, text: str) -> None:
    settings = get_settings()
    if not settings.email_sender_c or not settings.email_password_c:
        logger.warning("Email not configured (EMAIL_SENDER_C / EMAIL_PASSWORD_C); skipped %r to %s", subject, to)
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.email_sender_name, settings.email_sender_c))
    message["To"] = to
    message["Reply-To"] = settings.email_sender_c
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.email_sender_c, settings.email_password_c)
        smtp.send_message(message)


async def send_email(to: str, subject: str, html: str, text: str) -> None:
    """Never raises: a failed email must not undo a payment that already went through."""
    try:
        await asyncio.to_thread(_send_sync, to, subject, html, text)
    except Exception:  # noqa: BLE001
        logger.exception("Could not send %r to %s", subject, to)
