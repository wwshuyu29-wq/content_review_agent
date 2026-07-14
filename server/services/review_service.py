from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from scripts.text_review import schema
from scripts.text_review.reviewer import get_reviewer
from scripts.text_review.standards import Standards
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
from server.services.content_service import validate_content_format
from server.services.deterministic_rule_service import ReviewContext, evaluate_rules
from server.services.review_profile_service import get_review_profile

MANUAL_SEVERITIES = {"mid", "high", "unknown"}
ISSUE_FIELDS = (
    "rule_id", "category", "severity", "field", "evidence_quote", "reason",
    "suggestion", "source_reference", "auto_fixable", "human_required", "confidence",
)


def validate_rule_version_identity(project, rule_version: RuleVersion) -> None:
    if not project.code or not project.content_type:
        raise ValueError("project is missing code or content_type")
    expected = {
        "business_domain": "baidu_maps_marketing_review",
        "document_type": "project_standard",
        "project_code": project.code,
        "content_type": project.content_type,
    }
    for field, value in expected.items():
        if getattr(rule_version, field) != value:
            raise ValueError(f"rule version identity mismatch: {field}")
    if not rule_version.package_version:
        raise ValueError("rule version identity mismatch: package_version")
    if not rule_version.package_digest:
        raise ValueError("rule version identity mismatch: package_digest")


def _standards_from_rule_version(rule_version: RuleVersion) -> Standards:
    rules = rule_version.structured_rules
    facts_text = "\n".join(f"{key}: {value}" for key, value in rule_version.project_facts.items())
    dimensions = rule_version.dimension_standards
    dimension_docs = dimensions.get("standards", dimensions) if isinstance(dimensions, dict) else {}
    return Standards(
        global_text="\n\n".join(str(value) for value in dimension_docs.values()),
        project_text=facts_text,
        dimension_docs=dict(dimension_docs),
        deny_words=[],
        recommended={},
        must_human_keywords=[],
        required_tags=[],
    )


def _latest_version(item: ContentItem) -> ContentVersion:
    if not item.versions:
        raise ValueError(f"Content item {item.id} has no versions")
    return item.versions[-1]


def _new_version(item: ContentItem, *, source: str, title: str, body: str, payload: Mapping[str, Any]) -> ContentVersion:
    title, body = validate_content_format(title, body)
    version = ContentVersion(
        content_item=item,
        version=_latest_version(item).version + 1,
        source=source,
        title=title,
        body=body,
        payload=dict(payload),
    )
    item.title = title
    return version


def _open_tasks(item: ContentItem) -> list[ReviewTask]:
    return [task for task in item.review_tasks if task.status == "OPEN"]


def _derive_state(item: ContentItem, *, approved_when_clear: bool = False) -> None:
    if item.review_status is ReviewStatus.REJECTED:
        item.publish_status = PublishStatus.NOT_READY
        return
    open_tasks = _open_tasks(item)
    if open_tasks:
        item.publish_status = PublishStatus.NOT_READY
        item.review_status = (
            ReviewStatus.FIX_PROPOSED
            if all(task.task_type == "REVIEW_FIX_PROPOSAL" for task in open_tasks)
            else ReviewStatus.MANUAL_REQUIRED
        )
        return
    if approved_when_clear:
        item.review_status = ReviewStatus.APPROVED
        item.publish_status = PublishStatus.READY
    else:
        item.review_status = ReviewStatus.MANUAL_REQUIRED
        item.publish_status = PublishStatus.NOT_READY


def _normalize_agent_results(reviewer: Any, row: dict, standards: Standards) -> list[dict]:
    if hasattr(reviewer, "review_structured"):
        return list(reviewer.review_structured(row, standards))
    verdict = reviewer.review(row, standards)
    issues = []
    for index, reason in enumerate(verdict.issues):
        issues.append({
            "rule_id": f"LEGACY-{index + 1}",
            "category": verdict.categories[index] if index < len(verdict.categories) else "unknown",
            "severity": verdict.risk_level,
            "field": "body",
            "evidence_quote": "",
            "reason": reason,
            "suggestion": verdict.suggestion,
            "auto_fixable": verdict.risk_level == schema.RISK_LOW,
            "human_required": verdict.risk_level in MANUAL_SEVERITIES,
            "confidence": verdict.confidence,
        })
    return [{"agent_name": "legacy", "status": "COMPLETED", "issues": issues, "raw_result": {}}]


