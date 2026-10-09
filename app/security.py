"""Authentication: WhatsApp OTP (DC-40), sessions (DC-41), admin magic links (DC-42), roles (DC-43)."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AuthSession, Event, MagicLink, OtpCode, Role, User, utcnow

SESSION_COOKIE = "dc_session"
SESSION_DAYS = 30
SESSION_REFRESH_AFTER = timedelta(hours=12)
OTP_TTL = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_MAX_PER_WINDOW = 3
MAGIC_LINK_TTL = timedelta(minutes=15)
LOCK_TTL = timedelta(minutes=5)


class LoginRequired(Exception):
    def __init__(self, admin: bool = False):
        self.admin = admin


class Forbidden(Exception):
    pass


class RateLimited(Exception):
    pass


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def is_whitelisted_admin(user: User | None) -> bool:
    return bool(user and user.phone in settings.admin_phones)


def get_or_create_user(db: Session, phone: str) -> User:
    user = db.scalar(select(User).where(User.phone == phone))
    if user is None:
        user = User(phone=phone, role=Role.USER)
        db.add(user)
        db.flush()
    if is_whitelisted_admin(user):
        user.role = Role.ADMIN
    elif user.role == Role.ADMIN:
        # Admin rights exist only through the whitelist
        user.role = Role.ORGANIZER if db.scalar(select(func.count()).where(Event.owner_id == user.id)) else Role.USER
    return user


# --- OTP -------------------------------------------------------------------


def create_otp(db: Session, phone: str) -> str:
    window_start = utcnow() - OTP_TTL
    recent = db.scalar(
        select(func.count()).select_from(OtpCode).where(OtpCode.phone == phone, OtpCode.created_at > window_start)
    )
    if recent >= OTP_MAX_PER_WINDOW:
        raise RateLimited()
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(OtpCode(phone=phone, code_hash=_hash(f"{phone}:{code}"), expires_at=utcnow() + OTP_TTL))
    db.commit()
    return code


def verify_otp(db: Session, phone: str, code: str) -> bool:
    otp = db.scalar(
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.used.is_(False), OtpCode.expires_at > utcnow())
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if otp is None or otp.attempts >= OTP_MAX_ATTEMPTS:
        return False
    otp.attempts += 1
    ok = hmac.compare_digest(otp.code_hash, _hash(f"{phone}:{code.strip()}"))
    if ok:
        otp.used = True
    db.commit()
    return ok


# --- Sessions --------------------------------------------------------------


def create_session(db: Session, user: User, is_admin: bool = False) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_hash(token),
            user_id=user.id,
            is_admin=is_admin,
            expires_at=utcnow() + timedelta(days=SESSION_DAYS),
        )
    )
    db.commit()
    return token


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash(token)))
    if sess:
        sess.revoked = True
        db.commit()


def _load_session(request: Request, db: Session) -> AuthSession | None:
    if hasattr(request.state, "auth_session"):
        return request.state.auth_session
    sess = None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash(token)))
        now = utcnow()
        if sess is None or sess.revoked or sess.expires_at <= now:
            sess = None
        elif now - sess.last_seen_at > SESSION_REFRESH_AFTER:
            # Sliding refresh: active users stay logged in
            sess.last_seen_at = now
            sess.expires_at = now + timedelta(days=SESSION_DAYS)
            db.commit()
        if sess is not None and sess.is_admin and not is_whitelisted_admin(sess.user):
            sess.is_admin = False
            db.commit()
    request.state.auth_session = sess
    return sess


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession | None:
    return _load_session(request, db)


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    sess = _load_session(request, db)
    return sess.user if sess else None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    sess = _load_session(request, db)
    if sess is None:
        raise LoginRequired()
    return sess.user


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    sess = _load_session(request, db)
    if sess is None or not sess.is_admin:
        raise LoginRequired(admin=True)
    return sess.user


def is_admin_session(request: Request) -> bool:
    sess = getattr(request.state, "auth_session", None)
    return bool(sess and sess.is_admin)


# --- Magic links -----------------------------------------------------------


def create_magic_link(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(MagicLink(token_hash=_hash(token), user_id=user.id, expires_at=utcnow() + MAGIC_LINK_TTL))
    db.commit()
    return f"{settings.base_url}/admin/login/{token}"


def consume_magic_link(db: Session, token: str) -> User | None:
    link = db.scalar(select(MagicLink).where(MagicLink.token_hash == _hash(token)))
    if link is None or link.used or link.expires_at <= utcnow():
        return None
    link.used = True
    db.commit()
    user = db.get(User, link.user_id)
    return user if is_whitelisted_admin(user) else None


# --- Ownership -------------------------------------------------------------


def can_edit(user: User | None, event: Event, admin_session: bool) -> bool:
    if user is None:
        return False
    if admin_session:
        return True
    return event.owner_id == user.id


def acquire_lock(db: Session, event: Event, user: User) -> bool:
    """First come, first served lock for 5 minutes (DC-34). Returns False if someone else holds it."""
    now = utcnow()
    if event.is_locked_for(user.id, now):
        return False
    event.locked_by_id = user.id
    event.locked_until = now + LOCK_TTL
    db.commit()
    return True


def release_lock(db: Session, event: Event, user: User) -> None:
    if event.locked_by_id == user.id:
        event.locked_by_id = None
        event.locked_until = None
        db.commit()
