from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from scripts.text_review.reviewers.tech_media import AGENT_ORDER
from server.models import (
    AgentAuditProgress,
    Batch,
    BatchAuditJob,
    ContentItem,
    ManuscriptAuditJob,
)
from server.schemas import AuditJobProgressRead


ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")


@dataclass(frozen=True)
class ActiveJobResult:
    job: BatchAuditJob
    created: bool


def _active_key(batch_id: int) -> str:
    return f"batch:{batch_id}"


def _load_job(session: Session, job_id: int) -> Optional[BatchAuditJob]:
    return session.scalar(
        select(BatchAuditJob)
        .where(BatchAuditJob.id == job_id)
        .options(
            selectinload(BatchAuditJob.manuscripts).selectinload(ManuscriptAuditJob.agents)
        )
    )


def create_or_get_active_job(
    session: Session,
    batch_id: int,
    model: str,
    *,
    created_by_user_id: int | None = None,
) -> ActiveJobResult:
    """Create a complete progress tree or return the batch's existing active job.

    ``active_key`` is non-null only for non-terminal jobs. Its database uniqueness
    is the concurrency guard on both SQLite and PostgreSQL; the savepoint keeps an
    IntegrityError from rolling back unrelated work in the caller's transaction.
    """
    key = _active_key(batch_id)
    existing = session.scalar(
        select(BatchAuditJob)
        .where(BatchAuditJob.active_key == key)
        .options(
            selectinload(BatchAuditJob.manuscripts).selectinload(ManuscriptAuditJob.agents)
        )
    )
    if existing is not None:
        return ActiveJobResult(job=existing, created=False)

    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist")

    content_items = list(
        session.scalars(
            select(ContentItem)
            .where(ContentItem.batch_id == batch_id)
            .order_by(ContentItem.id)
        )
    )
    now = datetime.utcnow()
    job = BatchAuditJob(
        batch_id=batch.id,
        active_key=key,
        model=model,
        created_by_user_id=created_by_user_id,
        total_count=len(content_items),
        heartbeat_at=now,
    )
    for manuscript_position, content_item in enumerate(content_items, start=1):
        manuscript = ManuscriptAuditJob(
            content_item_id=content_item.id,
            position=manuscript_position,
        )
        manuscript.agents = [
            AgentAuditProgress(agent_id=agent_id, position=agent_position)
            for agent_position, agent_id in enumerate(AGENT_ORDER, start=1)
        ]
        job.manuscripts.append(manuscript)

    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        winner = session.scalar(
            select(BatchAuditJob)
            .where(BatchAuditJob.active_key == key)
            .options(
                selectinload(BatchAuditJob.manuscripts).selectinload(ManuscriptAuditJob.agents)
            )
        )
        if winner is None:
            raise
        return ActiveJobResult(job=winner, created=False)
    return ActiveJobResult(job=job, created=True)


def get_job_progress(session: Session, job_id: int) -> AuditJobProgressRead:
    job = _load_job(session, job_id)
    if job is None:
        raise ValueError(f"Audit job {job_id} does not exist")

    counts = Counter(manuscript.status for manuscript in job.manuscripts)
    current_manuscript = next(
        (
            manuscript
            for manuscript in job.manuscripts
            if manuscript.content_item_id == job.current_content_item_id
        ),
        None,
    )
    return AuditJobProgressRead.model_validate(
        {
            "id": job.id,
            "batch_id": job.batch_id,
            "model": job.model,
            "status": job.status,
            "total_count": len(job.manuscripts),
            "completed_count": counts["COMPLETED"],
            "failed_count": counts["FAILED"],
            "skipped_count": counts["SKIPPED"],
            "running_count": counts["RUNNING"],
            "pending_count": counts["PENDING"],
            "current_content_item_id": job.current_content_item_id,
            "current_agent_id": job.current_agent_id,
            "heartbeat_at": job.heartbeat_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "error_summary": job.error_summary,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "manuscripts": job.manuscripts,
            "current_agents": current_manuscript.agents if current_manuscript else [],
        }
    )


def interrupt_stale_jobs(session: Session, stale_before: datetime) -> int:
    """Mark abandoned active jobs terminal so their batches can be restarted."""
    stale_jobs = list(
        session.scalars(
            select(BatchAuditJob).where(
                BatchAuditJob.status.in_(ACTIVE_JOB_STATUSES),
                BatchAuditJob.heartbeat_at < stale_before,
            )
        )
    )
    interrupted_at = datetime.utcnow()
    for job in stale_jobs:
        job.status = "INTERRUPTED"
        job.active_key = None
        job.completed_at = interrupted_at
        job.error_summary = "审核任务因服务中断而停止，可重新发起审核。"
    session.flush()
    return len(stale_jobs)
