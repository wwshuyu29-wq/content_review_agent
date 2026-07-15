from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import Session

from server.db import Base, create_db_engine, get_session
from server.models import (
    AgentResult,
    AuditRun,
    Batch,
    ContentItem,
    ContentVersion,
    FormatStatus,
    HumanDecision,
    Issue,
    Project,
    PublishStatus,
    ReviewStatus,
    ReviewTask,
    RuleVersion,
)
from server.schemas import ContentItemCreate, ContentItemRead, IssueCreate
from server.seed import seed_default_project


def make_sqlite_engine(tmp_path: Path) -> Engine:
    return create_db_engine(f"sqlite:///{tmp_path / 'review.db'}")


def create_legacy_schema_without_import_token(engine: Engine) -> None:
    for table in Base.metadata.sorted_tables:
        if table.name != "batches":
            table.create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE batches (
                id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                supplier_id VARCHAR(200) NOT NULL,
                name VARCHAR(200) NOT NULL,
                status VARCHAR(50) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(project_id) REFERENCES projects (id)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX ix_batches_project_id ON batches (project_id)")


def test_issue_source_reference_round_trips_and_schema_serializes(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(name="source-ref-project", code="source-ref", content_type="TECH_MEDIA_REVIEW")
        batch = Batch(project=project, supplier_id="supplier", name="batch")
        item = ContentItem(project=project, batch=batch, external_id="content", title="标题")
        version = ContentVersion(content_item=item, version=1, source="SUPPLIER", title="标题", body="正文")
        rule_version = RuleVersion(project=project, version=1, package_version="0.9", prompt_version="test")
        audit = AuditRun(content_item=item, content_version=version, rule_version=rule_version, model="test", prompt_version="test")
        issue = Issue(
            audit_run=audit,
            rule_id="RULE-1",
            category="deterministic",
            severity="HIGH",
            field="body",
            evidence_quote="证据",
            evidence_start=4,
            evidence_end=6,
            evidence_asset_id="asset-1",
            evidence_timestamp="00:02",
            source_reference=["CLAIM-001"],
            reason="原因",
            suggestion="建议",
            confidence=1.0,
        )
        session.add(issue)
        session.commit()
        session.expire_all()
        saved = session.get(Issue, issue.id)
    assert saved.source_reference == ["CLAIM-001"]
    assert (saved.evidence_start, saved.evidence_end, saved.evidence_asset_id, saved.evidence_timestamp) == (
        4, 6, "asset-1", "00:02"
    )
    assert IssueCreate(
            audit_run_id=saved.audit_run_id,
            rule_id=saved.rule_id,
            category=saved.category,
            severity=saved.severity,
            field=saved.field,
            evidence_quote=saved.evidence_quote,
            evidence_start=saved.evidence_start,
            evidence_end=saved.evidence_end,
            evidence_asset_id=saved.evidence_asset_id,
            evidence_timestamp=saved.evidence_timestamp,
            reason=saved.reason,
            suggestion=saved.suggestion,
            auto_fixable=saved.auto_fixable,
            human_required=saved.human_required,
            confidence=saved.confidence,
            source_reference=saved.source_reference,
        ).source_reference == ["CLAIM-001"]


def test_database_url_builds_sqlite_engine_with_foreign_keys(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)

    assert engine.url.drivername == "sqlite"
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_schema_upgrade_adds_project_standard_identity_columns(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, description TEXT, "
            "current_rule_version_id INTEGER, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )

    import server.db as db_module

    db_module.ensure_schema_upgrades(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("projects")}
    assert {"code", "content_type"} <= columns
    assert any(index.get("unique") and index["column_names"] == ["code"] for index in inspect(engine).get_indexes("projects"))


def test_sqlite_engine_creates_missing_database_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "review.db"

    engine = create_db_engine(f"sqlite:///{database_path}")
    with engine.connect():
        pass

    assert database_path.exists()


def test_create_all_defines_every_workflow_table(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) == {
        "agent_results",
        "assets",
        "audit_runs",
        "batches",
        "content_items",
        "content_versions",
        "human_decisions",
        "issues",
        "projects",
        "review_tasks",
        "review_task_issues",
        "rule_versions",
        "test_cases",
        "test_evidence",
    }


def test_schema_upgrade_adds_import_token_to_legacy_batches_and_confirm_import_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.db as db_module
    from openpyxl import Workbook
    from server.services import excel_import_service
    from server.services.excel_import_service import IMPORT_COLUMNS, confirm_import, preview_import

    engine = make_sqlite_engine(tmp_path)
    create_legacy_schema_without_import_token(engine)
    legacy_columns = {column["name"] for column in inspect(engine).get_columns("batches")}
    assert "import_token" not in legacy_columns

    db_module.ensure_schema_upgrades(engine)

    upgraded = inspect(engine)
    upgraded_columns = {column["name"] for column in upgraded.get_columns("batches")}
    unique_indexes = {
        tuple(index["column_names"])
        for index in upgraded.get_indexes("batches")
        if index.get("unique")
    }
    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in upgraded.get_unique_constraints("batches")
    }
    assert "import_token" in upgraded_columns
    assert ("import_token",) in unique_indexes | unique_constraints

    monkeypatch.setenv("CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY", str(tmp_path / "preview-roots.json"))
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path / "data"))
    excel_import_service._preview_locations.clear()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "内容清单"
    worksheet.append(list(IMPORT_COLUMNS))
    worksheet.append(["legacy-row", "活动主题", "小红书", "标题", "正文", None, "2026-08-01", "备注"])
    xlsx = tmp_path / "legacy-import.xlsx"
    workbook.save(xlsx)
    preview = preview_import(xlsx, None, tmp_path / "previews")

    with Session(engine) as session:
        project = seed_default_project(session)
        batch = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="legacy-supplier",
            batch_name="升级后导入",
        )

        assert batch.import_token == preview.token
        assert batch.content_items[0].external_id == "legacy-row"
        assert session.scalar(select(Batch).where(Batch.import_token == preview.token)) is batch


