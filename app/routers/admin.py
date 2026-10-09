"""Admin area: triage (DC-30), review (DC-31), swipe (DC-32), batch (DC-33), locks (DC-34),
monitoring (DC-35), spam folder (DC-11), cancellation requests (DC-15) and a WhatsApp bot simulator."""

from collections import Counter

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import images
from app.config import settings
from app.db import get_db
from app.forms import REQUIRED, apply_values, form_values, parse_event_form
from app.ingestion import (
    create_event_from_media,
    handle_text,
    locate_event,
    process_media,
    receive_image,
    run_detached,
)
from app.messaging import send_message
from app.models import (
    EXTRACTED_FIELDS,
    CancellationRequest,
    Event,
    EventSource,
    EventStatus,
    FieldCorrection,
    Media,
    MediaStatus,
    OutboundMessage,
    ReviewDecision,
    User,
)
from app.phone import InvalidPhone, normalize_phone
from app.security import acquire_lock, release_lock, require_admin
from app.services import (
    cancel,
    event_url,
    get_threshold,
    publish,
    reject,
    set_threshold,
    touch,
)
from app.web import FIELD_LABELS, fmt_date, media_url, redirect, render

router = APIRouter(prefix="/admin")
SORTS = {"date": (Event.start.asc().nulls_last(),), "score": (Event.confidence.asc().nulls_first(),),
         "new": (Event.created_at.desc(),)}


def _queue(db: Session, status: str = EventStatus.ADMIN_REVIEW, source: str = "", sort: str = "date"):
    stmt = select(Event).where(Event.status == status)
    if source in (EventSource.BOT, EventSource.FORM):
        stmt = stmt.where(Event.source == source)
    return list(db.scalars(stmt.order_by(*SORTS.get(sort, SORTS["date"]), Event.id)))


def _counts(db: Session) -> dict:
    return {
        "review": db.scalar(select(func.count()).where(Event.status == EventStatus.ADMIN_REVIEW)),
        "spam": db.scalar(select(func.count()).where(Media.status.in_([MediaStatus.SPAM, MediaStatus.FAILED]))),
        "cancellations": db.scalar(select(func.count()).where(CancellationRequest.resolved.is_(False))),
    }


def low_fields(event: Event, threshold: float) -> list[str]:
    conf = event.field_confidence or {}
    verified = set(event.verified_fields or [])
    return [f for f, score in conf.items() if score < threshold and f not in verified]


# --- Triage list ------------------------------------------------------------


