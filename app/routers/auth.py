"""WhatsApp-OTP login for everyone (DC-40, DC-41) and magic links for admins (DC-42)."""

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.messaging import send_message
from app.phone import InvalidPhone, normalize_phone
from app.security import (
    SESSION_COOKIE,
    RateLimited,
    consume_magic_link,
    create_magic_link,
    create_otp,
    create_session,
    current_user,
    get_or_create_user,
    is_whitelisted_admin,
    revoke_session,
    set_session_cookie,
    verify_otp,
)
from app.web import redirect, render, safe_next

router = APIRouter()


@router.get("/login")
def login_form(request: Request, next: str = "/", _user=Depends(current_user)):
    return render(request, "login.html", step="phone", next=safe_next(next))


@router.post("/login")
def login_send(request: Request, phone: str = Form(...), next: str = Form("/"), db: Session = Depends(get_db),
               _user=Depends(current_user)):
    try:
        digits = normalize_phone(phone)
    except InvalidPhone as exc:
        return render(request, "login.html", step="phone", next=safe_next(next), error=str(exc), phone=phone,
                      status_code=400)
    try:
        code = create_otp(db, digits)
    except RateLimited:
        return render(request, "login.html", step="phone", next=safe_next(next), phone=phone, status_code=429,
                      error="Zu viele Codes angefordert. Bitte warte ein paar Minuten.")
    send_message(db, digits, f"Dein Dance-Calendar-Code: {code}\nEr ist 10 Minuten gültig. Gib ihn nicht weiter.",
                 log_as="[Login-Code gesendet]")
    return render(request, "login.html", step="code", phone=digits, next=safe_next(next),
                  dev_code=code if settings.dev_mode and settings.messenger != "cloud" else None)


@router.post("/login/verify")
def login_verify(request: Request, phone: str = Form(...), code: str = Form(...), next: str = Form("/"),
                 db: Session = Depends(get_db), _user=Depends(current_user)):
    try:
        digits = normalize_phone(phone)
    except InvalidPhone:
        return redirect("/login")
    if not verify_otp(db, digits, code):
        return render(request, "login.html", step="code", phone=digits, next=safe_next(next), status_code=400,
                      error="Der Code ist falsch oder abgelaufen.")
    user = get_or_create_user(db, digits)
    db.commit()
    token = create_session(db, user)
    response = redirect(safe_next(next), flash="Du bist angemeldet.")
    set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    response = redirect("/", flash="Du bist abgemeldet.")
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/admin/login")
def admin_login_form(request: Request, next: str = "/admin", _user=Depends(current_user)):
    return render(request, "admin/login.html", next=safe_next(next, "/admin"))


@router.post("/admin/login")
def admin_login_send(request: Request, phone: str = Form(...), db: Session = Depends(get_db),
                     _user=Depends(current_user)):
    dev_link = None
    try:
        digits = normalize_phone(phone)
    except InvalidPhone as exc:
        return render(request, "admin/login.html", error=str(exc), phone=phone, status_code=400)
    if digits in settings.admin_phones:
        user = get_or_create_user(db, digits)
        db.commit()
        link = create_magic_link(db, user)
        send_message(db, digits, f"Dein Admin-Login (15 Minuten gültig, nur einmal nutzbar):\n{link}",
                     log_as="[Admin-Login-Link gesendet]")
        if settings.dev_mode and settings.messenger != "cloud":
            dev_link = link
    # Same answer for every number, so the whitelist cannot be probed
    return render(request, "admin/login.html", sent=True, dev_link=dev_link)


@router.get("/admin/login/{token}")
def admin_login_confirm(token: str, request: Request, _user=Depends(current_user)):
    # Consumed only by the POST below: WhatsApp link previews must not burn the one-time link
    return render(request, "admin/login.html", confirm_token=token)


@router.post("/admin/login/{token}")
def admin_login_consume(token: str, request: Request, db: Session = Depends(get_db),
                        _user=Depends(current_user)):
    user = consume_magic_link(db, token)
    if user is None or not is_whitelisted_admin(user):
        return render(request, "admin/login.html", status_code=400,
                      error="Der Link ist ungültig, abgelaufen oder wurde schon benutzt.")
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    token_value = create_session(db, user, is_admin=True)
    response = redirect("/admin", flash="Admin-Anmeldung erfolgreich.")
    set_session_cookie(response, token_value)
    return response
