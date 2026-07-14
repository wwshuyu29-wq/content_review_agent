from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Generator, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "review.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH}"


class Base(DeclarativeBase):
    pass


def create_db_engine(database_url: Optional[str] = None) -> Engine:
    url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    parsed_url = make_url(url)
    is_sqlite = parsed_url.get_backend_name() == "sqlite"
    if is_sqlite and parsed_url.database and parsed_url.database != ":memory:":
        Path(parsed_url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(url, connect_args=connect_args)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None
_database_url: Optional[str] = None
_resource_lock = Lock()


def get_db_engine() -> Engine:
    global _engine, _session_factory, _database_url
    configured_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    with _resource_lock:
        if _engine is None or _database_url != configured_url:
            if _engine is not None:
                _engine.dispose()
            _engine = create_db_engine(configured_url)
            _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
            _database_url = configured_url
        return _engine


def reset_db_resources() -> None:
    global _engine, _session_factory, _database_url
    with _resource_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _database_url = None


def get_session() -> Generator[Session, None, None]:
    get_db_engine()
    assert _session_factory is not None
    with _session_factory() as session:
        yield session
