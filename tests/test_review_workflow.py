from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.text_review import schema
from server.db import Base, create_db_engine
from server.models import (
    AgentResult,
    AuditRun,
    ContentItem,
    ContentVersion,
    FormatStatus,
    HumanDecision,
    Issue,
    PublishStatus,
    ReviewStatus,
    ReviewTask,
)
from server.seed import seed_default_project
from server.services.content_service import submit_batch
from server.services.report_service import build_report
from server.services.review_service import resolve_task, run_audit


class FakeReviewer:
    name = "fake-reviewer"

    def __init__(self, results, rewritten=("建议标题", "建议正文")):
        self.results = results
        self.rewritten = rewritten
        self.received_standards = None

    def review_structured(self, row, standards):
        self.received_standards = standards
        return self.results

    def rewrite(self, row, standards):
        return self.rewritten


def issue(
    severity="low",
    *,
    rule_id="QUALITY-001",
    category="quality",
    field="body",
    evidence_quote="！！！",
    reason="重复标点",
    suggestion="！",
    auto_fixable=True,
    human_required=False,
    confidence=0.95,
):
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "field": field,
        "evidence_quote": evidence_quote,
        "reason": reason,
        "suggestion": suggestion,
        "auto_fixable": auto_fixable,
        "human_required": human_required,
        "confidence": confidence,
    }


def agent_result(name="quality", issues=None, status="COMPLETED"):
    return {
        "agent_name": name,
        "status": status,
        "issues": issues or [],
        "raw_result": {"dimension": name, "marker": "persisted"},
    }


def make_session(tmp_path: Path) -> Session:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def submit_valid_content(session: Session):
    project = seed_default_project(session)
    batch = submit_batch(
        session,
        project_id=project.id,
        supplier_id="supplier-1",
        name="首批内容",
        contents=[
            {
                "external_id": "content-1",
                "title": "原始标题",
                "body": "这是一段满足格式要求的原始正文。",
                "payload": {"platform": "小红书", "publish_time": "2026-07-14"},
            }
        ],
    )
    return project, batch, batch.content_items[0]


