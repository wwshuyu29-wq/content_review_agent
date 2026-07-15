from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
import os
from threading import BoundedSemaphore, Lock
from typing import Any, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from scripts.text_review.reviewers.tech_media import TechMediaReviewer
from server.db import get_db_engine
from server.models import (
    AgentAuditProgress,
    AuditRun,
    BatchAuditJob,
    ContentItem,
    ManuscriptAuditJob,
    PublishStatus,
    ReviewStatus,
)
from server.services.review_service import has_valid_completed_audit, run_audit

_PUBLIC_ERROR = "审核过程中出现异常，请稍后重试或联系管理员。"
_MAX_WORKERS = max(1, int(os.environ.get("AUDIT_EXECUTOR_MAX_WORKERS", "1")))
_MAX_PENDING = max(_MAX_WORKERS, int(os.environ.get("AUDIT_EXECUTOR_MAX_PENDING", str(_MAX_WORKERS * 2))))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="audit-job")
_submission_slots = BoundedSemaphore(_MAX_PENDING)
_futures: set[Future[Any]] = set()
_futures_lock = Lock()


def _default_reviewer_factory() -> Any:
    return TechMediaReviewer()


_reviewer_factory: Callable[[], Any] = _default_reviewer_factory


def set_reviewer_factory(factory: Callable[[], Any]) -> None:
    global _reviewer_factory
    _reviewer_factory = factory


def _safe_error(_error: BaseException) -> str:
    return _PUBLIC_ERROR


def _load_job(session: Session, job_id: int) -> Optional[BatchAuditJob]:
    return session.scalar(
        select(BatchAuditJob)
        .where(BatchAuditJob.id == job_id)
        .options(
            selectinload(BatchAuditJob.manuscripts).selectinload(ManuscriptAuditJob.agents)
        )
    )


def _claim_queued_job(session: Session, job_id: int) -> bool:
    now = datetime.utcnow()
    claimed = session.execute(
        update(BatchAuditJob)
        .where(
            BatchAuditJob.id == job_id,
            BatchAuditJob.status == "QUEUED",
        )
        .values(
            status="RUNNING",
            started_at=now,
            heartbeat_at=now,
            error_summary=None,
        )
    )
    session.commit()
    return claimed.rowcount == 1


def _update_counters(job: BatchAuditJob) -> None:
    counts = Counter(manuscript.status for manuscript in job.manuscripts)
    job.completed_count = counts["COMPLETED"]
    job.failed_count = counts["FAILED"]
    job.skipped_count = counts["SKIPPED"]


def _release_partial_audit(session: Session, content_item_id: int) -> None:
    partials = list(
        session.scalars(
            select(AuditRun).where(
                AuditRun.content_item_id == content_item_id,
                AuditRun.status == "RUNNING",
            )
        )
    )
    now = datetime.utcnow()
    for audit in partials:
        audit.status = "FAILED"
        audit.review_key = None
        audit.completed_at = now
    item = session.get(ContentItem, content_item_id)
    if item is not None and item.review_status is ReviewStatus.AI_REVIEWING:
        item.review_status = ReviewStatus.HUMAN_REVIEW_REQUIRED
        item.publish_status = PublishStatus.NOT_READY


def _progress_callback(
    session: Session,
    job: BatchAuditJob,
    manuscript: ManuscriptAuditJob,
) -> Callable[..., None]:
    agents = {agent.agent_id: agent for agent in manuscript.agents}

    def record(event: str, **payload: Any) -> None:
        agent_id = str(payload["agent_id"])
        agent = agents[agent_id]
        now = datetime.utcnow()
        attempt = int(payload.get("attempt", agent.attempt_count or 1))
        job.current_content_item_id = manuscript.content_item_id
        job.current_agent_id = agent_id
        job.heartbeat_at = now
        agent.attempt_count = max(agent.attempt_count, attempt)

        if event == "agent_started":
            agent.status = "RUNNING"
            agent.started_at = agent.started_at or now
            agent.completed_at = None
            agent.error_summary = None
        elif event == "agent_retry":
            agent.status = "RUNNING"
        elif event in {"agent_completed", "agent_failed"}:
            result = payload.get("result")
            agent.status = "COMPLETED" if event == "agent_completed" else "FAILED"
            agent.completed_at = now
            if agent.started_at is not None:
                agent.duration_ms = max(0, int((now - agent.started_at).total_seconds() * 1000))
            agent.decision = getattr(result, "decision", None)
            agent.score = getattr(result, "score", None)
            agent.error_summary = _PUBLIC_ERROR if event == "agent_failed" else None
        else:
            raise ValueError(f"Unknown Agent progress event: {event}")
        session.commit()

    return record


