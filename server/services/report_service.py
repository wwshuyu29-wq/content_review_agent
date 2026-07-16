from __future__ import annotations

from collections import Counter
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.models import AuditRun, Batch, ContentItem, Issue, Project, ReviewStatus, ReviewTask

DIMENSION_KEYS = {
    "CONTENT_QUALITY",
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "CAMPAIGN_EFFECTIVENESS",
}
DEPRECATED_PRESENTATION_RULE_IDS = {
    "TEST-COUNT-001",
    "TEST-EVIDENCE-001",
}
DEPRECATED_PRESENTATION_TERMS = ("证据", "测试", "实测", "亲测")


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _is_visible_report_issue(issue: Issue) -> bool:
    if issue.category in {"system", "system_suggestion"}:
        return False
    if issue.rule_id in DEPRECATED_PRESENTATION_RULE_IDS:
        return False
    searchable = " ".join(str(value or "") for value in (issue.rule_id, issue.category, issue.reason, issue.suggestion))
    return not any(term in searchable for term in DEPRECATED_PRESENTATION_TERMS)


def _issue_dimension_key(issue: Issue) -> str:
    raw_category = str(issue.category or "")
    if raw_category in DIMENSION_KEYS:
        return raw_category
    rule_id = str(issue.rule_id or "")
    searchable = " ".join(
        str(value or "")
        for value in (rule_id, raw_category, issue.reason, issue.suggestion, issue.field, issue.evidence_quote)
    )
    if rule_id.startswith("BRAND") or _contains_any(searchable, ("品牌", "官方名称", "产品名", "卖点口径")):
        return "BRAND"
    if rule_id.startswith("CLAIM") or _contains_any(searchable, ("合规", "绝对", "保证", "承诺", "夸大", "广告法")):
        return "COMPLIANCE"
    if _contains_any(searchable, ("功能", "能力", "路线", "规划", "导航", "产品准确", "事实错误", "讲错")):
        return "PRODUCT_ACCURACY"
    if _contains_any(searchable, ("传播", "卖点", "转化", "受众", "场景", "标题吸引")):
        return "CAMPAIGN_EFFECTIVENESS"
    return "CONTENT_QUALITY"


def build_report(session: Session, *, project_id: int, batch_id: Optional[int] = None) -> dict:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} does not exist")

    batch = None
    if batch_id is not None:
        batch = session.get(Batch, batch_id)
        if batch is None or batch.project_id != project_id:
            raise ValueError(f"Batch {batch_id} does not belong to project {project_id}")

    item_query = select(ContentItem).where(ContentItem.project_id == project_id)
    if batch_id is not None:
        item_query = item_query.where(ContentItem.batch_id == batch_id)
    items = list(session.scalars(item_query))
    item_ids = [item.id for item in items]

    issues = []
    active_tasks = []
    historical_issue_count = 0
    historical_task_count = 0
    if item_ids:
        latest_audits = (
            select(AuditRun.content_item_id, func.max(AuditRun.id).label("audit_id"))
            .where(AuditRun.content_item_id.in_(item_ids))
            .group_by(AuditRun.content_item_id)
            .subquery()
        )
        latest_audit_ids = select(latest_audits.c.audit_id)
        issues = list(session.scalars(select(Issue).where(Issue.audit_run_id.in_(latest_audit_ids))))
        active_tasks = list(session.scalars(
            select(ReviewTask).where(
                ReviewTask.content_item_id.in_(item_ids),
                ReviewTask.status == "OPEN",
                ReviewTask.audit_run_id.in_(latest_audit_ids),
            )
        ))
        historical_issue_count = session.scalar(
            select(func.count(Issue.id)).join(AuditRun).where(AuditRun.content_item_id.in_(item_ids))
        ) or 0
        historical_task_count = session.scalar(
            select(func.count(ReviewTask.id)).where(ReviewTask.content_item_id.in_(item_ids))
        ) or 0

    dimension_content_ids: dict[str, set[int]] = {}
    for issue in issues:
        if not _is_visible_report_issue(issue):
            continue
        dimension_content_ids.setdefault(_issue_dimension_key(issue), set()).add(issue.audit_run.content_item_id)

    manual_item_ids = {
        item.id for item in items if item.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
    }
    manual_item_ids.update(
        task.content_item_id for task in active_tasks if task.task_type in {"HUMAN_REVIEW", "BLOCK_REVIEW"}
    )
    return {
        "project": {"id": project.id, "name": project.name},
        "batch": {"id": batch.id, "name": batch.name} if batch is not None else None,
        "totals": {"contents": len(items), "issues": len(issues), "tasks": len(active_tasks)},
        "historical_totals": {"issues": historical_issue_count, "tasks": historical_task_count},
        "status_counts": dict(Counter(item.review_status.value for item in items)),
        "category_counts": {
            dimension: len(content_ids)
            for dimension, content_ids in sorted(dimension_content_ids.items())
        },
        "rule_counts": {},
        "manual_metrics": {
            "contents": len(manual_item_ids),
            "tasks": sum(task.task_type in {"HUMAN_REVIEW", "BLOCK_REVIEW"} for task in active_tasks),
            "rate": round(len(manual_item_ids) / len(items), 4) if items else 0.0,
        },
    }