def test_legacy_duplicate_audits_survive_review_key_upgrade(tmp_path: Path) -> None:
    import server.db as db_module

    engine = make_sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE audit_runs (
                id INTEGER PRIMARY KEY,
                content_item_id INTEGER NOT NULL,
                content_version_id INTEGER NOT NULL,
                rule_version_id INTEGER NOT NULL,
                model VARCHAR(200) NOT NULL,
                prompt_version VARCHAR(100) NOT NULL,
                status VARCHAR(50) NOT NULL,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO audit_runs VALUES "
            "(1, 10, 20, 30, 'legacy', 'v1', 'COMPLETED', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),"
            "(2, 10, 20, 30, 'legacy', 'v1', 'COMPLETED', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

    db_module.ensure_schema_upgrades(engine)

    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "SELECT id, review_key FROM audit_runs ORDER BY id"
        ).all()
        assert rows == [(1, None), (2, None)]
        connection.exec_driver_sql(
            "INSERT INTO audit_runs "
            "(id, content_item_id, content_version_id, rule_version_id, model, prompt_version, status, created_at, updated_at, review_key) "
            "VALUES (3, 10, 21, 31, 'tech', 'v1', 'COMPLETED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'v1:21:31')"
        )
        with pytest.raises(Exception):
            connection.exec_driver_sql(
                "INSERT INTO audit_runs "
                "(id, content_item_id, content_version_id, rule_version_id, model, prompt_version, status, created_at, updated_at, review_key) "
                "VALUES (4, 10, 22, 32, 'tech', 'v1', 'COMPLETED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'v1:21:31')"
            )


def test_schema_upgrade_reports_duplicate_audit_review_keys(tmp_path: Path) -> None:
    import server.db as db_module

    engine = make_sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE audit_runs (
                id INTEGER PRIMARY KEY,
                content_item_id INTEGER NOT NULL,
                content_version_id INTEGER NOT NULL,
                rule_version_id INTEGER NOT NULL,
                model VARCHAR(200) NOT NULL,
                prompt_version VARCHAR(100) NOT NULL,
                status VARCHAR(50) NOT NULL,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                review_key VARCHAR(200)
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO audit_runs VALUES "
            "(1, 10, 20, 30, ?, ?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?),"
            "(2, 10, 21, 30, ?, ?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)",
            ("legacy", "v1", "COMPLETED", "duplicate-review-key") * 2,
        )

    with pytest.raises(
        ValueError,
        match=r"audit_runs.*review_key.*duplicate-review-key",
    ):
        db_module.ensure_schema_upgrades(engine)


def test_schema_upgrade_adds_audit_and_agent_idempotency_indexes(tmp_path: Path) -> None:
    import server.db as db_module

    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    db_module.ensure_schema_upgrades(engine)
    inspector = inspect(engine)

    indexes = {
        (table_name, tuple(index["column_names"]), bool(index.get("unique")))
        for table_name in ("audit_runs", "agent_results")
        for index in inspector.get_indexes(table_name)
    }
    assert ("audit_runs", ("review_key",), True) in indexes
    assert ("agent_results", ("audit_run_id", "agent_id", "agent_version"), True) in indexes


