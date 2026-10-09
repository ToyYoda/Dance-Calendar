"""Profile: name, attendances, own events, filter profiles (DC-04), calendar feeds (DC-07)."""

import secrets
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from app.config import settings
from app.db import get_db
from app.models import Attendance, CalendarFeed, Event, EventStatus, FilterProfile, User
from app.security import require_user
from app.services import Filters, local_now
from app.web import redirect, render

router = APIRouter()


def _clean_query(raw: str) -> str:
    """Keep only filter parameters (view, date, profile are not part of a saved filter)."""
    return Filters.from_params(QueryParams(raw.lstrip("?"))).to_query()


@router.get("/me")
def profile(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    now = local_now()
    attending = list(
        db.scalars(
            select(Event)
            .join(Attendance, Attendance.event_id == Event.id)
            .where(Attendance.user_id == user.id, Event.start >= now.replace(hour=0, minute=0))
            .order_by(Event.start)
        )
    )
    own_events = list(
        db.scalars(
            select(Event)
            .where(Event.owner_id == user.id, Event.status != EventStatus.REJECTED)
            .order_by(Event.start.desc().nulls_first())
        )
    )
    profiles = list(db.scalars(select(FilterProfile).where(FilterProfile.user_id == user.id).order_by(FilterProfile.name)))
    feeds = list(db.scalars(select(CalendarFeed).where(CalendarFeed.user_id == user.id).order_by(CalendarFeed.created_at)))
    return render(
        request,
        "me.html",
        attending=attending,
        own_events=own_events,
        profiles=profiles,
        feeds=feeds,
        feed_base=settings.base_url,
        describe=_describe_query,
    )


def _describe_query(query: str) -> str:
    pairs = parse_qsl(query)
    if not pairs:
        return "Alle Events"
    labels = {"style": "Stil", "type": "Typ", "level": "Level", "city": "Stadt", "q": "Suche", "from": "ab", "to": "bis"}
    return ", ".join(f"{labels.get(k, k)}: {v}" for k, v in pairs)


@router.post("/me/name")
def set_name(name: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    user.name = name.strip()[:80] or None
    db.commit()
    return redirect("/me", flash="Name gespeichert.")


# --- Filter profiles --------------------------------------------------------


def _own_profile(db: Session, user: User, profile_id: int) -> FilterProfile:
    prof = db.get(FilterProfile, profile_id)
    if prof is None or prof.user_id != user.id:
        raise HTTPException(404)
    return prof


def _set_default(db: Session, user: User, prof: FilterProfile | None) -> None:
    for other in db.scalars(select(FilterProfile).where(FilterProfile.user_id == user.id)):
        other.is_default = prof is not None and other.id == prof.id


@router.post("/me/profiles")
def create_profile(name: str = Form(...), query: str = Form(""), default: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_user)):
    name = name.strip()[:60] or "Mein Filter"
    prof = FilterProfile(user_id=user.id, name=name, query=_clean_query(query))
    db.add(prof)
    db.flush()
    if default:
        _set_default(db, user, prof)
    db.commit()
    return redirect(f"/?{prof.query}&profile={prof.id}" if prof.query else f"/?profile={prof.id}",
                    flash=f"Filter „{name}“ gespeichert.")


@router.post("/me/profiles/{profile_id:int}/rename")
def rename_profile(profile_id: int, name: str = Form(...), db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    prof = _own_profile(db, user, profile_id)
    prof.name = name.strip()[:60] or prof.name
    db.commit()
    return redirect("/me", flash="Filter umbenannt.")


@router.post("/me/profiles/{profile_id:int}/default")
def default_profile(profile_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    prof = _own_profile(db, user, profile_id)
    _set_default(db, user, None if prof.is_default else prof)
    db.commit()
    return redirect("/me", flash="Standardfilter aktualisiert.")


@router.post("/me/profiles/{profile_id:int}/delete")
def delete_profile(profile_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    db.delete(_own_profile(db, user, profile_id))
    db.commit()
    return redirect("/me", flash="Filter gelöscht.")


# --- Calendar feeds ---------------------------------------------------------


@router.post("/me/feeds")
def create_feed(kind: str = Form(...), query: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_user)):
    if kind not in ("attending", "filter"):
        raise HTTPException(400)
    if kind == "attending":
        existing = db.scalar(select(CalendarFeed).where(CalendarFeed.user_id == user.id, CalendarFeed.kind == "attending"))
        if existing:
            return redirect("/me#feeds", flash="Du hast schon einen Feed für deine Zusagen.")
    feed = CalendarFeed(token=secrets.token_urlsafe(24), user_id=user.id, kind=kind,
                        query=_clean_query(query) if kind == "filter" else "")
    db.add(feed)
    db.commit()
    return redirect("/me#feeds", flash="Kalender-Feed erstellt. Kopiere den Link in deine Kalender-App.")


@router.post("/me/feeds/{feed_id:int}/delete")
def delete_feed(feed_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    feed = db.get(CalendarFeed, feed_id)
    if feed is None or feed.user_id != user.id:
        raise HTTPException(404)
    db.delete(feed)
    db.commit()
    return redirect("/me#feeds", flash="Feed gelöscht. Der alte Link funktioniert nicht mehr.")

