from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.db import get_session
from server.models import User, UserSession


SESSION_COOKIE_NAME = "content_review_session"
DEFAULT_SESSION_TTL = timedelta(hours=12)
DEFAULT_TEAM_MODEL = "GPT 5.6 SOL"
_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
_DUMMY_PASSWORD_HASH: Optional[str] = None


@dataclass(frozen=True)
class SessionSecrets:
    session_token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class StatelessSession:
    id: None
    user: User
    csrf_hash: str
    session_version: int
    expires_at: datetime
    revoked_at: None
    last_used_at: datetime


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not normalized or len(normalized) > 100 or re.fullmatch(r"[a-z0-9._-]+", normalized) is None:
        raise ValueError("Username must use 1-100 ASCII letters, numbers, dots, underscores, or hyphens")
    return normalized


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


def _secret_stream(nonce: bytes, length: int) -> bytes:
    chunks = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        chunks.append(
            hmac.new(
                _session_secret(),
                b"oneapi-key-stream:" + nonce + counter.to_bytes(4, "big"),
                hashlib.sha256,
            ).digest()
        )
        counter += 1
    return b"".join(chunks)[:length]


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("Secret must not be empty")
    nonce = secrets.token_bytes(16)
    plaintext = value.encode("utf-8")
    stream = _secret_stream(nonce, len(plaintext))
    ciphertext = bytes(left ^ right for left, right in zip(plaintext, stream))
    signature = hmac.new(
        _session_secret(),
        b"oneapi-key:v1:" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    return ".".join(
        (
            "v1",
            _base64url_encode(nonce),
            _base64url_encode(ciphertext),
            _base64url_encode(signature),
        )
    )


def decrypt_secret(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        raise ValueError("Invalid secret envelope")
    _, encoded_nonce, encoded_ciphertext, encoded_signature = parts
    nonce = _base64url_decode(encoded_nonce)
    ciphertext = _base64url_decode(encoded_ciphertext)
    supplied_signature = _base64url_decode(encoded_signature)
    expected_signature = hmac.new(
        _session_secret(),
        b"oneapi-key:v1:" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("Invalid secret signature")
    stream = _secret_stream(nonce, len(ciphertext))
    plaintext = bytes(left ^ right for left, right in zip(ciphertext, stream))
    return plaintext.decode("utf-8")


def _base64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _base64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode((payload + padding).encode("ascii"))


def _signed_session_token(user: User, expires_at: datetime) -> str:
    payload = {
        "uid": user.id,
        "username": user.username,
        "session_version": user.session_version,
        "expires_at": int(expires_at.timestamp()),
        "nonce": secrets.token_urlsafe(16),
    }
    encoded_payload = _base64url_encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    )
    signature = _secret_hash(f"stateless-session:{encoded_payload}")
    return f"v1.{encoded_payload}.{signature}"


def _decode_signed_session_token(session_token: str) -> Optional[dict[str, object]]:
    parts = session_token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    _, encoded_payload, supplied_signature = parts
    expected_signature = _secret_hash(f"stateless-session:{encoded_payload}")
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


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
    now = datetime.utcnow()
    expires_at = now + ttl
    session_token = _signed_session_token(user, expires_at)
    csrf_token = csrf_token_for_session(session_token)
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
        return _lookup_stateless_session(session, session_token)
    return record


def _lookup_stateless_session(session: Session, session_token: str) -> Optional[StatelessSession]:
    payload = _decode_signed_session_token(session_token)
    if payload is None:
        return None
    try:
        user_id = int(payload["uid"])
        username = str(payload["username"])
        session_version = int(payload["session_version"])
        expires_at = datetime.utcfromtimestamp(int(payload["expires_at"]))
    except (KeyError, TypeError, ValueError, OSError):
        return None
    if expires_at <= datetime.utcnow():
        return None
    user = session.scalar(select(User).where(User.id == user_id, User.username == username))
    if (
        user is None
        or not user.is_active
        or user.session_version != session_version
    ):
        return None
    return StatelessSession(
        id=None,
        user=user,
        csrf_hash=_secret_hash(csrf_token_for_session(session_token)),
        session_version=session_version,
        expires_at=expires_at,
        revoked_at=None,
        last_used_at=datetime.utcnow(),
    )


def authenticate_request(request: Request, session: Session) -> User:
    existing_user = getattr(request.state, "auth_user", None)
    if existing_user is not None:
        return existing_user
    record = lookup_session(session, request.cookies.get(SESSION_COOKIE_NAME, ""))
    if record is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = record.user
    if isinstance(record, UserSession):
        now = datetime.utcnow()
        session.execute(
            update(UserSession).where(UserSession.id == record.id).values(last_used_at=now)
        )
        session.commit()
        session.refresh(record)
        session.refresh(user)
    request.state.auth_session = record
    request.state.auth_user = user
    return user


def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    return authenticate_request(request, session)


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def _canonical_origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Origin must contain only scheme, host, and optional port")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Origin port is invalid") from error
    scheme = parsed.scheme.lower()
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def trusted_public_origins() -> set[tuple[str, str, int]]:
    configured = os.environ.get("TRUSTED_PUBLIC_ORIGINS", "")
    if configured.strip():
        values = [value.strip() for value in configured.split(",") if value.strip()]
    elif os.environ.get("ENVIRONMENT", "").strip().lower() in {"production", "prod"}:
        values = []
    else:
        values = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
        if os.environ.get("ENVIRONMENT", "").strip().lower() == "test":
            values.append("http://testserver")
    try:
        return {_canonical_origin(value) for value in values}
    except ValueError as error:
        raise RuntimeError("TRUSTED_PUBLIC_ORIGINS contains an invalid origin") from error


def trusted_public_origin_values() -> list[str]:
    values = []
    for scheme, host, port in sorted(trusted_public_origins()):
        default_port = 443 if scheme == "https" else 80
        values.append(f"{scheme}://{host}" + (f":{port}" if port != default_port else ""))
    return values


def _validate_request_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    try:
        canonical = _canonical_origin(origin or "")
    except ValueError:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    if canonical not in trusted_public_origins():
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def validate_csrf_request(request: Request) -> None:
    _validate_request_origin(request)
    record = getattr(request.state, "auth_session", None)
    supplied_token = request.headers.get("x-csrf-token", "")
    if record is None or not supplied_token:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    supplied_hash = _secret_hash(supplied_token)
    if not hmac.compare_digest(record.csrf_hash, supplied_hash):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def require_csrf(request: Request, user: User = Depends(require_user)) -> User:
    validate_csrf_request(request)
    return user


def authenticate_credentials(session: Session, username: str, password: str) -> Optional[User]:
    try:
        normalized = normalize_username(username)
    except ValueError:
        normalized = "invalid-login-username"
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


def set_user_active(session: Session, user_id: int, is_active: bool) -> Optional[User]:
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    user = session.get(User, user_id)
    if user is None:
        return None
    if user.is_active == is_active:
        return user
    if not is_active and user.role == "ADMIN":
        query = select(User.id).where(User.role == "ADMIN", User.is_active.is_(True))
        if dialect == "postgresql":
            query = query.with_for_update()
        active_admin_ids = list(session.scalars(query))
        if len(active_admin_ids) == 1 and active_admin_ids[0] == user.id:
            raise ValueError("Cannot disable the last active administrator")
    user.is_active = is_active
    user.session_version += 1
    revoke_user_sessions(session, user)
    return user


def _acquire_initial_admin_lock(session: Session) -> None:
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    elif dialect == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 1869771365})


