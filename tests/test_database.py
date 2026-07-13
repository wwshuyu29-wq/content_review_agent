from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select
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


def test_database_url_builds_sqlite_engine_with_foreign_keys(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)

    assert engine.url.drivername == "sqlite"
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


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
        "audit_runs",
        "batches",
        "content_items",
        "content_versions",
        "human_decisions",
        "issues",
        "projects",
        "review_tasks",
        "rule_versions",
    }


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


def test_seed_default_project_is_idempotent_and_preserves_campaign_facts(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        first = seed_default_project(session)
        second = seed_default_project(session)
        session.commit()

        assert first.id == second.id
        assert session.scalar(select(Project).where(Project.name == first.name)) is first
        assert len(first.rule_versions) == 1

        rules = first.current_rule_version
        assert rules is not None
        assert rules.version == 1
        assert rules.prompt_version == "review-prompt-v1"
        assert rules.project_facts["partner"] == "范丞丞"
        assert rules.project_facts["partnership_type"] == "短期合作伙伴"
        assert rules.project_facts["is_spokesperson"] is False
        assert rules.project_facts["official_slogan"] == "再复杂的出行，问小度想想就能搞定！"
        assert rules.structured_rules["deny_words"] == ["代言", "代言人"]
        assert rules.project_facts["pre_post_trip_assistant"] == {
            "positioning": "行前/行后AI公共出行助手",
            "capabilities": [
                "支持自然语言提出多点、多约束、多方式的出行需求",
                "支持时间窗口规划",
                "支持按人群特征和消费偏好规划",
                "支持公交、骑行、打车组合出行",
                "支持往返行程闭环规划",
            ],
        }
        assert rules.project_facts["in_trip_walking_companion"] == {
            "positioning": "行中AI步行陪伴助手",
            "capabilities": [
                "支持语音问答",
                "支持景点讲解及附近美食、厕所查询",
                "支持下雨、带儿童、步行场景路线规划",
                "支持精准游览时间规划",
                "支持动态调整路线",
                "支持终点餐饮和商圈推荐",
                "支持记忆用户出行偏好",
            ],
        }
        serialized_facts = str(rules.project_facts)
        assert "AR" not in serialized_facts
        assert "出行更简单，AI更懂你" not in serialized_facts
        assert "travel_features" not in rules.project_facts
        assert set(rules.dimension_standards) == {
            "compliance",
            "brand",
            "accuracy",
            "quality",
            "external",
        }


def test_get_session_uses_configured_database_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'configured.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    session_iterator = get_session()
    session = next(session_iterator)
    try:
        assert str(session.bind.url) == database_url
    finally:
        session_iterator.close()


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
        "review_tasks": {"content_item_id", "issue_id"},
        "human_decisions": {"review_task_id"},
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
        "content_items": {("batch_id", "external_id")},
        "content_versions": {("content_item_id", "version")},
        "agent_results": {("audit_run_id", "agent_name")},
        "review_tasks": {("issue_id",)},
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
            review_status=ReviewStatus.MANUAL_REQUIRED,
            publish_status=PublishStatus.NOT_READY,
        )
        session.add(item)
        session.commit()
        schema = ContentItemRead.model_validate(item)

        assert schema.model_dump(mode="json")["format_status"] == "INCOMPLETE"
        assert schema.model_dump(mode="json")["review_status"] == "MANUAL_REQUIRED"
        assert schema.model_dump(mode="json")["publish_status"] == "NOT_READY"

    with engine.connect() as connection:
        stored = connection.exec_driver_sql(
            "SELECT format_status, review_status, publish_status FROM content_items"
        ).one()
        assert stored == ("INCOMPLETE", "MANUAL_REQUIRED", "NOT_READY")


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
