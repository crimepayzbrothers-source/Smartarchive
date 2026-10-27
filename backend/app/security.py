"""Passwörter (scrypt), Zwei-Faktor (TOTP nach RFC 6238) und Sitzungen."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import config
from .db import now

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


# --- TOTP -----------------------------------------------------------------

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_now(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((at if at is not None else time.time()) // 30))


def verify_totp(secret: str, code: str, at: float | None = None) -> bool:
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return False
    counter = int((at if at is not None else time.time()) // 30)
    return any(hmac.compare_digest(_hotp(secret, counter + d), code) for d in (-1, 0, 1))


def totp_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/SmartArchive:{quote(username)}?secret={secret}&issuer=SmartArchive"


# --- Sitzungen ------------------------------------------------------------

COOKIE_NAME = "sa_session"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.settings.session_days)
    conn.execute(
        "INSERT INTO sessions(id, user_id, created_at, expires_at, user_agent) VALUES(?,?,?,?,?)",
        (_token_hash(token), user_id, now(), expires.replace(microsecond=0).isoformat(), user_agent[:200]),
    )
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    r = conn.execute(
        "SELECT u.id, u.username, u.totp_enabled, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (_token_hash(token),),
    ).fetchone()
    if not r:
        return None
    if r["expires_at"] < now():
        conn.execute("DELETE FROM sessions WHERE id = ?", (_token_hash(token),))
        return None
    return {"id": r["id"], "username": r["username"], "totp_enabled": bool(r["totp_enabled"])}


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE id = ?", (_token_hash(token),))


class LoginThrottle:
    """Einfache Bremse gegen Passwort-Raten: wachsende Sperre nach Fehlversuchen."""

    def __init__(self) -> None:
        self.failures: dict[str, tuple[int, float]] = {}

    def blocked_for(self, key: str) -> float:
        count, last = self.failures.get(key, (0, 0.0))
        if count < 5:
            return 0.0
        wait = min(2 ** (count - 5) * 5, 900)
        remaining = last + wait - time.time()
        return max(0.0, remaining)

    def fail(self, key: str) -> None:
        count, _ = self.failures.get(key, (0, 0.0))
        self.failures[key] = (count + 1, time.time())

    def ok(self, key: str) -> None:
        self.failures.pop(key, None)


throttle = LoginThrottle()
