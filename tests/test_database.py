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
from server.schemas import ContentItemCreate, IssueCreate
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
        assert rules.project_facts["official_slogan"] == "出行更简单，AI更懂你"
        assert rules.structured_rules["deny_words"] == ["代言", "代言人"]
        assert rules.project_facts["travel_features"] == [
            "AI安排行程",
            "个性化出游推荐",
            "智能规划游玩路线",
            "AI导游跟随讲解",
            "AR导览实景沉浸式游玩",
        ]
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
