from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.text_review import schema
from scripts.text_review.reviewers.base import AgentReviewResult
from scripts.text_review.reviewers.tech_media import AGENT_ORDER, TechMediaReviewer
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
    RuleVersion,
)
from server.seed import seed_default_project
from server.services.content_service import submit_batch
from server.services.deterministic_rule_service import ReviewContext
from server.services.report_service import build_report
from server.services.review_profile_service import get_review_profile
from server.services.review_service import _standards_from_rule_version, resolve_task, run_audit


class FakeReviewer:
    name = "fake-reviewer"

    def __init__(self, results, rewritten=("建议标题", "建议正文")):
        self.results = results
        self.rewritten = rewritten
        self.received_standards = None

    def review_structured(self, row, standards):
        self.received_standards = standards
        agent_ids = [
            "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
            "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
        ]
        if self.results and self.results[0].get("agent_id"):
            return self.results
        normalized = []
        for index, agent_id in enumerate(agent_ids):
            supplied = self.results[index] if index < len(self.results) else {"issues": []}
            issues = supplied.get("issues", [])
            normalized.append({
                **supplied,
                "agent_name": agent_id,
                "agent_id": agent_id,
                "agent_version": "tech-media-v1",
                "decision": "NEED_TEXT_FIX" if any(i.get("severity", "").lower() in {"mid", "medium"} for i in issues) else (
                    "HUMAN_REVIEW" if any(i.get("human_required") or i.get("severity", "").lower() in {"high", "unknown", "critical"} for i in issues) else (
                        "PASS_WITH_SUGGESTIONS" if issues else "PASS"
                    )
                ),
                "summary": "test result",
                "score": 90,
                "status": "COMPLETED",
            })
        return normalized

    def rewrite(self, row, standards):
        return self.rewritten


def issue(
    severity="low",
    *,
    rule_id="REPLACE-001",
    category="quality",
    field="body",
    evidence_quote="最优解",
    reason="重复标点",
    suggestion="一种可行方案",
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
        "source_reference": ["content_quality.md"],
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


class ProtocolReviewer:
    name = "protocol-test"

    def __init__(self, *, decisions=None, ids=None, version="tech-media-v1"):
        self.decisions = decisions or ["PASS"] * 6
        self.ids = ids
        self.version = version

    def review_structured(self, row, standards):
        ids = self.ids or [
            "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
            "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
        ]
        return [
            {
                "agent_name": agent_id,
                "agent_id": agent_id,
                "agent_version": self.version,
                "decision": decision,
                "summary": "protocol result",
                "score": 90,
                "status": decision,
                "issues": [],
                "raw_result": {},
            }
            for agent_id, decision in zip(ids, self.decisions)
        ]


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
                "body": "这是一段满足格式要求的原始正文，最优解。",
                "payload": {"platform": "小红书", "publish_time": "2026-07-14"},
            }
        ],
    )
    return project, batch, batch.content_items[0]


def tech_media_context_and_profile(tmp_path: Path):
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        profile = get_review_profile(project.current_rule_version)
    return ReviewContext(title="路线规划体验", body="路线结构清晰。", platform="xiaohongshu"), profile


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


