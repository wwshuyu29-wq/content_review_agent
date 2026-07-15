from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import main
from server.db import Base, create_db_engine, reset_db_resources


@pytest.fixture
def auth_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = f"sqlite:///{tmp_path / 'auth.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "correct horse battery staple")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-with-at-least-32-bytes")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    reset_db_resources()
    with TestClient(main.app) as client:
        yield client, create_db_engine(database_url)
    main.app.dependency_overrides.clear()
    reset_db_resources()


def login(client: TestClient, username: str = "admin", password: str = "correct horse battery staple"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def csrf_headers(response) -> dict[str, str]:
    return {"X-CSRF-Token": response.json()["csrf_token"], "Origin": "http://testserver"}


def test_passwords_use_argon2id_and_invalid_password_is_rejected() -> None:
    from server.services.auth_service import hash_password, verify_password

    password_hash = hash_password("a long password")

    assert password_hash.startswith("$argon2id$")
    assert "a long password" not in password_hash
    assert verify_password(password_hash, "a long password") is True
    assert verify_password(password_hash, "wrong password") is False


def test_short_session_secret_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from server.models import User
    from server.services.auth_service import create_session, hash_password

    monkeypatch.setenv("SESSION_SECRET", "too-short")
    engine = create_db_engine(f"sqlite:///{tmp_path / 'short-secret.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="reviewer", display_name="Reviewer", password_hash=hash_password("password value"))
        session.add(user)
        session.flush()
        with pytest.raises(RuntimeError, match="at least 32 bytes"):
            create_session(session, user, ttl=timedelta(hours=1))


def test_session_storage_hashes_tokens_and_expiry_and_revocation_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.models import User, UserSession
    from server.services.auth_service import create_session, hash_password, lookup_session

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-with-at-least-32-bytes")
    engine = create_db_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="reviewer", display_name="Reviewer", password_hash=hash_password("password value"))
        session.add(user)
        session.flush()
        secrets = create_session(session, user, ttl=timedelta(hours=1))
        session.commit()

        stored = session.scalar(select(UserSession))
        assert stored is not None
        assert secrets.session_token not in (stored.token_hash, stored.csrf_hash)
        assert secrets.csrf_token not in (stored.token_hash, stored.csrf_hash)
        assert len(stored.token_hash) == len(hashlib.sha256().hexdigest())
        assert lookup_session(session, secrets.session_token) is stored

        stored.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.flush()
        assert lookup_session(session, secrets.session_token) is None

        stored.expires_at = datetime.utcnow() + timedelta(hours=1)
        stored.revoked_at = datetime.utcnow()
        session.flush()
        assert lookup_session(session, secrets.session_token) is None


def test_initial_admin_is_created_once_without_changing_existing_review_data(
    auth_api,
) -> None:
    from server.models import Project, User
    from server.services.auth_service import verify_password

    client, engine = auth_api
    assert client.get("/api/health").status_code == 200
    with Session(engine) as session:
        users = list(session.scalars(select(User)))
        projects = list(session.scalars(select(Project)))
        assert len(users) == 1
        assert users[0].username == "admin"
        assert users[0].role == "ADMIN"
        assert verify_password(users[0].password_hash, "correct horse battery staple")
        assert projects and projects[0].current_rule_version.package_version == "1.0"

    with TestClient(main.app) as second_client:
        assert second_client.get("/api/health").status_code == 200
    with Session(engine) as session:
        assert len(list(session.scalars(select(User)))) == 1


def test_missing_bootstrap_secrets_fail_only_when_no_users_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.models import User
    from server.services.auth_service import ensure_initial_admin, hash_password

    engine = create_db_engine(f"sqlite:///{tmp_path / 'bootstrap.db'}")
    Base.metadata.create_all(engine)
    for name in ("INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD", "SESSION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="INITIAL_ADMIN_USERNAME.*INITIAL_ADMIN_PASSWORD.*SESSION_SECRET"):
            ensure_initial_admin(session)
        session.add(User(username="existing", display_name="Existing", password_hash=hash_password("existing password")))
        session.commit()
        ensure_initial_admin(session)


def test_login_sets_secure_aware_http_only_same_site_cookie_and_me_works(auth_api) -> None:
    client, _ = auth_api

    response = login(client)

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "admin"
    assert response.json()["csrf_token"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "ADMIN"
    assert me.json()["csrf_token"] == response.json()["csrf_token"]


def test_production_cookie_uses_secure_attribute(
    auth_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = auth_api
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    response = login(client)

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_invalid_password_and_disabled_user_share_generic_login_error(auth_api) -> None:
    from server.models import User

    client, engine = auth_api
    invalid = login(client, password="wrong password")
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.username == "admin"))
        user.is_active = False
        session.commit()
    disabled = login(client)

    assert invalid.status_code == disabled.status_code == 401
    assert invalid.json() == disabled.json() == {"detail": "Invalid username or password"}


def test_logout_requires_csrf_and_revokes_session(auth_api) -> None:
    client, _ = auth_api
    authenticated = login(client)

    rejected = client.post("/api/auth/logout")
    accepted = client.post("/api/auth/logout", headers=csrf_headers(authenticated))

    assert rejected.status_code == 403
    assert accepted.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_no_public_registration_and_admin_only_user_creation(auth_api) -> None:
    client, _ = auth_api
    assert client.post("/api/auth/register", json={"username": "public", "password": "password value"}).status_code == 404

    admin_login = login(client)
    created = client.post(
        "/api/admin/users",
        headers=csrf_headers(admin_login),
        json={
            "username": "reviewer",
            "display_name": "Review User",
            "password": "reviewer password value",
            "role": "REVIEWER",
        },
    )
    assert created.status_code == 201
    assert "password" not in created.text.lower()

    client.post("/api/auth/logout", headers=csrf_headers(admin_login))
    reviewer_login = login(client, "reviewer", "reviewer password value")
    forbidden = client.post(
        "/api/admin/users",
        headers=csrf_headers(reviewer_login),
        json={"username": "other", "display_name": "Other", "password": "other password value"},
    )
    assert forbidden.status_code == 403


def test_admin_can_list_disable_and_reset_users_and_sessions_are_invalidated(auth_api) -> None:
    client, _ = auth_api
    admin_login = login(client)
    created = client.post(
        "/api/admin/users",
        headers=csrf_headers(admin_login),
        json={"username": "reviewer", "display_name": "Reviewer", "password": "old password value"},
    ).json()
    client.post("/api/auth/logout", headers=csrf_headers(admin_login))

    reviewer_login = login(client, "reviewer", "old password value")
    assert reviewer_login.status_code == 200
    reviewer_csrf = csrf_headers(reviewer_login)
    client.cookies.clear()
    admin_login = login(client)
    headers = csrf_headers(admin_login)

    listed = client.get("/api/admin/users")
    assert listed.status_code == 200
    assert {user["username"] for user in listed.json()} == {"admin", "reviewer"}
    reset = client.post(
        f"/api/admin/users/{created['id']}/reset-password",
        headers=headers,
        json={"password": "new password value"},
    )
    assert reset.status_code == 204

    client.cookies.clear()
    assert login(client, "reviewer", "old password value").status_code == 401
    assert login(client, "reviewer", "new password value").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout", headers=reviewer_csrf).status_code in {401, 403}

    client.cookies.clear()
    admin_login = login(client)
    disabled = client.patch(
        f"/api/admin/users/{created['id']}",
        headers=csrf_headers(admin_login),
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    client.cookies.clear()
    assert login(client, "reviewer", "new password value").status_code == 401


def test_start_script_validates_session_secret_without_printing_values(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "start_local.sh"
    environment = {
        **os.environ,
        "PYTHON_BIN": "definitely-not-a-python-command",
        "SESSION_SECRET": "",
        "INITIAL_ADMIN_USERNAME": "secret-admin-name",
        "INITIAL_ADMIN_PASSWORD": "secret-admin-password",
    }

    completed = subprocess.run(
        [str(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "SESSION_SECRET is required" in output
    assert "secret-admin-name" not in output
    assert "secret-admin-password" not in output


def test_start_script_rejects_short_session_secret(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "start_local.sh"
    completed = subprocess.run(
        [str(script)],
        cwd=tmp_path,
        env={**os.environ, "PYTHON_BIN": "definitely-not-a-python-command", "SESSION_SECRET": "short"},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert "SESSION_SECRET must contain at least 32" in completed.stderr


def test_start_script_rejects_short_bootstrap_password_without_printing_it(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "start_local.sh"
    completed = subprocess.run(
        [str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHON_BIN": "definitely-not-a-python-command",
            "SESSION_SECRET": "test-session-secret-with-at-least-32-bytes",
            "INITIAL_ADMIN_USERNAME": "admin",
            "INITIAL_ADMIN_PASSWORD": "short",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "INITIAL_ADMIN_PASSWORD must contain at least 12 characters" in output
    assert "short" not in output


def test_csrf_rejects_wrong_token_and_untrusted_origin(auth_api) -> None:
    client, _ = auth_api
    authenticated = login(client)
    token = authenticated.json()["csrf_token"]

    wrong_token = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": "wrong", "Origin": "http://testserver"},
    )
    bad_origin = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
    )

    assert wrong_token.status_code == 403
    assert bad_origin.status_code == 403
