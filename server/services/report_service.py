from __future__ import annotations

from collections import Counter
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import AuditRun, Batch, ContentItem, Issue, Project, ReviewStatus, ReviewTask


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
    tasks = []
    if item_ids:
        issues = list(
            session.scalars(
                select(Issue)
                .join(AuditRun, Issue.audit_run_id == AuditRun.id)
                .where(AuditRun.content_item_id.in_(item_ids))
            )
        )
        tasks = list(session.scalars(select(ReviewTask).where(ReviewTask.content_item_id.in_(item_ids))))

    manual_item_ids = {
        item.id for item in items if item.review_status is ReviewStatus.MANUAL_REQUIRED
    }
    manual_item_ids.update(task.content_item_id for task in tasks if task.task_type == "RISK_REVIEW")
    result = {
        "project": {"id": project.id, "name": project.name},
        "batch": {"id": batch.id, "name": batch.name} if batch is not None else None,
        "totals": {"contents": len(items), "issues": len(issues), "tasks": len(tasks)},
        "status_counts": dict(Counter(item.review_status.value for item in items)),
        "category_counts": dict(Counter(issue.category for issue in issues)),
        "rule_counts": dict(Counter(issue.rule_id for issue in issues)),
        "manual_metrics": {
            "contents": len(manual_item_ids),
            "tasks": sum(task.task_type == "RISK_REVIEW" for task in tasks),
            "rate": round(len(manual_item_ids) / len(items), 4) if items else 0.0,
        },
    }
    return result