def test_run_audit_rejects_missing_project_identity(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project, _, item = submit_valid_content(session)
        project.code = None
        with pytest.raises(ValueError, match="missing code"):
            run_audit(session, item.id, reviewer=FakeReviewer([]), model="model-v1")


def test_tech_media_progress_callback_reports_retries_and_terminal_transitions(
    tmp_path: Path,
) -> None:
    context, profile = tech_media_context_and_profile(tmp_path)
    attempts = {agent_id: 0 for agent_id in AGENT_ORDER}
    events = []

    class RetryingLLM:
        def chat_json(self, prompt, _response_model):
            agent_id = next(agent for agent in AGENT_ORDER if f"Specialist: {agent}" in prompt)
            attempts[agent_id] += 1
            if agent_id == "COMPLIANCE" and attempts[agent_id] < 3:
                raise RuntimeError("Authorization: Bearer secret retry failure")
            return AgentReviewResult(
                agent_id=agent_id,
                agent_version="tech-media-v1",
                decision="PASS",
                summary="通过",
                score=90,
                confidence=0.9,
                issues=[],
            ).model_dump(mode="json")

    results = TechMediaReviewer(llm=RetryingLLM()).review_structured(
        context,
        profile,
        progress_callback=lambda event, **payload: events.append((event, payload)),
    )

    assert [result.agent_id for result in results] == list(AGENT_ORDER)
    compliance_events = [entry for entry in events if entry[1]["agent_id"] == "COMPLIANCE"]
    assert [event for event, _payload in compliance_events] == [
        "agent_started",
        "agent_retry",
        "agent_retry",
        "agent_completed",
    ]
    assert [payload["attempt"] for _event, payload in compliance_events] == [1, 1, 2, 3]
    assert all("secret" not in str(payload) for _event, payload in compliance_events)


def test_completed_callback_failure_does_not_retry_a_valid_model_response(tmp_path: Path) -> None:
    context, profile = tech_media_context_and_profile(tmp_path)
    calls = 0

    class ValidLLM:
        def chat_json(self, prompt, _response_model):
            nonlocal calls
            calls += 1
            agent_id = next(agent for agent in AGENT_ORDER if f"Specialist: {agent}" in prompt)
            return AgentReviewResult(
                agent_id=agent_id,
                agent_version="tech-media-v1",
                decision="PASS",
                summary="通过",
                score=90,
                confidence=0.9,
                issues=[],
            ).model_dump(mode="json")

    def callback(event, **_payload):
        if event == "agent_completed":
            raise RuntimeError("progress persistence failed")

    with pytest.raises(RuntimeError, match="progress persistence failed"):
        TechMediaReviewer(llm=ValidLLM()).review_structured(
            context,
            profile,
            progress_callback=callback,
        )

    assert calls == 1


def test_tech_media_progress_callback_reports_terminal_unavailable_result(
    tmp_path: Path,
) -> None:
    context, profile = tech_media_context_and_profile(tmp_path)
    events = []

    class FailingLLM:
        def chat_json(self, _prompt, _response_model):
            raise RuntimeError("https://gateway.internal raw response secret")

    TechMediaReviewer(llm=FailingLLM()).review_structured(
        context,
        profile,
        progress_callback=lambda event, **payload: events.append((event, payload)),
    )

    first_agent_events = [entry for entry in events if entry[1]["agent_id"] == AGENT_ORDER[0]]
    assert [event for event, _payload in first_agent_events] == [
        "agent_started",
        "agent_retry",
        "agent_retry",
        "agent_failed",
    ]
    failure = first_agent_events[-1][1]
    assert failure["attempt"] == 3
    assert failure["result"].decision == "HUMAN_REVIEW"
    assert "gateway.internal" not in str(failure)
    assert "secret" not in str(failure)


def test_database_compatibility_layer_never_forwards_legacy_rule_arrays() -> None:
    rule_version = RuleVersion(
        content_type="TECH_MEDIA_REVIEW",
        structured_rules={"deny_words": [], "must_human_keywords": [], "required_tags": [], "recommended": {}},
        dimension_standards={},
        project_facts={},
        prompt_version="test",
    )

    standards = _standards_from_rule_version(rule_version)
    assert standards.deny_words == []
    assert standards.recommended == {}
    assert standards.must_human_keywords == []
    assert standards.required_tags == []


def test_run_audit_rejects_configured_project_with_mismatched_snapshot_identity(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project, _, item = submit_valid_content(session)
        project.content_type = "OTHER_CONTENT"
        session.flush()

        with pytest.raises(ValueError, match="content_type"):
            run_audit(session, item.id, reviewer=FakeReviewer([]), model="model-v1")


def test_default_tech_media_audit_persists_fixed_six_agent_protocol(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        audit = run_audit(session, item.id)

        assert [result.agent_id for result in audit.agent_results] == [
            "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
            "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
        ]
        assert [result.decision for result in audit.agent_results[:-1]] == ["HUMAN_REVIEW"] * 5
        assert audit.agent_results[-1].decision == "PASS_WITH_SUGGESTIONS"
        assert audit.agent_results[-1].score is None
        assert all(result.agent_version == "tech-media-v1" for result in audit.agent_results)
        brand_result = next(result for result in audit.agent_results if result.agent_id == "BRAND")
        assert brand_result.score is None
        assert any(
            finding.agent_result_id == brand_result.id
            and finding.rule_id == "SYSTEM-LLM-UNAVAILABLE"
            for finding in audit.issues
        )
        assert not any(finding.rule_id == "SYSTEM-AGENT-PROTOCOL" for finding in audit.issues)
        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY
        assert len(audit.issues) == 7
        assert sum(issue.human_required for issue in audit.issues) == 5


def test_unavailable_campaign_null_score_does_not_become_protocol_error(tmp_path: Path) -> None:
    results = ProtocolReviewer().review_structured(None, None)
    unavailable_issue = issue(
        "low", rule_id="SYSTEM-LLM-UNAVAILABLE", category="system_suggestion",
        auto_fixable=False, human_required=False,
    )
    unavailable_issue["source_reference"] = ["SYSTEM:LLM_UNAVAILABLE"]
    results[-1] = {
        **results[-1],
        "decision": "PASS_WITH_SUGGESTIONS",
        "summary": "建议型专项审核不可用，当前仅返回非阻断性系统提示。",
        "score": None,
        "issues": [unavailable_issue],
    }

    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        audit = run_audit(session, item.id, reviewer=FakeReviewer(results))

        assert audit.agent_results[-1].score is None
        assert not any(finding.rule_id == "SYSTEM-AGENT-PROTOCOL" for finding in audit.issues)


@pytest.mark.parametrize("decision", ["HUMAN_REVIEW", "BLOCK", "NEED_TEXT_FIX"])
def test_blocking_agent_decision_with_zero_issues_never_approves(tmp_path: Path, decision: str) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        audit = run_audit(session, item.id, reviewer=ProtocolReviewer(decisions=[decision] + ["PASS"] * 5))
        expected = ReviewStatus.BLOCKED if decision == "BLOCK" else ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert item.review_status is expected
        assert item.publish_status is PublishStatus.NOT_READY
        assert any(issue.rule_id in {"SYSTEM-AGENT-DECISION", "SYSTEM-AGENT-PROTOCOL"} for issue in audit.issues)


@pytest.mark.parametrize(
    "reviewer",
    [
        ProtocolReviewer(ids=["COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY", "CONTENT_QUALITY"]),
        ProtocolReviewer(ids=["COMPLIANCE", "BRAND", "BRAND", "TEST_CREDIBILITY", "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS"]),
        ProtocolReviewer(version="wrong-version"),
    ],
)
def test_invalid_agent_protocol_never_approves(tmp_path: Path, reviewer: ProtocolReviewer) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        audit = run_audit(session, item.id, reviewer=reviewer)
        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY
        assert any(issue.rule_id == "SYSTEM-AGENT-PROTOCOL" for issue in audit.issues)


@pytest.mark.parametrize(
    ("agent_id", "decision", "category"),
    [
        ("CAMPAIGN_EFFECTIVENESS", "BLOCK", "campaign"),
        ("BRAND", "HUMAN_REVIEW", "brand_tone"),
    ],
)
def test_role_boundary_violations_fail_closed_without_becoming_explicit_blocks(
    tmp_path: Path, agent_id: str, decision: str, category: str,
) -> None:
    ids = [
        "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
        "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
    ]
    results = []
    for current in ids:
        findings = []
        current_decision = "PASS"
        if current == agent_id:
            current_decision = decision
            findings = [issue(
                "high", rule_id=f"{current}-ADVERSARIAL", category=category,
                auto_fixable=False, human_required=True,
            )]
        results.append({
            "agent_name": current, "agent_id": current, "agent_version": "tech-media-v1",
            "decision": current_decision, "summary": "adversarial", "score": 50,
            "status": "COMPLETED", "issues": findings, "raw_result": {},
        })

    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        audit = run_audit(session, item.id, reviewer=FakeReviewer(results))

        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY
        assert not any(task.task_type == "BLOCK_REVIEW" for task in item.review_tasks)
        assert any(finding.rule_id == "SYSTEM-AGENT-PROTOCOL" for finding in audit.issues)


def test_brand_fact_conflict_remains_a_valid_human_review_decision(tmp_path: Path) -> None:
    ids = [
        "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
        "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
    ]
    results = []
    for current in ids:
        findings = []
        decision = "PASS"
        if current == "BRAND":
            decision = "HUMAN_REVIEW"
            findings = [issue(
                "high", rule_id="BRAND-FACT-001", category="brand_fact",
                auto_fixable=False, human_required=True,
            )]
        results.append({
            "agent_name": current, "agent_id": current, "agent_version": "tech-media-v1",
            "decision": decision, "summary": "brand fact", "score": 50,
            "status": "COMPLETED", "issues": findings, "raw_result": {},
        })

    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        audit = run_audit(session, item.id, reviewer=FakeReviewer(results))
        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert not any(finding.rule_id == "SYSTEM-AGENT-PROTOCOL" for finding in audit.issues)


def test_all_pass_and_pass_with_suggestions_are_eligible_for_clear_approval(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        pass_item = submit_batch(session, project_id=project.id, supplier_id="pass", name="pass", contents=[
            {"external_id": "pass", "title": "标题", "body": "完整正文", "payload": {}}
        ]).content_items[0]
        run_audit(session, pass_item.id, reviewer=ProtocolReviewer())
        assert pass_item.review_status is ReviewStatus.PASSED
        assert pass_item.publish_status is PublishStatus.READY

        project = pass_item.project
        second = submit_batch(
            session,
            project_id=project.id,
            supplier_id="suggestions",
            name="suggestions",
            contents=[{"external_id": "suggestions", "title": "标题", "body": "完整正文", "payload": {}}],
        ).content_items[0]
        run_audit(
            session,
            second.id,
            reviewer=ProtocolReviewer(decisions=["PASS_WITH_SUGGESTIONS"] * 6),
        )
        assert second.review_status is ReviewStatus.PASSED_WITH_SUGGESTIONS
        assert second.publish_status is PublishStatus.READY


def test_run_audit_uses_rule_version_snapshot_and_approves_no_issue_content(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        item = submit_batch(session, project_id=project.id, supplier_id="no-issue", name="no-issue", contents=[
            {"external_id": "no-issue", "title": "标题", "body": "完整正文", "payload": {}}
        ]).content_items[0]
        reviewer = FakeReviewer([agent_result("quality")])

        audit = run_audit(session, item.id, reviewer=reviewer, model="model-v1")

        assert audit.content_version.version == 1
        assert audit.rule_version_id == project.current_rule_version_id
        assert audit.model == "model-v1"
        assert audit.prompt_version == "tech_media_review-1.0"
        assert audit.status == "COMPLETED"
        assert reviewer.received_standards.deny_words == []
        assert reviewer.received_standards.recommended == {}
        assert "小度想想" in reviewer.received_standards.project_text
        assert item.review_status is ReviewStatus.PASSED
        assert item.publish_status is PublishStatus.READY
        assert len(audit.agent_results) == 6
        assert audit.agent_results[0].raw_result["marker"] == "persisted"
        assert audit.issues == []


def test_deterministic_issue_is_persisted_in_audit_before_reviewer_results(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        item = submit_batch(
            session,
            project_id=project.id,
            supplier_id="supplier-deterministic",
            name="确定性规则批次",
            contents=[{
                "external_id": "deterministic-1",
                "title": "酒店能力测评",
                "body": "小度想想可以自动筛选、比较酒店并判断最划算。",
                "payload": {"platform": "xiaohongshu"},
            }],
        ).content_items[0]

        audit = run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))

        assert [saved.rule_id for saved in audit.issues] == ["CLAIM-PENDING-001"]
        assert audit.issues[0].agent_result_id is None
        assert audit.issues[0].human_required is True
        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED


def test_representative_fixture_audit_keeps_revision_and_human_tasks_open(tmp_path: Path) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "representative_tech_media_review.json"
    draft = json.loads(fixture_path.read_text(encoding="utf-8"))

    def protocol_issue(rule_id, severity, *, human_required=False):
        return issue(
            severity.lower(), rule_id=rule_id, category="calibration", evidence_quote=rule_id,
            reason="representative calibration finding", suggestion="revise or verify",
            auto_fixable=False, human_required=human_required,
        )

    decisions_and_issues = [
        ("COMPLIANCE", "NEED_TEXT_FIX", [protocol_issue("COMPLIANCE-ABSOLUTE", "MEDIUM")]),
        ("BRAND", "PASS_WITH_SUGGESTIONS", [protocol_issue("BRAND-TONE", "LOW")]),
        ("PRODUCT_ACCURACY", "HUMAN_REVIEW", [protocol_issue("PENDING-HOTEL", "HIGH", human_required=True)]),
        ("TEST_CREDIBILITY", "HUMAN_REVIEW", [protocol_issue("EVIDENCE-UNBOUND", "HIGH", human_required=True)]),
        ("CONTENT_QUALITY", "NEED_TEXT_FIX", [protocol_issue("QUALITY-ADLIKE", "MEDIUM")]),
        ("CAMPAIGN_EFFECTIVENESS", "PASS_WITH_SUGGESTIONS", [protocol_issue("CAMPAIGN-HOOK", "LOW")]),
    ]
    results = [
        {
            "agent_name": agent_id, "agent_id": agent_id, "agent_version": "tech-media-v1",
            "decision": decision, "summary": "calibrated", "score": 70, "status": "COMPLETED",
            "issues": findings, "raw_result": {},
        }
        for agent_id, decision, findings in decisions_and_issues
    ]

    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        item = submit_batch(
            session, project_id=project.id, supplier_id="representative", name="representative",
            contents=[draft],
        ).content_items[0]

        audit = run_audit(session, item.id, reviewer=FakeReviewer(results))

        assert {finding.rule_id for finding in audit.issues} >= {
            "TEST-COUNT-001", "TEST-EVIDENCE-001", "CLAIM-UNSUPPORTED-ABSOLUTE-001",
            "CLAIM-PENDING-001", "COMPLIANCE-ABSOLUTE", "PENDING-HOTEL", "EVIDENCE-UNBOUND",
        }
        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY
        assert {task.task_type for task in item.review_tasks if task.status == "OPEN"} == {
            "HUMAN_REVIEW", "SUPPLIER_REVISION",
        }
        assert any(finding.human_required for finding in audit.issues)


def test_low_risk_auto_fixable_issues_persist_and_create_unapproved_v2(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        reviewer = FakeReviewer([agent_result()])

        audit = run_audit(session, item.id, reviewer=reviewer, model="model-v1")

        assert len(audit.agent_results) == 6
        assert len(audit.issues) == 1
        saved_issue = audit.issues[0]
        assert saved_issue.rule_id == "REPLACE-001"
        assert saved_issue.agent_result_id is None
        assert [(version.version, version.source) for version in item.versions] == [
            (1, "SUPPLIER"),
            (2, "AI_PROPOSED"),
        ]
        assert item.versions[-1].title == "原始标题"
        assert item.review_status is ReviewStatus.AUTO_FIX_PENDING
        assert item.publish_status is PublishStatus.NOT_READY
        assert [(task.task_type, task.status) for task in item.review_tasks] == [
            ("AUTO_FIX_PROPOSAL", "OPEN")
        ]
        task = item.review_tasks[0]
        assert task.target_content_version_id == item.versions[-1].id
        assert task.audit_run_id == audit.id


def test_unavailable_only_audit_can_be_superseded_without_deleting_history(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        item = submit_batch(
            session,
            project_id=project.id,
            supplier_id="supersede",
            name="不可用审核重试",
            contents=[{
                "external_id": "supersede-1",
                "title": "路线规划体验",
                "body": "路线规划步骤清晰，正文信息完整。",
                "payload": {"platform": "xiaohongshu"},
            }],
        ).content_items[0]
        unavailable = run_audit(session, item.id, reviewer=TechMediaReviewer())

        replacement = run_audit(session, item.id, reviewer=ProtocolReviewer())

        audits = list(session.scalars(select(AuditRun).where(AuditRun.content_item_id == item.id).order_by(AuditRun.id)))
        assert [audit.id for audit in audits] == [unavailable.id, replacement.id]
        assert unavailable.status == "SUPERSEDED"
        assert unavailable.review_key is None
        assert replacement.status == "COMPLETED"
        assert all(task.status == "SUPERSEDED" for task in unavailable.review_tasks)
        assert item.review_status is ReviewStatus.PASSED


def test_failed_historical_audit_does_not_block_retry_or_get_deleted(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project, _, item = submit_valid_content(session)
        failed = AuditRun(
            content_item=item,
            content_version=item.versions[-1],
            rule_version=project.current_rule_version,
            review_key=None,
            model="failed-model",
            prompt_version=project.current_rule_version.prompt_version,
            status="FAILED",
        )
        session.add(failed)
        session.commit()

        replacement = run_audit(session, item.id, reviewer=ProtocolReviewer())

        audits = list(session.scalars(
            select(AuditRun).where(AuditRun.content_item_id == item.id).order_by(AuditRun.id)
        ))
        assert [audit.id for audit in audits] == [failed.id, replacement.id]
        assert failed.status == "FAILED"
        assert replacement.status == "COMPLETED"


def test_incomplete_unavailable_audit_is_not_treated_as_supersedable(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        unavailable = run_audit(session, item.id, reviewer=TechMediaReviewer())
        session.delete(unavailable.agent_results[-1])
        session.commit()

        with pytest.raises(ValueError, match="open review tasks|already been audited"):
            run_audit(session, item.id, reviewer=ProtocolReviewer())

        assert unavailable.status == "COMPLETED"
        assert unavailable.review_key is not None


def test_reaudit_is_rejected_while_blocking_task_is_open(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))

        try:
            run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))
        except ValueError as error:
            assert "open review tasks" in str(error)
        else:
            raise AssertionError("re-audit must reject an active workflow")


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

        assert item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
        assert item.publish_status is PublishStatus.NOT_READY
        assert len(item.versions) == 1
        assert len(item.review_tasks) == 1
        assert item.review_tasks[0].issue.rule_id in {"FACT-UNKNOWN", "PARTNER-001"}
        assert item.review_tasks[0].task_type == "HUMAN_REVIEW"


def test_accept_suggestion_creates_confirmed_version_and_human_decision(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))
        task = item.review_tasks[0]

        decision = resolve_task(session, task.id, decision="ACCEPT_AUTO_FIX", reviewer="owner@example.com")

        assert decision.review_task_id == task.id
        assert task.status == "CLOSED"
        assert [(version.version, version.source) for version in item.versions] == [
            (1, "SUPPLIER"),
            (2, "AI_PROPOSED"),
            (3, "HUMAN_CONFIRMED"),
        ]
        assert item.versions[-1].body == "这是一段满足格式要求的原始正文，一种可行方案。"
        assert item.review_status is ReviewStatus.PASSED
        assert item.publish_status is PublishStatus.READY


def test_accept_edited_text_and_reject_proposal_preserve_version_history(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, edited_item = submit_valid_content(session)
        run_audit(session, edited_item.id, reviewer=FakeReviewer([agent_result()]))

        resolve_task(
            session,
            edited_item.review_tasks[0].id,
            decision="EDIT_AUTO_FIX",
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
            contents=[{"external_id": "reject", "title": "标题", "body": "一段完整的正文最优解", "payload": {}}],
        )
        rejected_item = rejected_batch.content_items[0]
        run_audit(session, rejected_item.id, reviewer=FakeReviewer([agent_result()]))

        resolve_task(
            session,
            rejected_item.review_tasks[0].id,
            decision="REJECT_AUTO_FIX",
            reviewer="editor@example.com",
            note="建议不准确",
        )

        assert len(rejected_item.versions) == 2
        assert rejected_item.review_status is ReviewStatus.SUPPLIER_REVISION_REQUIRED
        assert rejected_item.publish_status is PublishStatus.NOT_READY
        assert any(task.task_type == "SUPPLIER_REVISION" and task.status == "OPEN" for task in rejected_item.review_tasks)


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
            decision="HUMAN_APPROVE",
            reviewer="legal@example.com",
            payload={"title": "核准标题", "body": "经人工核准的正文"},
        )

        assert item.versions[-1].source == "HUMAN_APPROVED"
        assert item.review_status is ReviewStatus.PASSED
        assert item.publish_status is PublishStatus.READY
        assert session.scalars(select(HumanDecision)).one().decision == "HUMAN_APPROVE"


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
            decision="HUMAN_APPROVE",
            reviewer="legal@example.com",
        )

        assert item.review_status is ReviewStatus.PASSED
        assert item.publish_status is PublishStatus.READY
        assert item.publish_status is PublishStatus.READY


def test_rejecting_risk_closes_sibling_tasks_and_is_terminal(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        risks = [
            issue("high", rule_id="RISK-A", auto_fixable=False, human_required=True),
            issue("high", rule_id="RISK-B", auto_fixable=False, human_required=True),
        ]
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result("external", risks)]))
        assert len(item.review_tasks) == 1
        task = item.review_tasks[0]

        resolve_task(session, task.id, decision="HUMAN_REJECT", reviewer="legal@example.com")

        assert item.review_status is ReviewStatus.REJECTED
        assert item.publish_status is PublishStatus.NOT_READY
        with pytest.raises(ValueError, match="closed"):
            resolve_task(session, task.id, decision="HUMAN_APPROVE", reviewer="legal@example.com")


def test_rejected_content_cannot_be_reaudited_or_create_audit_run(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        risk = issue("high", auto_fixable=False, human_required=True)
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result("external", [risk])]))
        resolve_task(session, item.review_tasks[0].id, decision="HUMAN_REJECT", reviewer="legal@example.com")
        audit_count = len(item.audit_runs)

        with pytest.raises(ValueError, match="Rejected content is terminal"):
            run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))

        assert item.review_status is ReviewStatus.REJECTED
        assert item.publish_status is PublishStatus.NOT_READY
        assert len(item.audit_runs) == audit_count


