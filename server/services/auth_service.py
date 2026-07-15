from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from server.db import get_session
from server.models import User, UserSession


SESSION_COOKIE_NAME = "content_review_session"
DEFAULT_SESSION_TTL = timedelta(hours=12)
_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
_DUMMY_PASSWORD_HASH: Optional[str] = None


@dataclass(frozen=True)
class SessionSecrets:
    session_token: str
    csrf_token: str
    expires_at: datetime


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _dummy_password_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
    return _DUMMY_PASSWORD_HASH


def _session_secret() -> bytes:
    value = os.environ.get("SESSION_SECRET", "")
    if not value:
        raise RuntimeError("SESSION_SECRET is required")
    encoded = value.encode("utf-8")
    if len(encoded) < 32:
        raise RuntimeError("SESSION_SECRET must contain at least 32 bytes")
    return encoded


def _secret_hash(value: str) -> str:
    return hmac.new(_session_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_token_for_session(session_token: str) -> str:
    return hmac.new(
        _session_secret(), f"csrf:{session_token}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def create_session(
    session: Session,
    user: User,
    *,
    ttl: timedelta = DEFAULT_SESSION_TTL,
) -> SessionSecrets:
    if ttl.total_seconds() <= 0:
        raise ValueError("Session ttl must be positive")
    session_token = secrets.token_urlsafe(48)
    csrf_token = csrf_token_for_session(session_token)
    now = datetime.utcnow()
    expires_at = now + ttl
    session.add(
        UserSession(
            user=user,
            token_hash=_secret_hash(session_token),
            csrf_hash=_secret_hash(csrf_token),
            session_version=user.session_version,
            expires_at=expires_at,
            last_used_at=now,
        )
    )
    session.flush()
    return SessionSecrets(session_token=session_token, csrf_token=csrf_token, expires_at=expires_at)


def lookup_session(session: Session, session_token: str) -> Optional[UserSession]:
    if not session_token:
        return None
    record = session.scalar(
        select(UserSession).where(UserSession.token_hash == _secret_hash(session_token))
    )
    now = datetime.utcnow()
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= now
        or record.user is None
        or not record.user.is_active
        or record.session_version != record.user.session_version
    ):
        return None
    record.last_used_at = now
    return record


def authenticate_request(request: Request, session: Session) -> User:
    record = lookup_session(session, request.cookies.get(SESSION_COOKIE_NAME, ""))
    if record is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    request.state.auth_session = record
    return record.user


def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    return authenticate_request(request, session)


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def _validate_request_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    parsed = urlsplit(origin)
    request_host = request.url.hostname
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname != request_host:
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def require_csrf(request: Request, user: User = Depends(require_user)) -> User:
    _validate_request_origin(request)
    record = getattr(request.state, "auth_session", None)
    supplied_token = request.headers.get("x-csrf-token", "")
    if record is None or not supplied_token:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    supplied_hash = _secret_hash(supplied_token)
    if not hmac.compare_digest(record.csrf_hash, supplied_hash):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return user


def authenticate_credentials(session: Session, username: str, password: str) -> Optional[User]:
    normalized = username.strip().lower()
    user = session.scalar(select(User).where(User.username == normalized))
    candidate_hash = user.password_hash if user is not None else _dummy_password_hash()
    password_valid = verify_password(candidate_hash, password)
    if user is None or not password_valid or not user.is_active:
        return None
    return user


def revoke_user_sessions(session: Session, user: User) -> None:
    now = datetime.utcnow()
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def ensure_initial_admin(session: Session) -> Optional[User]:
    if session.scalar(select(func.count(User.id))) > 0:
        return None
    required_names = ("INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD", "SESSION_SECRET")
    missing = [name for name in required_names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required bootstrap settings: {', '.join(missing)}")
    username = os.environ["INITIAL_ADMIN_USERNAME"].strip().lower()
    password = os.environ["INITIAL_ADMIN_PASSWORD"]
    if not username:
        raise RuntimeError("INITIAL_ADMIN_USERNAME must not be blank")
    if len(password) < 12:
        raise RuntimeError("INITIAL_ADMIN_PASSWORD must contain at least 12 characters")
    _session_secret()
    admin = User(
        username=username,
        display_name=username,
        password_hash=hash_password(password),
        role="ADMIN",
        is_active=True,
    )
    session.add(admin)
    session.flush()
    return admin