def run_audit(
    session: Session,
    content_item_id: int,
    *,
    reviewer: Any = None,
    model: Optional[str] = None,
) -> AuditRun:
    item = session.get(ContentItem, content_item_id)
    if item is None:
        raise ValueError(f"Content item {content_item_id} does not exist")
    if item.review_status is ReviewStatus.REJECTED:
        raise ValueError("Rejected content is terminal")
    if item.format_status is not FormatStatus.PASSED:
        raise ValueError("Only content with PASSED format status can be audited")
    if _open_tasks(item):
        raise ValueError("Content has open review tasks; resolve them before re-audit")

    rule_version = item.project.current_rule_version
    if rule_version is None:
        raise ValueError("Project has no current rule version")
    validate_rule_version_identity(item.project, rule_version)
    content_version = _latest_version(item)
    reviewer = reviewer or get_reviewer("heuristic")
    standards = _standards_from_rule_version(rule_version)
    profile = get_review_profile(rule_version)
    row = {
        schema.COL_ID: item.external_id,
        schema.COL_TITLE: content_version.title,
        schema.COL_BODY: content_version.body,
        **content_version.payload,
    }
    audit = AuditRun(
        content_item=item,
        content_version=content_version,
        rule_version=rule_version,
        model=model or getattr(reviewer, "name", reviewer.__class__.__name__),
        prompt_version=rule_version.prompt_version,
        status="RUNNING",
    )
    item.review_status = ReviewStatus.AI_REVIEWING
    item.publish_status = PublishStatus.NOT_READY
    session.add(audit)
    session.flush()

    context = ReviewContext(
        title=content_version.title,
        body=content_version.body,
        platform=str(content_version.payload.get("platform", "")),
        content_type=item.project.content_type or "",
        project_id=str(item.project_id),
        project_code=item.project.code or "",
        test_cases=list(content_version.payload.get("test_cases", [])),
        evidence=list(content_version.payload.get("evidence", [])),
        evidence_assets=list(content_version.payload.get("evidence_assets", [])),
    )
    persisted_issues: list[Issue] = []
    for deterministic in evaluate_rules(profile, context):
        persisted = Issue(
            audit_run=audit,
            rule_id=deterministic.rule_id,
            category=deterministic.category,
            severity=deterministic.severity,
            field=deterministic.field,
            evidence_quote=deterministic.evidence,
            source_reference=deterministic.source_reference,
            reason=deterministic.reason,
            suggestion=deterministic.suggestion,
            auto_fixable=deterministic.auto_fixable,
            human_required=deterministic.human_required,
            confidence=deterministic.confidence,
        )
        session.add(persisted)
        persisted_issues.append(persisted)
    for result_data in _normalize_agent_results(reviewer, row, standards):
        result = AgentResult(
            audit_run=audit,
            agent_name=result_data["agent_name"],
            status=result_data.get("status", "COMPLETED"),
            raw_result=dict(result_data.get("raw_result", result_data)),
        )
        session.add(result)
        session.flush()
        for issue_data in result_data.get("issues", []):
            issue_data = {"source_reference": [], **issue_data}
            missing = [field for field in ISSUE_FIELDS if field not in issue_data]
            if missing:
                raise ValueError(f"Structured issue missing fields: {', '.join(missing)}")
            persisted = Issue(
                audit_run=audit,
                agent_result=result,
                **{field: issue_data[field] for field in ISSUE_FIELDS},
            )
            session.add(persisted)
            persisted_issues.append(persisted)
    session.flush()

    manual_issues = [
        issue for issue in persisted_issues
        if issue.human_required or issue.severity.lower() in MANUAL_SEVERITIES
    ]
    purely_safe_low_risk = bool(persisted_issues) and not manual_issues and all(
        issue.severity.lower() == "low" and issue.auto_fixable for issue in persisted_issues
    )
    if manual_issues or (persisted_issues and not purely_safe_low_risk):
        for issue in manual_issues or persisted_issues:
            session.add(ReviewTask(
                content_item=item,
                target_content_version=content_version,
                audit_run=audit,
                issue=issue,
                task_type="RISK_REVIEW",
            ))
        session.flush()
        _derive_state(item)
    elif purely_safe_low_risk:
        proposed_title, proposed_body = reviewer.rewrite(row, standards)
        proposed = _new_version(
            item,
            source="AI_PROPOSED",
            title=proposed_title,
            body=proposed_body,
            payload=content_version.payload,
        )
        session.add(proposed)
        session.flush()
        session.add(ReviewTask(
            content_item=item,
            target_content_version=proposed,
            audit_run=audit,
            task_type="REVIEW_FIX_PROPOSAL",
        ))
        session.flush()
        _derive_state(item)
    else:
        _derive_state(item, approved_when_clear=True)

    audit.status = "COMPLETED"
    audit.completed_at = datetime.utcnow()
    session.commit()
    session.refresh(audit)
    return audit