def test_metadata_compiles_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect))


def test_workflow_entities_persist_with_separate_content_statuses(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(name="测试项目")
        rule_version = RuleVersion(
            project=project,
            version=1,
            dimension_standards={"brand": "品牌标准"},
            project_facts={"partner": "测试伙伴"},
            structured_rules={"deny_words": []},
            prompt_version="prompt-v1",
        )
        project.current_rule_version = rule_version
        batch = Batch(project=project, supplier_id="supplier-1", name="首批")
        item = ContentItem(
            project=project,
            batch=batch,
            external_id="content-1",
            title="标题",
            format_status=FormatStatus.PASSED,
            review_status=ReviewStatus.AI_REVIEWING,
            publish_status=PublishStatus.NOT_READY,
        )
        version = ContentVersion(
            content_item=item,
            version=1,
            source="SUPPLIER",
            title="标题",
            body="正文",
            payload={"platform": "小红书"},
        )
        audit = AuditRun(
            content_item=item,
            content_version=version,
            rule_version=rule_version,
            model="test-model",
            prompt_version="prompt-v1",
            status="COMPLETED",
        )
        result = AgentResult(
            audit_run=audit,
            agent_name="brand",
            status="COMPLETED",
            raw_result={"issues": 1},
        )
        issue = Issue(
            audit_run=audit,
            agent_result=result,
            rule_id="BRAND-001",
            category="brand",
            severity="high",
            field="body",
            evidence_quote="代言人",
            reason="合作身份错误",
            suggestion="改为短期合作伙伴",
            auto_fixable=False,
            human_required=True,
            confidence=0.99,
        )
        task = ReviewTask(
            content_item=item,
            issue=issue,
            target_content_version=version,
            audit_run=audit,
            task_type="RISK_REVIEW",
            status="OPEN",
        )
        decision = HumanDecision(
            review_task=task,
            decision="REJECT",
            reviewer="reviewer@example.com",
            note="合作身份需修正",
        )
        session.add(decision)
        session.commit()
        item_id = item.id

    with Session(engine) as session:
        saved = session.get(ContentItem, item_id)
        assert saved is not None
        assert saved.format_status is FormatStatus.PASSED
        assert saved.review_status is ReviewStatus.AI_REVIEWING
        assert saved.publish_status is PublishStatus.NOT_READY
        assert saved.versions[0].audit_runs[0].issues[0].review_task.human_decisions[0].decision == "REJECT"


def test_seed_default_project_is_idempotent_and_uses_tech_review_package(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        first = seed_default_project(session)
        second = seed_default_project(session)
        session.commit()

        assert first.id == second.id
        assert first.code == "bdmap_xdxx_tech_review_2026"
        assert first.content_type == "TECH_MEDIA_REVIEW"
        assert len(first.rule_versions) == 1
        rules = first.current_rule_version
        assert rules is not None
        assert rules.version == 1
        assert rules.package_version == "0.9"
        assert rules.project_code == "bdmap_xdxx_tech_review_2026"
        assert rules.dimension_standards["metadata"]["content_type"] == "TECH_MEDIA_REVIEW"
        serialized = str({"facts": rules.project_facts, "rules": rules.structured_rules})
        assert "范丞丞" not in serialized
        assert "代言" not in serialized
        assert "deny_words" not in serialized
        assert "must_human_keywords" not in serialized


def test_seed_repairs_stale_current_rule_version_pointer(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = seed_default_project(session)
        stale = RuleVersion(
            project=project,
            version=2,
            package_version="0.8",
            business_domain="baidu_maps_marketing_review",
            document_type="project_standard",
            project_code=project.code,
            content_type=project.content_type,
            package_digest="stale",
            dimension_standards={},
            project_facts={},
            structured_rules={},
            prompt_version="stale",
        )
        session.add(stale)
        session.flush()
        project.current_rule_version = stale
        session.flush()

        repaired = seed_default_project(session)

        assert repaired.current_rule_version.package_version == "0.9"
        assert repaired.current_rule_version.package_digest != "stale"


def test_seed_rejects_same_version_snapshot_with_tampered_digest(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(
            name="百度地图小度想想科技媒体测评",
            code="bdmap_xdxx_tech_review_2026",
            content_type="TECH_MEDIA_REVIEW",
        )
        tampered = RuleVersion(
            project=project,
            version=1,
            package_version="0.9",
            package_digest="tampered",
            business_domain="baidu_maps_marketing_review",
            document_type="project_standard",
            project_code=project.code,
            content_type=project.content_type,
            dimension_standards={},
            project_facts={},
            structured_rules={},
            prompt_version="tampered",
        )
        project.current_rule_version = tampered
        session.add(project)
        session.flush()

        with pytest.raises(ValueError, match="digest mismatch"):
            seed_default_project(session)


def test_get_session_uses_configured_database_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'configured.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    session_iterator = get_session()
    session = next(session_iterator)
    try:
        assert str(session.bind.url) == database_url
    finally:
        session_iterator.close()


def test_get_session_reuses_process_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import server.db as db

    database_url = f"sqlite:///{tmp_path / 'shared.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    db.reset_db_resources()
    first_iterator = db.get_session()
    second_iterator = db.get_session()
    first = next(first_iterator)
    second = next(second_iterator)
    try:
        assert first.bind is second.bind
        assert str(first.bind.url) == database_url
    finally:
        first_iterator.close()
        second_iterator.close()
        db.reset_db_resources()


def test_rule_and_content_versions_are_immutable(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = seed_default_project(session)
        batch = Batch(project=project, supplier_id="immutable", name="immutable")
        item = ContentItem(project=project, batch=batch, external_id="immutable", title="title")
        version = ContentVersion(content_item=item, version=1, source="SUPPLIER", title="title", body="body")
        session.add(batch)
        session.commit()

        version.body = "changed"
        with pytest.raises(InvalidRequestError, match="immutable"):
            session.commit()
        session.rollback()

        session.delete(project.current_rule_version)
        with pytest.raises(InvalidRequestError, match="immutable"):
            session.commit()


def test_foreign_keys_used_by_workflow_queries_are_indexed(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    database_inspector = inspect(engine)

    expected_indexed_foreign_keys = {
        "projects": {"current_rule_version_id"},
        "rule_versions": {"project_id"},
        "batches": {"project_id"},
        "content_items": {"project_id", "batch_id"},
        "content_versions": {"content_item_id"},
        "audit_runs": {"content_item_id", "content_version_id", "rule_version_id"},
        "agent_results": {"audit_run_id"},
        "issues": {"audit_run_id", "agent_result_id"},
        "review_tasks": {"content_item_id", "target_content_version_id", "audit_run_id", "issue_id"},
        "human_decisions": {"review_task_id"},
        "assets": {"content_item_id"},
        "test_cases": {"content_item_id", "content_version_id"},
        "test_evidence": {"test_case_id", "asset_id"},
    }

    for table_name, expected_columns in expected_indexed_foreign_keys.items():
        indexed_columns = {
            column
            for index in database_inspector.get_indexes(table_name)
            for column in index["column_names"]
        }
        indexed_columns.update(
            constraint["column_names"][0]
            for constraint in database_inspector.get_unique_constraints(table_name)
            if constraint["column_names"]
        )
        assert expected_columns <= indexed_columns, table_name


def test_workflow_identity_constraints_are_unique(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    database_inspector = inspect(engine)

    expected_unique_constraints = {
        "projects": {("name",)},
        "rule_versions": {("project_id", "version")},
        "batches": {("import_token",)},
        "content_items": {("batch_id", "external_id")},
        "content_versions": {("content_item_id", "version")},
        "agent_results": {
            ("audit_run_id", "agent_name"),
            ("audit_run_id", "agent_id", "agent_version"),
        },
        "review_tasks": {("issue_id",)},
        "assets": {("content_item_id", "asset_id"), ("content_item_id", "external_id")},
        "test_cases": {("content_item_id", "external_test_case_id")},
        "test_evidence": {("test_case_id", "asset_id")},
    }

    for table_name, expected_constraints in expected_unique_constraints.items():
        actual_constraints = {
            tuple(constraint["column_names"])
            for constraint in database_inspector.get_unique_constraints(table_name)
        }
        assert expected_constraints <= actual_constraints, table_name


def test_content_status_enums_store_values_and_serialize_from_orm(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        project = Project(name="枚举测试项目")
        batch = Batch(project=project, supplier_id="supplier-enum", name="枚举批次")
        item = ContentItem(
            project=project,
            batch=batch,
            external_id="enum-content",
            title="枚举标题",
            format_status=FormatStatus.INCOMPLETE,
            review_status=ReviewStatus.HUMAN_REVIEW_REQUIRED,
            publish_status=PublishStatus.NOT_READY,
        )
        session.add(item)
        session.commit()
        schema = ContentItemRead.model_validate(item)

        assert schema.model_dump(mode="json")["format_status"] == "INCOMPLETE"
        assert schema.model_dump(mode="json")["review_status"] == "HUMAN_REVIEW_REQUIRED"
        assert schema.model_dump(mode="json")["publish_status"] == "NOT_READY"

    with engine.connect() as connection:
        stored = connection.exec_driver_sql(
            "SELECT format_status, review_status, publish_status FROM content_items"
        ).one()
        assert stored == ("INCOMPLETE", "HUMAN_REVIEW_REQUIRED", "NOT_READY")


def test_schemas_validate_statuses_and_issue_confidence() -> None:
    content = ContentItemCreate(
        project_id=1,
        batch_id=2,
        external_id="content-1",
        title="标题",
        format_status=FormatStatus.PASSED,
        review_status=ReviewStatus.NOT_STARTED,
        publish_status=PublishStatus.NOT_READY,
    )
    assert content.format_status is FormatStatus.PASSED

    with pytest.raises(ValidationError):
        IssueCreate(
            audit_run_id=1,
            rule_id="RULE-1",
            category="brand",
            severity="high",
            field="body",
            evidence_quote="证据",
            reason="原因",
            suggestion="建议",
            auto_fixable=False,
            human_required=True,
            confidence=1.1,
        )


def test_schema_upgrade_maps_legacy_review_statuses_and_preserves_rejected(tmp_path: Path) -> None:
    import server.db as db_module

    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(name="legacy-status")
        batch = Batch(project=project, supplier_id="supplier", name="batch")
        for index, status in enumerate(("MANUAL_REQUIRED", "FIX_PROPOSED", "APPROVED", "REJECTED")):
            batch.content_items.append(ContentItem(
                project=project, external_id=str(index), title=status,
                review_status=ReviewStatus.NOT_STARTED,
            ))
        session.add(batch)
        session.commit()
    with engine.begin() as connection:
        for index, status in enumerate(("MANUAL_REQUIRED", "FIX_PROPOSED", "APPROVED", "REJECTED")):
            connection.exec_driver_sql(
                "UPDATE content_items SET review_status = ? WHERE external_id = ?", (status, str(index))
            )

    db_module.ensure_schema_upgrades(engine)

    with engine.connect() as connection:
        values = connection.exec_driver_sql(
            "SELECT review_status FROM content_items ORDER BY external_id"
        ).scalars().all()
    assert values == ["HUMAN_REVIEW_REQUIRED", "AUTO_FIX_PENDING", "PASSED", "REJECTED"]


def test_schema_upgrade_reports_duplicate_project_codes_before_unique_index(tmp_path: Path) -> None:
    import server.db as db_module
    engine = make_sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, code VARCHAR(200), "
            "content_type VARCHAR(100), description TEXT, current_rule_version_id INTEGER, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO projects (id,name,code,created_at,updated_at) VALUES "
            "(1,'one','duplicate',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
            "(2,'two','duplicate',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    with pytest.raises(ValueError, match=r"projects.*code.*duplicate"):
        db_module.ensure_schema_upgrades(engine)


def test_schema_upgrade_reports_duplicate_rule_package_versions(tmp_path: Path) -> None:
    import server.db as db_module
    engine = make_sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE rule_versions (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, version INTEGER NOT NULL, "
            "package_version VARCHAR(50), prompt_version VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO rule_versions (id,project_id,version,package_version,prompt_version,created_at,updated_at) VALUES "
            "(1,1,1,'0.9','p',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
            "(2,1,2,'0.9','p',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    with pytest.raises(ValueError, match=r"rule_versions.*project_id.*package_version"):
        db_module.ensure_schema_upgrades(engine)


def test_schema_upgrade_reports_duplicate_agent_identity(tmp_path: Path) -> None:
    import server.db as db_module
    engine = make_sqlite_engine(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE agent_results (id INTEGER PRIMARY KEY, audit_run_id INTEGER NOT NULL, agent_name VARCHAR(100) NOT NULL, "
            "agent_id VARCHAR(100), agent_version VARCHAR(100), status VARCHAR(50) NOT NULL, raw_result JSON NOT NULL, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO agent_results (id,audit_run_id,agent_name,agent_id,agent_version,status,raw_result,created_at,updated_at) VALUES "
            "(1,1,'a','A','v1','DONE','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
            "(2,1,'b','A','v1','DONE','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    with pytest.raises(ValueError, match=r"agent_results.*audit_run_id.*agent_id.*agent_version"):
        db_module.ensure_schema_upgrades(engine)