def _verified_initial_admin(session: Session, username: str) -> User:
    existing = session.scalar(select(User).where(User.username == username))
    if existing is None or existing.role != "ADMIN" or not existing.is_active:
        raise RuntimeError("Concurrent initial administrator bootstrap did not create an active ADMIN")
    return existing


def ensure_initial_admin(session: Session) -> Optional[User]:
    _acquire_initial_admin_lock(session)
    user_count = session.scalar(select(func.count(User.id)))
    required_names = ("INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD", "SESSION_SECRET")
    bootstrap_names = ("INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD")
    configured_bootstrap_names = [name for name in bootstrap_names if os.environ.get(name)]
    if user_count > 0 and not configured_bootstrap_names:
        return None
    missing = [name for name in required_names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required bootstrap settings: {', '.join(missing)}")
    try:
        username = normalize_username(os.environ["INITIAL_ADMIN_USERNAME"])
    except ValueError as error:
        raise RuntimeError("INITIAL_ADMIN_USERNAME is invalid") from error
    password = os.environ["INITIAL_ADMIN_PASSWORD"]
    if len(password) < 12:
        raise RuntimeError("INITIAL_ADMIN_PASSWORD must contain at least 12 characters")
    _session_secret()
    existing = session.scalar(select(User).where(User.username == username))
    if existing is not None:
        changed = False
        if existing.role != "ADMIN":
            existing.role = "ADMIN"
            changed = True
        if not existing.is_active:
            existing.is_active = True
            changed = True
        if not verify_password(existing.password_hash, password):
            existing.password_hash = hash_password(password)
            changed = True
        if changed:
            existing.session_version += 1
            revoke_user_sessions(session, existing)
            session.flush()
        return existing
    admin = User(
        username=username,
        display_name=username,
        password_hash=hash_password(password),
        role="ADMIN",
        is_active=True,
    )
    session.add(admin)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return _verified_initial_admin(session, username)
    return admin


def _configured_team_usernames() -> list[str]:
    raw = os.environ.get("TEAM_USERNAMES", "")
    usernames: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        if not item.strip():
            continue
        username = normalize_username(item)
        if username not in seen:
            usernames.append(username)
            seen.add(username)
    return usernames


def ensure_team_users(session: Session) -> list[User]:
    usernames = _configured_team_usernames()
    if not usernames:
        return []
    password = os.environ.get("TEAM_USER_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("TEAM_USER_PASSWORD must contain at least 12 characters")
    _session_secret()
    model = os.environ.get("TEAM_USER_MODEL", DEFAULT_TEAM_MODEL).strip() or DEFAULT_TEAM_MODEL
    users: list[User] = []
    for username in usernames:
        existing = session.scalar(select(User).where(User.username == username))
        if existing is None:
            existing = User(
                username=username,
                display_name=username,
                password_hash=hash_password(password),
                role="REVIEWER",
                is_active=True,
                oneapi_model=model,
            )
            session.add(existing)
            session.flush()
            users.append(existing)
            continue
        changed = False
        if existing.role not in {"ADMIN", "REVIEWER"}:
            existing.role = "REVIEWER"
            changed = True
        if not existing.is_active:
            existing.is_active = True
            changed = True
        if not verify_password(existing.password_hash, password):
            existing.password_hash = hash_password(password)
            changed = True
        if not existing.oneapi_model:
            existing.oneapi_model = model
            changed = True
        if changed:
            existing.session_version += 1
            revoke_user_sessions(session, existing)
            session.flush()
        users.append(existing)
    return users