def resolve_task(
    session: Session,
    review_task_id: int,
    *,
    decision: str,
    reviewer: str,
    note: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> HumanDecision:
    task = session.get(ReviewTask, review_task_id)
    if task is None:
        raise ValueError(f"Review task {review_task_id} does not exist")
    if task.status != "OPEN":
        raise ValueError("Review task is already closed")
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    item = task.content_item
    if item.review_status is ReviewStatus.REJECTED:
        raise ValueError("Rejected content is terminal")

    payload = dict(payload or {})
    target = task.target_content_version
    allowed = {
        "REVIEW_FIX_PROPOSAL": {"ACCEPT_SUGGESTION", "ACCEPT_EDITED", "REJECT_SUGGESTION"},
        "RISK_REVIEW": {"APPROVE_RISK", "REJECT_RISK"},
    }
    if decision not in allowed.get(task.task_type, set()):
        raise ValueError(f"Decision {decision} is invalid for {task.task_type}")

    approved_when_clear = False
    if decision == "ACCEPT_SUGGESTION":
        if target.source != "AI_PROPOSED":
            raise ValueError("Task target is not an AI proposed version")
        session.add(_new_version(
            item, source="HUMAN_CONFIRMED", title=target.title, body=target.body, payload=target.payload
        ))
        approved_when_clear = True
    elif decision == "ACCEPT_EDITED":
        title, body = validate_content_format(payload.get("title"), payload.get("body"))
        session.add(_new_version(
            item, source="HUMAN_EDITED", title=title, body=body, payload=target.payload
        ))
        approved_when_clear = True
    elif decision == "REJECT_SUGGESTION":
        session.add(ReviewTask(
            content_item=item,
            target_content_version=target,
            audit_run=task.audit_run,
            task_type="RISK_REVIEW",
        ))
    elif decision == "APPROVE_RISK":
        title = payload.get("title", target.title)
        body = payload.get("body", target.body)
        title, body = validate_content_format(title, body)
        session.add(_new_version(
            item, source="HUMAN_APPROVED", title=title, body=body, payload=target.payload
        ))
        approved_when_clear = True
    elif decision == "REJECT_RISK":
        item.review_status = ReviewStatus.REJECTED
        for candidate in _open_tasks(item):
            if candidate.id != task.id:
                candidate.status = "SUPERSEDED"
                candidate.closed_at = datetime.utcnow()

    task.status = "CLOSED"
    task.closed_at = datetime.utcnow()
    human_decision = HumanDecision(
        review_task=task,
        decision=decision,
        reviewer=reviewer.strip(),
        note=note,
        payload=payload,
    )
    session.add(human_decision)
    session.flush()
    _derive_state(item, approved_when_clear=approved_when_clear)
    session.commit()
    session.refresh(human_decision)
    return human_decision