def run_audit_job(job_id: int, reviewer_factory: Callable[[], Any]) -> None:
    """Run one persisted audit job with worker-owned resources.

    Manuscripts are deliberately sequential. Every externally observable state
    transition is committed before the next model request begins.
    """
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise TypeError("job_id must be an integer")

    engine = get_db_engine()
    with Session(engine, expire_on_commit=False) as session:
        if not _claim_queued_job(session, job_id):
            return
        job = _load_job(session, job_id)
        if job is None:
            return

        try:
            reviewer = reviewer_factory()

            for manuscript in job.manuscripts:
                if manuscript.status in {"COMPLETED", "SKIPPED"}:
                    continue

                item = session.get(ContentItem, manuscript.content_item_id)
                if item is None:
                    manuscript.status = "FAILED"
                    manuscript.completed_at = datetime.utcnow()
                    manuscript.error_summary = _PUBLIC_ERROR
                    _update_counters(job)
                    job.error_summary = _PUBLIC_ERROR
                    job.heartbeat_at = datetime.utcnow()
                    session.commit()
                    continue

                if (
                    item.versions
                    and item.project.current_rule_version_id is not None
                    and has_valid_completed_audit(
                        session,
                        item.versions[-1].id,
                        item.project.current_rule_version_id,
                    )
                ):
                    manuscript.status = "SKIPPED"
                    manuscript.completed_at = datetime.utcnow()
                    manuscript.error_summary = None
                    _update_counters(job)
                    job.heartbeat_at = datetime.utcnow()
                    session.commit()
                    continue

                manuscript.status = "RUNNING"
                manuscript.started_at = manuscript.started_at or datetime.utcnow()
                manuscript.completed_at = None
                manuscript.error_summary = None
                job.current_content_item_id = manuscript.content_item_id
                job.current_agent_id = None
                job.heartbeat_at = datetime.utcnow()
                session.commit()

                try:
                    run_audit(
                        session,
                        manuscript.content_item_id,
                        reviewer=reviewer,
                        model=job.model,
                        progress_callback=_progress_callback(session, job, manuscript),
                    )
                except Exception as error:
                    session.rollback()
                    job = _load_job(session, job_id)
                    if job is None:
                        return
                    manuscript = next(row for row in job.manuscripts if row.id == manuscript.id)
                    _release_partial_audit(session, manuscript.content_item_id)
                    manuscript.status = "FAILED"
                    manuscript.completed_at = datetime.utcnow()
                    manuscript.error_summary = _safe_error(error)
                    job.error_summary = _safe_error(error)
                else:
                    manuscript.status = "COMPLETED"
                    manuscript.completed_at = datetime.utcnow()
                    manuscript.error_summary = None

                _update_counters(job)
                job.current_agent_id = None
                job.heartbeat_at = datetime.utcnow()
                session.commit()

            _update_counters(job)
            job.status = "COMPLETED_WITH_ERRORS" if job.failed_count else "COMPLETED"
            job.active_key = None
            job.current_content_item_id = None
            job.current_agent_id = None
            job.completed_at = datetime.utcnow()
            job.heartbeat_at = job.completed_at
            session.commit()
        except Exception as error:
            session.rollback()
            job = _load_job(session, job_id)
            if job is not None and job.status in {"QUEUED", "RUNNING"}:
                job.status = "FAILED"
                job.active_key = None
                job.current_content_item_id = None
                job.current_agent_id = None
                job.completed_at = datetime.utcnow()
                job.heartbeat_at = job.completed_at
                job.error_summary = _safe_error(error)
                session.commit()


def _execute_submitted_job(job_id: int) -> None:
    try:
        run_audit_job(job_id, _reviewer_factory)
    finally:
        _submission_slots.release()


def _forget_future(future: Future[Any]) -> None:
    with _futures_lock:
        _futures.discard(future)


def submit_audit_job(job_id: int) -> None:
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise TypeError("job_id must be an integer")
    if not _submission_slots.acquire(blocking=False):
        raise RuntimeError("Audit executor queue is full")
    try:
        future = _executor.submit(_execute_submitted_job, job_id)
    except Exception:
        _submission_slots.release()
        raise
    if isinstance(future, Future):
        with _futures_lock:
            _futures.add(future)
        future.add_done_callback(_forget_future)


def wait_for_audit_jobs(timeout: Optional[float] = 10.0) -> None:
    with _futures_lock:
        pending = set(_futures)
    if pending:
        wait(pending, timeout=timeout)