@router.get("")
def triage(request: Request, status: str = EventStatus.ADMIN_REVIEW, source: str = "", sort: str = "date",
           db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if status not in EventStatus.__members__:
        status = EventStatus.ADMIN_REVIEW
    threshold = get_threshold(db, "field_threshold")
    events = _queue(db, status, source, sort)
    return render(request, "admin/triage.html", events=events, status=status, source=source, sort=sort,
                  counts=_counts(db), threshold=threshold, low_fields=low_fields, admin=admin)


# --- Review -----------------------------------------------------------------


def _next_in_queue(db: Session, current_id: int) -> int | None:
    for ev in _queue(db):
        if ev.id != current_id:
            return ev.id
    return None


def _review_context(db: Session, event: Event, admin: User, values=None, errors=None, locked=False):
    threshold = get_threshold(db, "field_threshold")
    return dict(
        event=event,
        values=values or form_values(event),
        errors=errors or {},
        locked=locked,
        threshold=threshold,
        low=low_fields(event, threshold),
        next_id=_next_in_queue(db, event.id),
        admin=admin,
        counts=_counts(db),
    )


@router.get("/events/{event_id:int}")
def review(event_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404)
    locked = not acquire_lock(db, event, admin)
    return render(request, "admin/review.html", **_review_context(db, event, admin, locked=locked))


@router.post("/events/{event_id:int}")
async def review_submit(event_id: int, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404)
    form = await request.form()
    action = form.get("action", "save")
    next_id = _next_in_queue(db, event.id)
    after = f"/admin/events/{next_id}" if next_id else "/admin"

    if event.is_locked_for(admin.id):
        return render(request, "admin/review.html", status_code=409,
                      **_review_context(db, event, admin, locked=True,
                                        errors={"_form": "Ein anderer Admin bearbeitet dieses Event gerade."}))

    if action == "delete":
        db.add(ReviewDecision(event_id=event.id, admin_id=admin.id, action="delete", previous_status=event.status))
        db.delete(event)
        db.commit()
        return redirect(after, flash="Event gelöscht.")
    if action == "reject":
        release_lock(db, event, admin)
        reject(db, event, admin)
        return redirect(after, flash="Event abgelehnt. Der Einreicher wurde benachrichtigt.")

    values, errors = parse_event_form(form, require_complete=(action == "approve"))
    if errors:
        return render(request, "admin/review.html", status_code=400,
                      **_review_context(db, event, admin, values=values, errors=errors))

    old = {name: getattr(event, name) for name in values}
    changed = apply_values(event, values)

    candidate = form.get("candidate")
    if candidate not in (None, "") and event.address_candidates:
        try:
            chosen = event.address_candidates[int(candidate)]
        except (ValueError, IndexError):
            chosen = None
        if chosen:
            for name in ("address", "postcode", "city"):
                if chosen.get(name) and getattr(event, name) != chosen[name]:
                    old.setdefault(name, getattr(event, name))
                    setattr(event, name, chosen[name])
                    changed.append(name)
            event.lat, event.lng = chosen["lat"], chosen["lng"]
            event.address_candidates = []
    elif {"location_name", "address", "postcode", "city"} & set(changed):
        event.lat = event.lng = None

    if event.source == EventSource.BOT:
        conf = event.field_confidence or {}
        for name in changed:
            if name in EXTRACTED_FIELDS:
                db.add(FieldCorrection(event_id=event.id, field=name, old_value=_str(old.get(name)),
                                       new_value=_str(getattr(event, name)), confidence=conf.get(name),
                                       admin_id=admin.id))
    event.verified_fields = sorted(set(event.verified_fields or []) | set(changed))
    if changed:
        touch(event)
    event.locked_by_id = None
    event.locked_until = None
    db.commit()
    if event.lat is None:
        _run(background, locate_event, event.id)

    if action == "approve":
        publish(db, event, admin=admin)
        return redirect(after, flash=f"„{event.title}“ ist live.")
    return redirect(f"/admin/events/{event.id}", flash="Gespeichert.")


def _str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


@router.post("/events/{event_id:int}/unlock")
def unlock(event_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    event = db.get(Event, event_id)
    if event:
        release_lock(db, event, admin)
    return {"ok": True}


@router.post("/events/{event_id:int}/cancel")
def admin_cancel(event_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404)
    cancel(db, event)
    return redirect(f"/events/{event.id}", flash="Event abgesagt.")


# --- Swipe review (mobile) -----------------------------------------------------


@router.get("/swipe")
def swipe(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return render(request, "admin/swipe.html", counts=_counts(db))


@router.get("/api/queue")
def queue_api(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    threshold = get_threshold(db, "field_threshold")
    items = []
    for ev in _queue(db):
        items.append(
            {
                "id": ev.id,
                "title": ev.title or "(ohne Titel)",
                "when": f"{fmt_date(ev.start)} {ev.start:%H:%M} Uhr" if ev.start else "Datum fehlt",
                "where": ", ".join(p for p in [ev.location_name, ev.city] if p) or "Ort fehlt",
                "styles": ev.dance_styles or [],
                "confidence": ev.confidence,
                "low": [FIELD_LABELS.get(f, f) for f in low_fields(ev, threshold)],
                "missing": [FIELD_LABELS[f] for f in REQUIRED if not getattr(ev, f)],
                "preview": media_url(ev.media.original_path, "preview") if ev.media else "",
                "original": media_url(ev.media.original_path, "original") if ev.media else "",
                "locked_by": ev.locked_by.display_name if ev.is_locked_for(admin.id) and ev.locked_by else None,
            }
        )
    return {"items": items}


@router.post("/api/events/{event_id:int}/decision")
async def decision_api(event_id: int, request: Request, db: Session = Depends(get_db),
                       admin: User = Depends(require_admin)):
    payload = await request.json()
    action = payload.get("action")
    event = db.get(Event, event_id)
    if event is None:
        return JSONResponse({"error": "Event nicht gefunden."}, status_code=404)
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "Unbekannte Aktion."}, status_code=400)
    if event.status != EventStatus.ADMIN_REVIEW:
        return JSONResponse({"error": "Das Event wurde schon bearbeitet."}, status_code=409)
    if event.is_locked_for(admin.id):
        return JSONResponse({"error": f"{event.locked_by.display_name} bearbeitet dieses Event gerade."},
                            status_code=409)
    if action == "approve":
        missing = [FIELD_LABELS[f] for f in REQUIRED if not getattr(event, f)]
        if missing:
            return JSONResponse({"error": "Es fehlen Pflichtfelder: " + ", ".join(missing)}, status_code=422)
        publish(db, event, admin=admin)
    else:
        reject(db, event, admin)
    decision = db.scalar(
        select(ReviewDecision).where(ReviewDecision.event_id == event.id, ReviewDecision.admin_id == admin.id)
        .order_by(ReviewDecision.id.desc()).limit(1)
    )
    return {"ok": True, "decision_id": decision.id}


@router.post("/api/undo")
async def undo_api(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    payload = await request.json()
    decision = db.get(ReviewDecision, int(payload.get("decision_id", 0)))
    if decision is None or decision.admin_id != admin.id or decision.undone or decision.event_id is None:
        return JSONResponse({"error": "Diese Aktion kann nicht rückgängig gemacht werden."}, status_code=400)
    event = db.get(Event, decision.event_id)
    if event is None:
        return JSONResponse({"error": "Event existiert nicht mehr."}, status_code=400)
    event.status = decision.previous_status
    event.auto_published = False
    touch(event)
    decision.undone = True
    db.commit()
    return {"ok": True, "event_id": event.id}


# --- Batch gallery ------------------------------------------------------------


@router.get("/gallery")
def gallery(request: Request, status: str = EventStatus.ADMIN_REVIEW, db: Session = Depends(get_db),
            admin: User = Depends(require_admin)):
    if status not in EventStatus.__members__:
        status = EventStatus.ADMIN_REVIEW
    return render(request, "admin/gallery.html", events=_queue(db, status, sort="new"), status=status,
                  counts=_counts(db))


@router.post("/batch")
async def batch(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    form = await request.form()
    action = form.get("action")
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    if not ids or action not in ("approve", "reject", "not_event", "delete", "set_location"):
        return redirect("/admin/gallery", flash="Bitte Events und eine Aktion auswählen.")

    location = {k: (form.get(k) or "").strip() or None for k in ("location_name", "address", "postcode", "city")}
    if action == "set_location" and not (location["location_name"] or location["city"]):
        return redirect("/admin/gallery", flash="Für „Ort zuweisen“ mindestens Location oder Stadt angeben.")

    done, failed = 0, []
    for event_id in ids:
        event = db.get(Event, event_id)
        try:
            if event is None:
                raise ValueError("existiert nicht mehr")
            if event.is_locked_for(admin.id):
                raise ValueError(f"gesperrt von {event.locked_by.display_name}")
            if action == "approve":
                missing = [FIELD_LABELS[f] for f in REQUIRED if not getattr(event, f)]
                if missing:
                    raise ValueError("fehlt: " + ", ".join(missing))
                publish(db, event, admin=admin)
            elif action == "reject":
                reject(db, event, admin)
            elif action in ("not_event", "delete"):
                db.add(ReviewDecision(event_id=event.id, admin_id=admin.id, action=action,
                                      previous_status=event.status))
                if action == "not_event" and event.media:
                    event.media.status = MediaStatus.SPAM
                db.delete(event)
                db.commit()
            elif action == "set_location":
                for name, value in location.items():
                    if value is not None and getattr(event, name) != value:
                        if event.source == EventSource.BOT:
                            db.add(FieldCorrection(event_id=event.id, field=name, old_value=getattr(event, name),
                                                   new_value=value, confidence=(event.field_confidence or {}).get(name),
                                                   admin_id=admin.id))
                        setattr(event, name, value)
                        event.verified_fields = sorted(set(event.verified_fields or []) | {name})
                event.lat = event.lng = None
                event.address_candidates = []
                touch(event)
                db.commit()
            done += 1
        except Exception as exc:  # report and continue with the rest (DC-33)
            db.rollback()
            title = event.title if event is not None else f"#{event_id}"
            failed.append(f"„{title or '#' + str(event_id)}“: {exc}")
    return render(request, "admin/batch_result.html", done=done, failed=failed, action=action, counts=_counts(db))


# --- Spam folder / failed media -------------------------------------------------


@router.get("/spam")
def spam(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    items = list(db.scalars(
        select(Media).where(Media.status.in_([MediaStatus.SPAM, MediaStatus.FAILED])).order_by(Media.received_at.desc())
    ))
    return render(request, "admin/spam.html", items=items, counts=_counts(db))


@router.post("/media/{media_id:int}/restore")
def restore_media(media_id: int, background: BackgroundTasks, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)):
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(404)
    if media.status == MediaStatus.FAILED:
        _run(background, process_media, media.id)
        return redirect("/admin/spam", flash="Verarbeitung wird erneut gestartet.")
    media.status = MediaStatus.FLYER  # classifier_label stays "no_flyer": counted as false negative
    db.commit()
    _run(background, _extract_restored, media.id)
    return redirect("/admin/spam", flash="Bild wird als Flyer verarbeitet und erscheint gleich in der Prüfliste.")


def _extract_restored(db: Session, media_id: int) -> None:
    media = db.get(Media, media_id)
    event = create_event_from_media(db, media, classifier_score=0.5)
    # Restored media always gets a human look
    if event.status != EventStatus.ADMIN_REVIEW:
        event.status = EventStatus.ADMIN_REVIEW
        db.commit()


@router.post("/media/{media_id:int}/delete")
def delete_media(media_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(404)
    images.delete_image(media.original_path)
    db.delete(media)
    db.commit()
    return redirect("/admin/spam", flash="Bild gelöscht.")


# --- Cancellation requests ----------------------------------------------------------


@router.get("/cancellations")
def cancellations(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    reqs = list(db.scalars(select(CancellationRequest).where(CancellationRequest.resolved.is_(False))
                           .order_by(CancellationRequest.created_at)))
    events = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_({i for r in reqs for i in r.candidate_ids})))}
    return render(request, "admin/cancellations.html", reqs=reqs, events=events, counts=_counts(db))


@router.post("/cancellations/{req_id:int}")
def resolve_cancellation(req_id: int, event_id: str = Form(""), db: Session = Depends(get_db),
                         admin: User = Depends(require_admin)):
    req = db.get(CancellationRequest, req_id)
    if req is None:
        raise HTTPException(404)
    req.resolved = True
    db.commit()
    if event_id.isdigit():
        event = db.get(Event, int(event_id))
        if event is not None:
            cancel(db, event)
            send_message(db, req.sender_phone, f"🛑 „{event.title}“ ist jetzt als ABGESAGT markiert. {event_url(event)}")
            return redirect("/admin/cancellations", flash=f"„{event.title}“ abgesagt.")
    return redirect("/admin/cancellations", flash="Anfrage verworfen.")


# --- Monitoring (DC-35) -------------------------------------------------------------


def compute_stats(db: Session) -> dict:
    bot_events = db.scalar(select(func.count()).where(Event.source == EventSource.BOT)) or 0
    decisions = list(db.scalars(select(ReviewDecision).where(ReviewDecision.undone.is_(False))))
    auto = db.scalar(select(func.count()).where(Event.source == EventSource.BOT, Event.auto_published.is_(True))) or 0
    not_event = sum(1 for d in decisions if d.action == "not_event")
    processed = bot_events + not_event  # deleted "not an event" drafts were bot events too
    reviewed_ids = {d.event_id for d in decisions if d.action in ("approve", "reject")}
    reviewed = len(reviewed_ids) + auto
    corrections = Counter(db.scalars(select(FieldCorrection.field)))
    field_errors = [
        {"field": FIELD_LABELS.get(f, f), "count": corrections.get(f, 0),
         "rate": (corrections.get(f, 0) / reviewed) if reviewed else 0.0}
        for f in EXTRACTED_FIELDS
    ]
    field_errors.sort(key=lambda r: -r["rate"])

    media = list(db.scalars(select(Media).where(Media.source == EventSource.BOT)))
    timings = sorted(m.classifier_ms for m in media if m.classifier_ms is not None)
    restored = sum(1 for m in media if m.classifier_label == "no_flyer" and m.status == MediaStatus.FLYER)
    classified = sum(1 for m in media if m.classifier_label)
    return {
        "bot_events": processed,
        "auto_published": auto,
        "automation_rate": (auto / processed) if processed else 0.0,
        "in_review": db.scalar(select(func.count()).where(Event.status == EventStatus.ADMIN_REVIEW)) or 0,
        "approved": sum(1 for d in decisions if d.action == "approve"),
        "rejected": sum(1 for d in decisions if d.action == "reject"),
        "field_errors": field_errors,
        "media_total": len(media),
        "spam": sum(1 for m in media if m.status == MediaStatus.SPAM),
        "false_negatives": restored,
        "false_positives": not_event,
        "classifier_accuracy": (1 - (restored + not_event) / classified) if classified else None,
        "classifier_avg_ms": (sum(timings) / len(timings)) if timings else None,
        "classifier_p95_ms": timings[int(len(timings) * 0.95) - 1] if len(timings) >= 20 else None,
        "failed": sum(1 for m in media if m.status == MediaStatus.FAILED),
    }


@router.get("/stats")
def stats(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return render(request, "admin/stats.html", stats=compute_stats(db), counts=_counts(db),
                  auto_threshold=get_threshold(db, "auto_publish_threshold"),
                  field_threshold=get_threshold(db, "field_threshold"))


@router.post("/settings")
def update_settings(auto_publish_threshold: float = Form(...), field_threshold: float = Form(...),
                    db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    set_threshold(db, "auto_publish_threshold", auto_publish_threshold)
    set_threshold(db, "field_threshold", field_threshold)
    return redirect("/admin/stats", flash="Schwellenwerte gespeichert.")


# --- Bot simulator (development without WhatsApp) -------------------------------


def _run(background: BackgroundTasks, fn, *args) -> None:
    if settings.ingest_sync:
        run_detached(fn, *args)
    else:
        background.add_task(run_detached, fn, *args)


@router.get("/bot")
def bot(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    messages = list(db.scalars(select(OutboundMessage).order_by(OutboundMessage.id.desc()).limit(40)))
    return render(request, "admin/bot.html", messages=messages, counts=_counts(db), admin=admin)


@router.post("/bot/image")
async def bot_image(request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                    admin: User = Depends(require_admin)):
    form = await request.form()
    try:
        phone = normalize_phone(form.get("phone") or "")
    except InvalidPhone as exc:
        return redirect("/admin/bot", flash=str(exc))
    upload = form.get("image")
    if not isinstance(upload, UploadFile):
        return redirect("/admin/bot", flash="Bitte ein Bild auswählen.")
    try:
        media = receive_image(db, await upload.read(), phone)
    except images.InvalidImage as exc:
        return redirect("/admin/bot", flash=str(exc))
    _run(background, process_media, media.id)
    return redirect("/admin/bot", flash="Bild empfangen. Die KI verarbeitet es im Hintergrund.")


@router.post("/bot/text")
def bot_text(phone: str = Form(...), text: str = Form(...), db: Session = Depends(get_db),
             admin: User = Depends(require_admin)):
    try:
        digits = normalize_phone(phone)
    except InvalidPhone as exc:
        return redirect("/admin/bot", flash=str(exc))
    handle_text(db, digits, text)
    return redirect("/admin/bot", flash="Nachricht verarbeitet.")
