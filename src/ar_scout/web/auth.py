"""Sign-in for the hosted dashboard. Off unless a password is set, so local use needs no login.

Two roles: "team" can use everything for scouting; "admin" can also run actions that spend API quota, rewrite
linked accounts or delete data. Sessions are signed cookies (HMAC-SHA256), so there is no session table.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .. import config

COOKIE = "ar_scout_session"
SESSION_DAYS = 30
# Wrong guesses allowed per address, and from everyone together (a backstop, since an address seen through a
# proxy header can be faked), before sign-in pauses.
MAX_FAILURES, MAX_FAILURES_ALL, FAILURE_WINDOW = 8, 40, 15 * 60

_failures: dict[str, list[float]] = {}
_lock = threading.Lock()
_secret: bytes | None = None


def _settings():
    return config.settings


def enabled() -> bool:
    s = _settings()
    return bool(s.team_password or s.admin_password)


def _key() -> bytes:
    """AR_SCOUT_SECRET, else a random secret saved next to the database so sign-ins survive restarts."""
    global _secret
    if _settings().session_secret:
        return _settings().session_secret.encode()
    if _secret is None:
        path = _settings().db_path.parent / ".session_secret"
        if path.exists():
            _secret = path.read_bytes().strip()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            _secret = secrets.token_hex(32).encode()
            path.write_bytes(_secret)
            path.chmod(0o600)
    return _secret


def _sign(payload: str) -> str:
    return hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(role: str, days: int = SESSION_DAYS) -> str:
    payload = f"{role}.{int(time.time()) + days * 86400}"
    return f"{payload}.{_sign(payload)}"


def read_token(token: str | None) -> str | None:
    """The role a cookie grants, or None if it's missing, forged or expired."""
    try:
        role, expires, sig = (token or "").split(".")
    except ValueError:
        return None
    if role not in ("team", "admin") or not hmac.compare_digest(sig, _sign(f"{role}.{expires}")):
        return None
    return role if int(expires) > time.time() else None


def check_password(password: str) -> str | None:
    s = _settings()
    given = (password or "").encode()
    if s.admin_password and hmac.compare_digest(given, s.admin_password.encode()):
        return "admin"
    if s.team_password and hmac.compare_digest(given, s.team_password.encode()):
        return "team"
    return None


def too_many_failures(addr: str) -> bool:
    now = time.time()
    with _lock:
        for key in (addr, "*"):
            _failures[key] = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]
        return len(_failures[addr]) >= MAX_FAILURES or len(_failures["*"]) >= MAX_FAILURES_ALL


def record_failure(addr: str) -> None:
    with _lock:
        for key in (addr, "*"):
            _failures.setdefault(key, []).append(time.time())
