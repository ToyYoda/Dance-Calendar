import re

import pytest

from app.models import AuthSession, OutboundMessage, Role, User
from app.phone import InvalidPhone, normalize_phone
from tests.conftest import ADMIN_PHONE, USER_PHONE, admin_login, login


def test_normalize_phone():
    assert normalize_phone("0170 123 4567", "49") == "491701234567"
    assert normalize_phone("+49 (170) 123-4567", "49") == "491701234567"
    assert normalize_phone("0049 170 1234567", "49") == "491701234567"
    with pytest.raises(InvalidPhone):
        normalize_phone("abc", "49")


def test_otp_login_creates_profile_and_session(client, db):
    login(client, USER_PHONE)
    user = db.query(User).filter_by(phone=USER_PHONE).one()
    assert user.role == Role.USER
    assert client.get("/me").status_code == 200
    # the code itself is never stored in the outbound log
    assert all(not re.search(r"\d{6}", m.body) for m in db.query(OutboundMessage))


def test_wrong_code_is_rejected_and_code_is_single_use(client):
    r = client.post("/login", data={"phone": "+" + USER_PHONE, "next": "/"})
    code = re.search(r'class="mono">(\d{6})<', r.text).group(1)
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/login/verify", data={"phone": USER_PHONE, "code": wrong}).status_code == 400
    assert client.post("/login/verify", data={"phone": USER_PHONE, "code": code}, follow_redirects=False).status_code == 303
    client.post("/logout")
    assert client.post("/login/verify", data={"phone": USER_PHONE, "code": code}).status_code == 400


def test_otp_rate_limit(client):
    for _ in range(3):
        assert client.post("/login", data={"phone": "+" + USER_PHONE}).status_code == 200
    assert client.post("/login", data={"phone": "+" + USER_PHONE}).status_code == 429


def test_logout_revokes_session_server_side(client, db):
    login(client, USER_PHONE)
    token = client.cookies.get("dc_session")
    client.post("/logout")
    client.cookies.set("dc_session", token)
    assert client.get("/me", follow_redirects=False).status_code == 303
    assert db.query(AuthSession).one().revoked


def test_admin_magic_link_only_for_whitelist(client, db):
    r = client.post("/admin/login", data={"phone": "+" + USER_PHONE})
    assert "Login-Link öffnen" not in r.text
    assert "Wenn diese Nummer als Admin freigeschaltet ist" in r.text
    admin_login(client)
    assert client.get("/admin").status_code == 200
    assert db.query(User).filter_by(phone=ADMIN_PHONE).one().role == Role.ADMIN


def test_magic_link_is_single_use_and_get_does_not_consume(client):
    r = client.post("/admin/login", data={"phone": "+" + ADMIN_PHONE})
    link = re.search(r'href="https?://[^/"]+(/admin/login/[^"]+)"', r.text).group(1)
    assert client.get(link).status_code == 200  # link preview / first open
    assert client.post(link, follow_redirects=False).status_code == 303
    client.post("/logout")
    assert client.post(link).status_code == 400


def test_otp_login_is_not_admin(client):
    login(client, ADMIN_PHONE)
    assert client.get("/admin", follow_redirects=False).headers["location"].startswith("/admin/login")
