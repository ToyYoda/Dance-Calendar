"""Outgoing WhatsApp messages (OTP codes, confirmations, notifications).

MESSENGER=console only records messages in the database (visible in the admin bot simulator).
MESSENGER=cloud sends them through the WhatsApp Cloud API.
"""

import logging

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models import OutboundMessage

log = logging.getLogger(__name__)


def _send_cloud(to_phone: str, body: str) -> None:
    url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": body, "preview_url": True},
        },
        timeout=15,
    )
    resp.raise_for_status()


def send_message(db: Session, to_phone: str, body: str, log_as: str | None = None) -> bool:
    """Send a WhatsApp text. log_as replaces the stored body for secrets (codes, login links)."""
    msg = OutboundMessage(to_phone=to_phone, body=log_as or body)
    try:
        if settings.messenger == "cloud":
            _send_cloud(to_phone, body)
        else:
            log.info("WhatsApp → +%s: %s", to_phone, log_as or body)
    except Exception as exc:  # network errors must not break the request that triggered the message
        log.exception("WhatsApp message to +%s failed", to_phone)
        msg.ok = False
        msg.error = str(exc)[:500]
    db.add(msg)
    db.commit()
    return msg.ok


def download_media(media_id: str) -> bytes:
    """Fetch an image the user sent to the bot (WhatsApp Cloud API)."""
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    meta = requests.get(
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/{media_id}", headers=headers, timeout=15
    )
    meta.raise_for_status()
    data = requests.get(meta.json()["url"], headers=headers, timeout=30)
    data.raise_for_status()
    return data.content
