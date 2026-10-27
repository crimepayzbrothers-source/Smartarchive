"""Einrichtung, Anmeldung, Zwei-Faktor, Passwort."""
from __future__ import annotations

import hmac

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import config
from ..db import audit, now
from ..security import (COOKIE_NAME, create_session, delete_session, hash_password, new_totp_secret,
                        throttle, totp_uri, verify_password, verify_totp)
from .common import ApiError, body, endpoint


class SetupIn(BaseModel):
    setup_token: str
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    username: str
    password: str
    totp: str | None = None


class CodeIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=10, max_length=200)


def _set_cookie(resp: JSONResponse, token: str) -> None:
    resp.set_cookie(COOKIE_NAME, token, max_age=config.settings.session_days * 86400, httponly=True,
                    secure=config.settings.cookie_secure, samesite="strict", path="/")


@endpoint(auth=False)
async def status(request: Request, conn, user):
    has_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    return {
        "setup_required": not has_user,
        "setup_possible": bool(config.settings.setup_token),
        "user": user,
        "ai_available": config.settings.ai_available,
    }


@endpoint(auth=False)
async def setup(request: Request, conn, user):
    data = await body(request, SetupIn)
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        raise ApiError(409, "Die App ist bereits eingerichtet.")
    if not config.settings.setup_token:
        raise ApiError(403, "Einrichtung gesperrt: SETUP_TOKEN in der .env setzen.")
    if not hmac.compare_digest(data.setup_token.strip(), config.settings.setup_token):
        raise ApiError(403, "Einrichtungscode ist falsch.")
    uid = conn.execute("INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
                       (data.username.strip(), hash_password(data.password), now())).lastrowid
    audit(conn, "setup", "user", uid)
    token = create_session(conn, uid, request.headers.get("user-agent", ""))
    resp = JSONResponse({"ok": True, "user": {"id": uid, "username": data.username.strip(), "totp_enabled": False}})
    _set_cookie(resp, token)
    return resp


@endpoint(auth=False)
async def login(request: Request, conn, user):
    data = await body(request, LoginIn)
    key = (request.client.host if request.client else "?") + "|" + data.username.lower()
    wait = throttle.blocked_for(key)
    if wait > 0:
        raise ApiError(429, f"Zu viele Fehlversuche. Bitte {int(wait) + 1} Sekunden warten.")
    u = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (data.username.strip(),)).fetchone()
    if not u or not verify_password(data.password, u["password_hash"]):
        throttle.fail(key)
        raise ApiError(401, "Benutzername oder Passwort falsch.")
    if u["totp_enabled"]:
        if not data.totp:
            return JSONResponse({"need_totp": True}, status_code=401)
        if not verify_totp(u["totp_secret"], data.totp):
            throttle.fail(key)
            raise ApiError(401, "Code aus der Authenticator-App ist falsch.", need_totp=True)
    throttle.ok(key)
    token = create_session(conn, u["id"], request.headers.get("user-agent", ""))
    audit(conn, "login", "user", u["id"])
    resp = JSONResponse({"ok": True, "user": {"id": u["id"], "username": u["username"], "totp_enabled": bool(u["totp_enabled"])}})
    _set_cookie(resp, token)
    return resp


@endpoint()
async def logout(request: Request, conn, user):
    delete_session(conn, request.cookies.get(COOKIE_NAME))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@endpoint()
async def totp_start(request: Request, conn, user):
    if user["totp_enabled"]:
        raise ApiError(409, "Zwei-Faktor ist bereits aktiv.")
    secret = new_totp_secret()
    conn.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user["id"]))
    return {"secret": secret, "uri": totp_uri(secret, user["username"])}


@endpoint()
async def totp_confirm(request: Request, conn, user):
    data = await body(request, CodeIn)
    secret = conn.execute("SELECT totp_secret FROM users WHERE id = ?", (user["id"],)).fetchone()[0]
    if not secret or not verify_totp(secret, data.code):
        raise ApiError(400, "Code passt nicht. Uhrzeit am Handy prüfen und neuen Code eingeben.")
    conn.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (user["id"],))
    audit(conn, "totp_enabled", "user", user["id"])
    return {"ok": True}


class PasswordOnly(BaseModel):
    password: str


@endpoint()
async def totp_off(request: Request, conn, user):
    data = await body(request, PasswordOnly)
    u = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not verify_password(data.password, u[0]):
        raise ApiError(403, "Passwort falsch.")
    conn.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL WHERE id = ?", (user["id"],))
    audit(conn, "totp_disabled", "user", user["id"])
    return {"ok": True}


@endpoint()
async def change_password(request: Request, conn, user):
    data = await body(request, PasswordIn)
    u = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not verify_password(data.old_password, u[0]):
        raise ApiError(403, "Bisheriges Passwort falsch.")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(data.new_password), user["id"]))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))  # alle Geräte abmelden
    token = create_session(conn, user["id"], request.headers.get("user-agent", ""))
    audit(conn, "password_changed", "user", user["id"])
    resp = JSONResponse({"ok": True})
    _set_cookie(resp, token)
    return resp


routes = [
    Route("/api/auth/status", status, methods=["GET"]),
    Route("/api/auth/setup", setup, methods=["POST"]),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/totp/start", totp_start, methods=["POST"]),
    Route("/api/auth/totp/confirm", totp_confirm, methods=["POST"]),
    Route("/api/auth/totp/disable", totp_off, methods=["POST"]),
    Route("/api/auth/password", change_password, methods=["POST"]),
]