def test_submit_batch_creates_v1_and_deterministic_format_statuses(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        batch = submit_batch(
            session,
            project_id=project.id,
            supplier_id="supplier-1",
            name="格式测试",
            contents=[
                {"external_id": "ok", "title": "标题", "body": "完整正文", "payload": {}},
                {"external_id": "missing", "title": "", "body": "正文", "payload": {}},
                {"external_id": "invalid", "title": ["错误类型"], "body": "正文", "payload": {}},
            ],
        )

        assert {item.external_id: item.format_status for item in batch.content_items} == {
            "ok": FormatStatus.PASSED,
            "missing": FormatStatus.INCOMPLETE,
            "invalid": FormatStatus.INVALID,
        }
        assert all(len(item.versions) == 1 for item in batch.content_items)
        assert all(item.versions[0].version == 1 for item in batch.content_items)
        assert all(item.versions[0].source == "SUPPLIER" for item in batch.content_items)
        assert all(item.review_status is ReviewStatus.NOT_STARTED for item in batch.content_items)
        assert all(item.publish_status is PublishStatus.NOT_READY for item in batch.content_items)


def test_run_audit_uses_rule_version_snapshot_and_approves_no_issue_content(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project, _, item = submit_valid_content(session)
        reviewer = FakeReviewer([agent_result("quality")])

        audit = run_audit(session, item.id, reviewer=reviewer, model="model-v1")

        assert audit.content_version.version == 1
        assert audit.rule_version_id == project.current_rule_version_id
        assert audit.model == "model-v1"
        assert audit.prompt_version == "review-prompt-v1"
        assert audit.status == "COMPLETED"
        assert reviewer.received_standards.deny_words == ["代言", "代言人"]
        assert reviewer.received_standards.recommended["范丞丞代言百度地图"] == "范丞丞与百度地图开展短期合作"
        assert "短期合作伙伴" in reviewer.received_standards.project_text
        assert item.review_status is ReviewStatus.APPROVED
        assert item.publish_status is PublishStatus.READY
        assert len(audit.agent_results) == 1
        assert audit.agent_results[0].raw_result["marker"] == "persisted"
        assert audit.issues == []


def test_low_risk_auto_fixable_issues_persist_and_create_unapproved_v2(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        structured_issue = issue()
        reviewer = FakeReviewer([agent_result(issues=[structured_issue])])

        audit = run_audit(session, item.id, reviewer=reviewer, model="model-v1")

        assert len(audit.agent_results) == 1
        assert len(audit.issues) == 1
        saved_issue = audit.issues[0]
        for key, value in structured_issue.items():
            assert getattr(saved_issue, key) == value
        assert [(version.version, version.source) for version in item.versions] == [
            (1, "SUPPLIER"),
            (2, "AI_PROPOSED"),
        ]
        assert item.versions[-1].title == "建议标题"
        assert item.review_status is ReviewStatus.FIX_PROPOSED
        assert item.publish_status is PublishStatus.NOT_READY
        assert [(task.task_type, task.status) for task in item.review_tasks] == [
            ("REVIEW_FIX_PROPOSAL", "OPEN")
        ]


def test_manual_priority_prevents_rewrite_and_creates_tasks(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        low = issue()
        unknown = issue(
            "unknown",
            rule_id="FACT-UNKNOWN",
            category="accuracy",
            reason="事实不足",
            suggestion="人工核验",
            auto_fixable=False,
            confidence=0.2,
        )
        manual = issue(
            "low",
            rule_id="PARTNER-001",
            category="external",
            evidence_quote="范丞丞",
            reason="合作身份需人工确认",
            suggestion="核对授权",
            auto_fixable=True,
            human_required=True,
        )
        reviewer = FakeReviewer(
            [agent_result("quality", [low]), agent_result("accuracy", [unknown]), agent_result("external", [manual])]
        )

        run_audit(session, item.id, reviewer=reviewer)

        assert item.review_status is ReviewStatus.MANUAL_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY
        assert len(item.versions) == 1
        assert {task.issue.rule_id for task in item.review_tasks} == {"FACT-UNKNOWN", "PARTNER-001"}
        assert {task.task_type for task in item.review_tasks} == {"RISK_REVIEW"}


def test_accept_suggestion_creates_confirmed_version_and_human_decision(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result(issues=[issue()])]))
        task = item.review_tasks[0]

        decision = resolve_task(session, task.id, decision="ACCEPT_SUGGESTION", reviewer="owner@example.com")

        assert decision.review_task_id == task.id
        assert task.status == "CLOSED"
        assert [(version.version, version.source) for version in item.versions] == [
            (1, "SUPPLIER"),
            (2, "AI_PROPOSED"),
            (3, "HUMAN_CONFIRMED"),
        ]
        assert item.versions[-1].body == "建议正文"
        assert item.review_status is ReviewStatus.APPROVED
        assert item.publish_status is PublishStatus.READY


def test_accept_edited_text_and_reject_proposal_preserve_version_history(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, edited_item = submit_valid_content(session)
        run_audit(session, edited_item.id, reviewer=FakeReviewer([agent_result(issues=[issue()])]))

        resolve_task(
            session,
            edited_item.review_tasks[0].id,
            decision="ACCEPT_EDITED",
            reviewer="editor@example.com",
            payload={"title": "人工标题", "body": "人工编辑后的正文"},
        )

        assert edited_item.versions[-1].source == "HUMAN_EDITED"
        assert edited_item.versions[-1].title == "人工标题"
        assert edited_item.versions[-1].body == "人工编辑后的正文"

        project = edited_item.project
        rejected_batch = submit_batch(
            session,
            project_id=project.id,
            supplier_id="supplier-2",
            name="拒绝建议批次",
            contents=[{"external_id": "reject", "title": "标题", "body": "一段完整的正文", "payload": {}}],
        )
        rejected_item = rejected_batch.content_items[0]
        run_audit(session, rejected_item.id, reviewer=FakeReviewer([agent_result(issues=[issue()])]))

        resolve_task(
            session,
            rejected_item.review_tasks[0].id,
            decision="REJECT_SUGGESTION",
            reviewer="editor@example.com",
            note="建议不准确",
        )

        assert len(rejected_item.versions) == 2
        assert rejected_item.review_status is ReviewStatus.MANUAL_REQUIRED
        assert rejected_item.publish_status is PublishStatus.NOT_READY
        assert any(task.task_type == "RISK_REVIEW" and task.status == "OPEN" for task in rejected_item.review_tasks)


def test_risk_decisions_create_versions_and_update_statuses(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        high = issue(
            "high",
            rule_id="PARTNER-002",
            category="external",
            auto_fixable=False,
            human_required=True,
        )
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result("external", [high])]))
        task = item.review_tasks[0]

        resolve_task(
            session,
            task.id,
            decision="APPROVE_RISK",
            reviewer="legal@example.com",
            payload={"title": "核准标题", "body": "经人工核准的正文"},
        )

        assert item.versions[-1].source == "HUMAN_APPROVED"
        assert item.review_status is ReviewStatus.APPROVED
        assert item.publish_status is PublishStatus.READY
        assert session.scalars(select(HumanDecision)).one().decision == "APPROVE_RISK"


def test_risk_approval_waits_for_all_open_risk_tasks(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        first = issue(
            "high",
            rule_id="RISK-001",
            category="external",
            auto_fixable=False,
            human_required=True,
        )
        second = issue(
            "unknown",
            rule_id="RISK-002",
            category="accuracy",
            auto_fixable=False,
            human_required=True,
        )
        run_audit(
            session,
            item.id,
            reviewer=FakeReviewer([agent_result("external", [first]), agent_result("accuracy", [second])]),
        )

        resolve_task(
            session,
            item.review_tasks[0].id,
            decision="APPROVE_RISK",
            reviewer="legal@example.com",
        )

        assert item.review_status is ReviewStatus.MANUAL_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY

        resolve_task(
            session,
            item.review_tasks[1].id,
            decision="APPROVE_RISK",
            reviewer="legal@example.com",
        )

        assert item.review_status is ReviewStatus.APPROVED
        assert item.publish_status is PublishStatus.READY


def test_build_report_returns_project_batch_status_category_rule_and_manual_metrics(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project, batch, approved_item = submit_valid_content(session)
        run_audit(session, approved_item.id, reviewer=FakeReviewer([agent_result()]))
        second = submit_batch(
            session,
            project_id=project.id,
            supplier_id="supplier-2",
            name="人工批次",
            contents=[{"external_id": "manual", "title": "标题", "body": "一段完整正文", "payload": {}}],
        ).content_items[0]
        high = issue(
            "high",
            rule_id="PARTNER-003",
            category="external",
            auto_fixable=False,
            human_required=True,
        )
        run_audit(session, second.id, reviewer=FakeReviewer([agent_result("external", [high])]))

        report = build_report(session, project_id=project.id)
        batch_report = build_report(session, project_id=project.id, batch_id=batch.id)

        assert report["project"] == {"id": project.id, "name": project.name}
        assert report["totals"]["contents"] == 2
        assert report["status_counts"] == {"APPROVED": 1, "MANUAL_REQUIRED": 1}
        assert report["category_counts"] == {"external": 1}
        assert report["rule_counts"] == {"PARTNER-003": 1}
        assert report["manual_metrics"] == {"contents": 1, "tasks": 1, "rate": 0.5}
        assert batch_report["batch"] == {"id": batch.id, "name": batch.name}
        assert batch_report["totals"]["contents"] == 1


def test_multi_agent_reviewer_normalizes_legacy_dimension_issues() -> None:
    from scripts.text_review.reviewers.base import DimensionResult
    from scripts.text_review.reviewers.orchestrator import MultiAgentReviewer
    from scripts.text_review.standards import Standards

    reviewer = MultiAgentReviewer()
    reviewer.agents = [
        type(
            "LegacyAgent",
            (),
            {
                "review": lambda self, row, standards, llm: DimensionResult(
                    dimension="quality",
                    risk_level=schema.RISK_LOW,
                    issues=["重复标点"],
                    evidence=["！！！"],
                    confidence=0.8,
                )
            },
        )()
    ]

    structured = reviewer.review_structured(
        {schema.COL_TITLE: "标题", schema.COL_BODY: "正文！！！"}, Standards()
    )

    assert structured[0]["issues"] == [
        {
            "rule_id": "QUALITY-001",
            "category": "quality",
            "severity": "low",
            "field": "body",
            "evidence_quote": "！！！",
            "reason": "重复标点",
            "suggestion": "重复标点",
            "auto_fixable": True,
            "human_required": False,
            "confidence": 0.8,
        }
    ]


def test_multi_agent_reviewer_keeps_verdict_api_and_exposes_structured_results() -> None:
    from scripts.text_review.reviewers.base import DimensionResult, StructuredIssue
    from scripts.text_review.reviewers.orchestrator import MultiAgentReviewer
    from scripts.text_review.standards import Standards

    reviewer = MultiAgentReviewer()
    reviewer.agents = [
        type(
            "FakeAgent",
            (),
            {
                "review": lambda self, row, standards, llm: DimensionResult(
                    dimension="quality",
                    risk_level=schema.RISK_LOW,
                    issues=["重复标点"],
                    structured_issues=[
                        StructuredIssue(
                            rule_id="QUALITY-001",
                            category="quality",
                            severity="low",
                            field="body",
                            evidence_quote="！！！",
                            reason="重复标点",
                            suggestion="！",
                            auto_fixable=True,
                            human_required=False,
                            confidence=0.9,
                        )
                    ],
                )
            },
        )()
    ]
    standards = Standards()
    row = {schema.COL_TITLE: "标题", schema.COL_BODY: "正文！！！"}

    verdict = reviewer.review(row, standards)
    structured = reviewer.review_structured(row, standards)

    assert verdict.risk_level == schema.RISK_LOW
    assert verdict.issues == ["[内容质量] 重复标点"]
    assert structured[0]["agent_name"] == "quality"
    assert structured[0]["issues"][0]["rule_id"] == "QUALITY-001"