def test_approvals_validate_trimmed_content_and_format_limits(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        _, _, item = submit_valid_content(session)
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))
        task = item.review_tasks[0]

        invalid_payloads = (
            {"title": " ", "body": "valid"},
            {"title": "valid", "body": " "},
            {"title": "x" * 501, "body": "valid"},
        )
        for payload in invalid_payloads:
            try:
                resolve_task(session, task.id, decision="EDIT_AUTO_FIX", reviewer="editor", payload=payload)
            except ValueError as error:
                assert "format" in str(error).lower()
            else:
                raise AssertionError("invalid edited content must be rejected")


def test_build_report_returns_project_batch_status_category_rule_and_manual_metrics(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        batch = submit_batch(session, project_id=project.id, supplier_id="approved", name="approved", contents=[
            {"external_id": "approved", "title": "标题", "body": "完整正文", "payload": {}}
        ])
        approved_item = batch.content_items[0]
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
        assert report["status_counts"] == {"PASSED": 1, "HUMAN_REVIEW_REQUIRED": 1}
        assert report["category_counts"] == {"external": 1}
        assert report["rule_counts"] == {"PARTNER-003": 1}
        assert report["manual_metrics"] == {"contents": 1, "tasks": 1, "rate": 0.5}
        assert batch_report["batch"] == {"id": batch.id, "name": batch.name}
        assert batch_report["totals"]["contents"] == 1


def test_report_counts_only_latest_audit_issues_and_open_tasks(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        project, _, item = submit_valid_content(session)
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))
        resolve_task(session, item.review_tasks[0].id, decision="ACCEPT_AUTO_FIX", reviewer="owner")
        run_audit(session, item.id, reviewer=FakeReviewer([agent_result()]))

        report = build_report(session, project_id=project.id)

        assert report["totals"] == {"contents": 1, "issues": 0, "tasks": 0}
        assert report["historical_totals"] == {"issues": 1, "tasks": 1}


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
