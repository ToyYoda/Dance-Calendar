"""Calendar, filters, event details, attendance, iCal and media (DC-01 … DC-08, DC-16)."""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import images
from app.db import get_db
from app.ical import build_calendar
from app.models import Attendance, CalendarFeed, Event, EventStatus, FilterProfile, User
from app.security import current_user, is_admin_session, require_user
from app.services import Filters, attendance_count, known_cities, local_now, public_events
from app.web import MONTHS, media_url, redirect, render

router = APIRouter()
VIEWS = ("month", "week", "list", "map")


def _parse_day(raw: str | None, default: date) -> date:
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


def _shift_month(d: date, delta: int) -> date:
    month = d.month - 1 + delta
    return date(d.year + month // 12, month % 12 + 1, 1)


@router.get("/")
def calendar(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    params = request.query_params
    # DC-04: open with the default filter profile when nothing else was asked for
    if user and not params:
        default = db.scalar(
            select(FilterProfile).where(FilterProfile.user_id == user.id, FilterProfile.is_default.is_(True))
        )
        if default:
            return redirect(f"/?{default.query}&profile={default.id}" if default.query else f"/?profile={default.id}")

    filters = Filters.from_params(params)
    view = params.get("view") if params.get("view") in VIEWS else "month"
    today = local_now().date()
    anchor = _parse_day(params.get("date"), today)

    ctx = dict(view=view, filters=filters, today=today, anchor=anchor, cities=known_cities(db))
    if view == "month":
        selected_day = _parse_day(params.get("day"), today)
        first = anchor.replace(day=1)
        grid_start = first - timedelta(days=first.weekday())
        last = _shift_month(first, 1) - timedelta(days=1)
        grid_end = last + timedelta(days=6 - last.weekday())
        events = public_events(db, filters, datetime.combine(grid_start, datetime.min.time()),
                               datetime.combine(grid_end, datetime.max.time()))
        by_day = defaultdict(list)
        for ev in events:
            by_day[ev.start.date()].append(ev)
        weeks, d = [], grid_start
        while d <= grid_end:
            weeks.append([d + timedelta(days=i) for i in range(7)])
            d += timedelta(days=7)
        if selected_day and not (grid_start <= selected_day <= grid_end):
            selected_day = None
        ctx.update(
            weeks=weeks,
            by_day=by_day,
            month_label=f"{MONTHS[first.month - 1]} {first.year}",
            prev_date=_shift_month(first, -1),
            next_date=_shift_month(first, 1),
            selected_day=selected_day,
            day_events=by_day.get(selected_day, []) if selected_day else [],
        )
    elif view == "week":
        week_start = anchor - timedelta(days=anchor.weekday())
        days = [week_start + timedelta(days=i) for i in range(7)]
        events = public_events(db, filters, datetime.combine(days[0], datetime.min.time()),
                               datetime.combine(days[-1], datetime.max.time()))
        by_day = defaultdict(list)
        for ev in events:
            by_day[ev.start.date()].append(ev)
        ctx.update(
            days=days,
            by_day=by_day,
            week_label=f"{days[0]:%d.%m.} – {days[-1]:%d.%m.%Y}",
            prev_date=week_start - timedelta(days=7),
            next_date=week_start + timedelta(days=7),
        )
    else:
        events = public_events(db, filters)[:300]
        grouped = defaultdict(list)
        for ev in events:
            grouped[ev.start.date()].append(ev)
        ctx.update(grouped=sorted(grouped.items()), events=events)
        if view == "map":
            ctx["markers"] = [
                {
                    "id": e.id,
                    "title": e.title,
                    "lat": e.lat,
                    "lng": e.lng,
                    "when": f"{e.start:%d.%m. %H:%M}",
                    "where": e.location_name or "",
                    "cancelled": e.is_cancelled,
                    "thumb": media_url(e.media.original_path, "thumb") if e.media else "",
                }
                for e in events
                if e.lat is not None and e.lng is not None
            ]

    base_q = filters.to_query()
    if params.get("profile"):
        base_q = "&".join(p for p in [base_q, f"profile={params.get('profile')}"] if p)

    def link(**extra) -> str:
        parts = [base_q] + [f"{k}={v}" for k, v in extra.items() if v]
        return "/?" + "&".join(p for p in parts if p)

    ctx.update(link=link, feed_url=f"/feed.ics?{filters.to_query()}")
    profiles = []
    if user:
        profiles = list(db.scalars(select(FilterProfile).where(FilterProfile.user_id == user.id).order_by(FilterProfile.name)))
    ctx.update(profiles=profiles, active_profile=params.get("profile"))
    return render(request, "calendar.html", **ctx)


def _visible_event(db: Session, event_id: int, request: Request, user: User | None) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404)
    if event.status in (EventStatus.LIVE, EventStatus.CANCELLED):
        return event
    if user and (event.owner_id == user.id or is_admin_session(request)):
        return event
    raise HTTPException(404)


@router.get("/events/{event_id:int}")
def event_detail(event_id: int, request: Request, db: Session = Depends(get_db),
                 user: User | None = Depends(current_user)):
    event = _visible_event(db, event_id, request, user)
    attending = bool(
        user and db.scalar(select(Attendance).where(Attendance.user_id == user.id, Attendance.event_id == event.id))
    )
    can_edit = bool(user and (event.owner_id == user.id or is_admin_session(request)))
    return render(
        request,
        "event_detail.html",
        event=event,
        count=attendance_count(db, event.id),
        attending=attending,
        can_edit=can_edit,
    )


@router.get("/events/{event_id:int}.ics")
def event_ics(event_id: int, request: Request, db: Session = Depends(get_db),
              user: User | None = Depends(current_user)):
    event = _visible_event(db, event_id, request, user)
    body = build_calendar([event], event.title or "Tanzevent")
    return Response(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="event-{event.id}.ics"'},
    )


def _toggle_attendance(db: Session, user: User, event: Event, want: bool | None) -> bool:
    existing = db.scalar(select(Attendance).where(Attendance.user_id == user.id, Attendance.event_id == event.id))
    want = (existing is None) if want is None else want
    if want and existing is None:
        if event.status != EventStatus.LIVE:
            return False
        db.add(Attendance(user_id=user.id, event_id=event.id))
        try:
            db.commit()
        except IntegrityError:  # double click: the unique constraint keeps one attendance per person
            db.rollback()
    elif not want and existing is not None:
        db.delete(existing)
        db.commit()
    return want


@router.post("/events/{event_id:int}/attend")
async def attend_form(event_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_user)):
    event = _visible_event(db, event_id, request, user)
    form = await request.form()
    _toggle_attendance(db, user, event, form.get("attend") == "1")
    return redirect(f"/events/{event.id}")


