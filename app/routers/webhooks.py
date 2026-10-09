"""WhatsApp Cloud API webhook (DC-10, DC-15)."""

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app import images
from app.config import settings
from app.db import get_db
from app.ingestion import handle_text, process_media, receive_image, run_detached
from app.messaging import download_media, send_message
from app.phone import InvalidPhone, normalize_phone

router = APIRouter(prefix="/webhooks")
log = logging.getLogger(__name__)


@router.get("/whatsapp")
def verify(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and settings.whatsapp_verify_token
        and hmac.compare_digest(params.get("hub.verify_token", ""), settings.whatsapp_verify_token)
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(403)


def _signature_ok(body: bytes, header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        return settings.dev_mode  # unsigned webhooks are accepted only in development
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _handle_image(db: Session, phone: str, media_id: str) -> None:
    try:
        data = download_media(media_id)
        media = receive_image(db, data, phone)
    except images.InvalidImage:
        send_message(db, phone, "Das Bild konnte ich leider nicht lesen. Bitte als JPG oder PNG schicken.")
        return
    process_media(db, media.id)


@router.post("/whatsapp")
async def receive(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.body()
    if not _signature_ok(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403)
    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                try:
                    phone = normalize_phone("+" + msg.get("from", ""))
                except InvalidPhone:
                    continue
                kind = msg.get("type")
                if kind == "image":
                    # Answer the webhook fast; downloading and AI processing run afterwards
                    background.add_task(run_detached, _handle_image, phone, msg["image"]["id"])
                elif kind == "text":
                    handle_text(db, phone, msg.get("text", {}).get("body", ""))
                else:
                    send_message(db, phone, "Bitte schick mir einen Event-Flyer als Bild.")
    return {"ok": True}
