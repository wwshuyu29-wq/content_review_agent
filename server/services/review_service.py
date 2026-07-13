from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from sqlalchemy import select
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

MANUAL_SEVERITIES = {"mid", "high", "unknown"}
ISSUE_FIELDS = (
    "rule_id",
    "category",
    "severity",
    "field",
    "evidence_quote",
    "reason",
    "suggestion",
    "auto_fixable",
    "human_required",
    "confidence",
)


def _standards_from_rule_version(rule_version: RuleVersion) -> Standards:
    rules = rule_version.structured_rules
    facts = rule_version.project_facts
    facts_text = "\n".join(f"{key}: {value}" for key, value in facts.items())
    return Standards(
        global_text="\n\n".join(str(value) for value in rule_version.dimension_standards.values()),
        project_text=facts_text,
        dimension_docs=dict(rule_version.dimension_standards),
        deny_words=list(rules.get("deny_words", [])),
        recommended=dict(rules.get("recommended", {})),
        must_human_keywords=list(rules.get("must_human_keywords", [])),
        required_tags=list(rules.get("required_tags", [])),
    )


def _latest_version(item: ContentItem) -> ContentVersion:
    if not item.versions:
        raise ValueError(f"Content item {item.id} has no versions")
    return item.versions[-1]


def _new_version(item: ContentItem, *, source: str, title: str, body: str, payload: Mapping[str, Any]) -> ContentVersion:
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


def _normalize_agent_results(reviewer: Any, row: dict, standards: Standards) -> list[dict]:
    if hasattr(reviewer, "review_structured"):
        return list(reviewer.review_structured(row, standards))
    verdict = reviewer.review(row, standards)
    issues = []
    for index, reason in enumerate(verdict.issues):
        issues.append(
            {
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
            }
        )
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
    if item.format_status is not FormatStatus.PASSED:
        raise ValueError("Only content with PASSED format status can be audited")

    rule_version = item.project.current_rule_version
    if rule_version is None:
        raise ValueError("Project has no current rule version")
    content_version = _latest_version(item)
    reviewer = reviewer or get_reviewer("offline")
    standards = _standards_from_rule_version(rule_version)
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
    session.add(audit)
    session.flush()

    structured_results = _normalize_agent_results(reviewer, row, standards)
    persisted_issues: list[Issue] = []
    for result_data in structured_results:
        result = AgentResult(
            audit_run=audit,
            agent_name=result_data["agent_name"],
            status=result_data.get("status", "COMPLETED"),
            raw_result=dict(result_data.get("raw_result", result_data)),
        )
        session.add(result)
        session.flush()
        for issue_data in result_data.get("issues", []):
            missing = [field for field in ISSUE_FIELDS if field not in issue_data]
            if missing:
                raise ValueError(f"Structured issue missing fields: {', '.join(missing)}")
            persisted = Issue(audit_run=audit, agent_result=result, **{field: issue_data[field] for field in ISSUE_FIELDS})
            session.add(persisted)
            persisted_issues.append(persisted)
    session.flush()

    manual_issues = [
        issue for issue in persisted_issues if issue.human_required or issue.severity.lower() in MANUAL_SEVERITIES
    ]
    purely_safe_low_risk = bool(persisted_issues) and not manual_issues and all(
        issue.severity.lower() == "low" and issue.auto_fixable for issue in persisted_issues
    )

    if manual_issues or (persisted_issues and not purely_safe_low_risk):
        item.review_status = ReviewStatus.MANUAL_REQUIRED
        item.publish_status = PublishStatus.NOT_READY
        task_issues = manual_issues or persisted_issues
        for issue in task_issues:
            session.add(ReviewTask(content_item=item, issue=issue, task_type="RISK_REVIEW"))
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
        session.add(ReviewTask(content_item=item, task_type="REVIEW_FIX_PROPOSAL"))
        item.review_status = ReviewStatus.FIX_PROPOSED
        item.publish_status = PublishStatus.NOT_READY
    else:
        item.review_status = ReviewStatus.APPROVED
        item.publish_status = PublishStatus.READY

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

    payload = dict(payload or {})
    item = task.content_item
    latest = _latest_version(item)
    allowed = {
        "REVIEW_FIX_PROPOSAL": {"ACCEPT_SUGGESTION", "ACCEPT_EDITED", "REJECT_SUGGESTION"},
        "RISK_REVIEW": {"APPROVE_RISK", "REJECT_RISK"},
    }
    if decision not in allowed.get(task.task_type, set()):
        raise ValueError(f"Decision {decision} is invalid for {task.task_type}")

    if decision == "ACCEPT_SUGGESTION":
        if latest.source != "AI_PROPOSED":
            raise ValueError("No AI proposed version is available")
        session.add(_new_version(item, source="HUMAN_CONFIRMED", title=latest.title, body=latest.body, payload=latest.payload))
        item.review_status = ReviewStatus.APPROVED
        item.publish_status = PublishStatus.READY
    elif decision == "ACCEPT_EDITED":
        title, body = payload.get("title"), payload.get("body")
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError("ACCEPT_EDITED requires title and body")
        session.add(_new_version(item, source="HUMAN_EDITED", title=title, body=body, payload=latest.payload))
        item.review_status = ReviewStatus.APPROVED
        item.publish_status = PublishStatus.READY
    elif decision == "REJECT_SUGGESTION":
        item.review_status = ReviewStatus.MANUAL_REQUIRED
        item.publish_status = PublishStatus.NOT_READY
        session.add(ReviewTask(content_item=item, task_type="RISK_REVIEW"))
    elif decision == "APPROVE_RISK":
        title = payload.get("title", latest.title)
        body = payload.get("body", latest.body)
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError("APPROVE_RISK title and body must be strings")
        session.add(_new_version(item, source="HUMAN_APPROVED", title=title, body=body, payload=latest.payload))
        other_open_risk_tasks = any(
            candidate.id != task.id and candidate.task_type == "RISK_REVIEW" and candidate.status == "OPEN"
            for candidate in item.review_tasks
        )
        item.review_status = (
            ReviewStatus.MANUAL_REQUIRED if other_open_risk_tasks else ReviewStatus.APPROVED
        )
        item.publish_status = PublishStatus.NOT_READY if other_open_risk_tasks else PublishStatus.READY
    elif decision == "REJECT_RISK":
        item.review_status = ReviewStatus.REJECTED
        item.publish_status = PublishStatus.NOT_READY

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
    session.commit()
    session.refresh(human_decision)
    return human_decision
