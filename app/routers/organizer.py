"""Organizer self-service (DC-20), ownership (DC-21, DC-43) and edit locks (DC-34)."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from starlette.datastructures import UploadFile
from sqlalchemy.orm import Session

from app import images
from app.db import get_db
from app.forms import apply_values, form_values, parse_event_form
from app.ingestion import locate_event, run_detached
from app.models import Event, EventSource, EventStatus, Media, MediaStatus, User
from app.security import Forbidden, acquire_lock, can_edit, is_admin_session, release_lock, require_user
from app.services import cancel, make_organizer, touch
from app.web import redirect, render

router = APIRouter()
LOCATION_FIELDS = {"location_name", "address", "postcode", "city"}


async def _store_flyer(form) -> tuple[Media | None, str | None]:
    upload = form.get("flyer")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return None, None
    data = await upload.read()
    if not data:
        return None, None
    try:
        key, width, height = images.store_image(data)
    except images.InvalidImage as exc:
        return None, str(exc)
    return Media(source=EventSource.FORM, original_path=key, width=width, height=height, status=MediaStatus.FLYER), None


def _schedule_locate(background: BackgroundTasks, event: Event) -> None:
    if event.lat is None and (event.city or event.location_name):
        background.add_task(run_detached, locate_event, event.id)


@router.get("/events/new")
def new_event(request: Request, user: User = Depends(require_user)):
    return render(request, "event_form.html", event=None, values=form_values(None), errors={})


@router.post("/events/new")
async def create_event(request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                       user: User = Depends(require_user)):
    form = await request.form()
    submit = form.get("action") == "submit"
    values, errors = parse_event_form(form, require_complete=submit)
    media, flyer_error = await _store_flyer(form)
    if flyer_error:
        errors["flyer"] = flyer_error
    if errors:
        if media:
            images.delete_image(media.original_path)
        return render(request, "event_form.html", event=None, values=values, errors=errors, status_code=400)

    event = Event(owner_id=user.id, source=EventSource.FORM, submitter_phone=user.phone,
                  status=EventStatus.LIVE if submit else EventStatus.DRAFT)
    apply_values(event, values)
    if media:
        db.add(media)
        db.flush()
        event.media_id = media.id
    db.add(event)
    make_organizer(user)
    db.commit()
    _schedule_locate(background, event)
    flash = "Event veröffentlicht." if submit else "Entwurf gespeichert. Er ist erst nach dem Absenden sichtbar."
    return redirect(f"/events/{event.id}", flash=flash)


def _editable(db: Session, request: Request, user: User, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404)
    if not can_edit(user, event, is_admin_session(request)):
        raise Forbidden()
    return event


@router.get("/events/{event_id:int}/edit")
def edit_event(event_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    event = _editable(db, request, user, event_id)
    locked = not acquire_lock(db, event, user)
    return render(request, "event_form.html", event=event, values=form_values(event), errors={}, locked=locked)


@router.post("/events/{event_id:int}/edit")
async def update_event(event_id: int, request: Request, background: BackgroundTasks,
                       db: Session = Depends(get_db), user: User = Depends(require_user)):
    event = _editable(db, request, user, event_id)
    form = await request.form()
    submit = form.get("action") == "submit"
    values, errors = parse_event_form(form, require_complete=submit or event.status == EventStatus.LIVE)

    # DC-34: never overwrite silently
    if event.is_locked_for(user.id):
        errors["_form"] = (f"{event.locked_by.display_name if event.locked_by else 'Jemand'} bearbeitet dieses Event "
                           "gerade. Deine Änderungen wurden nicht gespeichert.")
    elif str(event.sequence) != form.get("sequence"):
        errors["_form"] = ("Das Event wurde inzwischen geändert (z. B. von einem Admin). "
                           "Bitte lade die Seite neu und übernimm deine Änderungen erneut.")

    media, flyer_error = await _store_flyer(form)
    if flyer_error:
        errors["flyer"] = flyer_error
    if errors:
        if media:
            images.delete_image(media.original_path)
        return render(request, "event_form.html", event=event, values=values, errors=errors,
                      locked=event.is_locked_for(user.id), status_code=409 if "_form" in errors else 400)

    changed = apply_values(event, values)
    if LOCATION_FIELDS & set(changed):
        event.lat = event.lng = None
    if media:
        db.add(media)
        db.flush()
        event.media_id = media.id
        changed.append("flyer")
    if submit and event.status == EventStatus.DRAFT:
        event.status = EventStatus.LIVE
    if changed:
        touch(event)
    event.locked_by_id = None
    event.locked_until = None
    db.commit()
    _schedule_locate(background, event)
    msg = "Änderungen gespeichert."
    if event.status == EventStatus.ADMIN_REVIEW and not is_admin_session(request):
        msg += " Das Event ist noch in Prüfung und erscheint nach der Freigabe."
    return redirect(f"/events/{event.id}", flash=msg)


@router.post("/events/{event_id:int}/unlock")
def unlock_event(event_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    event = db.get(Event, event_id)
    if event is not None:
        release_lock(db, event, user)
    return {"ok": True}


@router.post("/events/{event_id:int}/delete")
def delete_event(event_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    event = _editable(db, request, user, event_id)
    if event.is_locked_for(user.id):
        return redirect(f"/events/{event.id}", flash="Das Event wird gerade bearbeitet und kann nicht gelöscht werden.")
    db.delete(event)
    db.commit()
    return redirect("/me", flash="Event gelöscht.")


@router.post("/events/{event_id:int}/cancel")
def cancel_event(event_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    event = _editable(db, request, user, event_id)
    if event.status == EventStatus.LIVE:
        cancel(db, event)
    return redirect(f"/events/{event.id}", flash="Event als abgesagt markiert.")


@router.get("/events/{event_id:int}/disown")
def disown_form(event_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    event = db.get(Event, event_id)
    if event is None or event.owner_id != user.id:
        raise HTTPException(404)
    return render(request, "disown.html", event=event)


@router.post("/events/{event_id:int}/disown")
def disown(event_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """DC-21: 'Nicht mein Event' hands the event over to the admins."""
    event = db.get(Event, event_id)
    if event is None or event.owner_id != user.id:
        raise HTTPException(404)
    event.owner_id = None
    db.commit()
    return redirect("/me", flash="Danke! Das Event gehört jetzt den Admins und ist aus deiner Übersicht verschwunden.")
