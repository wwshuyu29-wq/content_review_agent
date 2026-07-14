from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Generator, Optional

from sqlalchemy import Engine, create_engine, event, inspect
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


def _raise_duplicate_groups(connection, table: str, columns: tuple[str, ...]) -> None:
    selected = ", ".join(columns)
    where = " AND ".join(f"{column} IS NOT NULL" for column in columns)
    rows = connection.exec_driver_sql(
        f"SELECT {selected}, COUNT(*) AS duplicate_count FROM {table} "
        f"WHERE {where} GROUP BY {selected} HAVING COUNT(*) > 1"
    ).all()
    if rows:
        values = [dict(zip((*columns, "duplicate_count"), row)) for row in rows]
        raise ValueError(
            f"Cannot create unique index for {table}({', '.join(columns)}); "
            f"duplicate non-null values exist: {values}. Resolve duplicates without deleting history."
        )


def ensure_schema_upgrades(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    database_inspector = inspect(engine)
    table_names = database_inspector.get_table_names()
    with engine.begin() as connection:
        if "content_items" in table_names:
            connection.exec_driver_sql(
                "UPDATE content_items SET review_status = CASE review_status "
                "WHEN 'MANUAL_REQUIRED' THEN 'HUMAN_REVIEW_REQUIRED' "
                "WHEN 'FIX_PROPOSED' THEN 'AUTO_FIX_PENDING' "
                "WHEN 'APPROVED' THEN 'PASSED' ELSE review_status END"
            )
        if "projects" in table_names:
            project_columns = {column["name"] for column in database_inspector.get_columns("projects")}
            if "code" not in project_columns:
                connection.exec_driver_sql("ALTER TABLE projects ADD COLUMN code VARCHAR(200)")
            if "content_type" not in project_columns:
                connection.exec_driver_sql("ALTER TABLE projects ADD COLUMN content_type VARCHAR(100)")
            _raise_duplicate_groups(connection, "projects", ("code",))
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_projects_code_unique ON projects (code)"
            )
        if "rule_versions" in table_names:
            rule_columns = {column["name"] for column in database_inspector.get_columns("rule_versions")}
            for column, sql_type in (
                ("business_domain", "VARCHAR(200)"),
                ("document_type", "VARCHAR(100)"),
                ("project_code", "VARCHAR(200)"),
                ("content_type", "VARCHAR(100)"),
                ("package_version", "VARCHAR(50)"),
                ("package_digest", "VARCHAR(64)"),
            ):
                if column not in rule_columns:
                    connection.exec_driver_sql(f"ALTER TABLE rule_versions ADD COLUMN {column} {sql_type}")
            _raise_duplicate_groups(connection, "rule_versions", ("project_id", "package_version"))
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_rule_versions_project_package_version "
                "ON rule_versions (project_id, package_version)"
            )
        if "agent_results" in table_names:
            agent_columns = {column["name"] for column in database_inspector.get_columns("agent_results")}
            for column, sql_type in (
                ("agent_id", "VARCHAR(100)"),
                ("agent_version", "VARCHAR(100)"),
                ("decision", "VARCHAR(50)"),
                ("summary", "TEXT"),
                ("score", "INTEGER"),
            ):
                if column not in agent_columns:
                    connection.exec_driver_sql(f"ALTER TABLE agent_results ADD COLUMN {column} {sql_type}")
            connection.exec_driver_sql(
                "UPDATE agent_results SET agent_id = 'LEGACY-' || CAST(id AS VARCHAR) "
                "WHERE agent_id IS NULL"
            )
            connection.exec_driver_sql(
                "UPDATE agent_results SET agent_version = 'legacy-v1' WHERE agent_version IS NULL"
            )
            connection.exec_driver_sql(
                "UPDATE agent_results SET decision = 'LEGACY' WHERE decision IS NULL"
            )
            connection.exec_driver_sql(
                "UPDATE agent_results SET summary = 'Legacy result; excluded from tech arbitration' "
                "WHERE summary IS NULL"
            )
            connection.exec_driver_sql(
                "UPDATE agent_results SET score = 0 WHERE score IS NULL"
            )
            _raise_duplicate_groups(connection, "agent_results", ("audit_run_id", "agent_id", "agent_version"))
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_results_audit_agent_version "
                "ON agent_results (audit_run_id, agent_id, agent_version)"
            )
        if "audit_runs" in table_names:
            audit_columns = {column["name"] for column in database_inspector.get_columns("audit_runs")}
            if "review_key" not in audit_columns:
                connection.exec_driver_sql("ALTER TABLE audit_runs ADD COLUMN review_key VARCHAR(200)")
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_audit_runs_content_rule")
            _raise_duplicate_groups(connection, "audit_runs", ("review_key",))
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_runs_review_key "
                "ON audit_runs (review_key)"
            )
        if "review_tasks" in table_names:
            task_columns = {column["name"] for column in database_inspector.get_columns("review_tasks")}
            if "task_key" not in task_columns:
                connection.exec_driver_sql("ALTER TABLE review_tasks ADD COLUMN task_key VARCHAR(300)")
            _raise_duplicate_groups(connection, "review_tasks", ("task_key",))
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_review_tasks_task_key ON review_tasks (task_key)"
            )

        if "issues" in table_names:
            issue_columns = {column["name"] for column in database_inspector.get_columns("issues")}
            if "source_reference" not in issue_columns:
                connection.exec_driver_sql("ALTER TABLE issues ADD COLUMN source_reference JSON")
                connection.exec_driver_sql("UPDATE issues SET source_reference = '[]' WHERE source_reference IS NULL")
            for column, sql_type in (
                ("evidence_start", "INTEGER"),
                ("evidence_end", "INTEGER"),
                ("evidence_asset_id", "VARCHAR(200)"),
                ("evidence_timestamp", "VARCHAR(100)"),
            ):
                if column not in issue_columns:
                    connection.exec_driver_sql(f"ALTER TABLE issues ADD COLUMN {column} {sql_type}")

        if "batches" not in table_names:
            return

        batch_columns = {column["name"] for column in database_inspector.get_columns("batches")}
        has_import_token = "import_token" in batch_columns
        has_unique_import_token = _has_unique_import_token(database_inspector)
        if not has_import_token:
            connection.exec_driver_sql("ALTER TABLE batches ADD COLUMN import_token VARCHAR(128)")
        if not has_unique_import_token:
            _raise_duplicate_groups(connection, "batches", ("import_token",))
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_batches_import_token_unique "
                "ON batches (import_token)"
            )


def _has_unique_import_token(database_inspector) -> bool:
    for constraint in database_inspector.get_unique_constraints("batches"):
        if constraint.get("column_names") == ["import_token"]:
            return True
    for index in database_inspector.get_indexes("batches"):
        if index.get("unique") and index.get("column_names") == ["import_token"]:
            return True
    return False


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