@router.get("/api/events/{event_id:int}/attendance")
def attendance_api(event_id: int, request: Request, db: Session = Depends(get_db),
                   user: User | None = Depends(current_user)):
    event = _visible_event(db, event_id, request, user)
    attending = bool(
        user and db.scalar(select(Attendance).where(Attendance.user_id == user.id, Attendance.event_id == event.id))
    )
    return {"count": attendance_count(db, event.id), "attending": attending}


@router.post("/api/events/{event_id:int}/attendance")
async def attendance_toggle_api(event_id: int, request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require_user)):
    event = _visible_event(db, event_id, request, user)
    payload = await request.json()
    attending = _toggle_attendance(db, user, event, bool(payload.get("attend")))
    return JSONResponse({"count": attendance_count(db, event.id), "attending": attending})


@router.get("/feed.ics")
def filter_feed(request: Request, db: Session = Depends(get_db)):
    """DC-07: every filter combination has its own stable, subscribable URL."""
    filters = Filters.from_params(request.query_params)
    return Response(build_calendar(public_events(db, filters), "Tanzevents"), media_type="text/calendar; charset=utf-8")


@router.get("/feeds/{token}.ics")
def feed(token: str, db: Session = Depends(get_db)):
    feed = db.scalar(select(CalendarFeed).where(CalendarFeed.token == token))
    if feed is None:
        raise HTTPException(404)
    if feed.kind == "attending":
        stmt = (
            select(Event)
            .join(Attendance, Attendance.event_id == Event.id)
            .where(Attendance.user_id == feed.user_id,
                   Event.status.in_([EventStatus.LIVE, EventStatus.CANCELLED]),
                   Event.start >= local_now() - timedelta(days=30))
            .order_by(Event.start)
        )
        events, name = list(db.scalars(stmt)), "Meine Tanzevents"
    else:
        from starlette.datastructures import QueryParams

        events, name = public_events(db, Filters.from_params(QueryParams(feed.query))), "Tanzevents"
    return Response(build_calendar(events, name), media_type="text/calendar; charset=utf-8")


MEDIA_KEY = re.compile(r"^[0-9a-f]{32}$")


@router.get("/media/{key}/{variant}")
def media(key: str, variant: str):
    if not MEDIA_KEY.match(key) or variant not in ("original", *images.VARIANTS):
        raise HTTPException(404)
    path = images.image_path(key, variant)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})
