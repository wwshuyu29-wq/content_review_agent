from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, get_ident

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from scripts.text_review import schema
from scripts.text_review.reviewers.base import AgentReviewResult
from scripts.text_review.reviewers.tech_media import AGENT_ORDER, TechMediaReviewer
from server.db import Base, create_db_engine
from server.models import (
    AgentAuditProgress,
    Batch,
    BatchAuditJob,
    ContentItem,
    ManuscriptAuditJob,
    Project,
)
from server.seed import seed_default_project
from server.services.content_service import submit_batch
from server.services.audit_job_service import (
    create_or_get_active_job,
    get_job_progress,
    interrupt_stale_jobs,
)


def make_engine(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'audit-jobs.db'}")
    Base.metadata.create_all(engine)
    return engine


def create_batch(session: Session, item_count: int = 10) -> Batch:
    project = Project(name="进度测试项目")
    batch = Batch(project=project, supplier_id="supplier", name="十篇稿件")
    for index in range(item_count):
        batch.content_items.append(
            ContentItem(
                project=project,
                external_id=f"content-{index + 1}",
                title=f"稿件 {index + 1}",
            )
        )
    session.add(batch)
    session.commit()
    return batch


def test_create_job_persists_default_manuscript_and_agent_progress(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        batch = create_batch(session)

        job = create_or_get_active_job(session, batch.id, "gpt-5.6-sol")
        session.commit()

        assert job.status == "QUEUED"
        assert job.total_count == 10
        assert job.completed_count == 0
        assert job.failed_count == 0
        assert job.skipped_count == 0
        assert job.active_key == f"batch:{batch.id}"
        assert job.heartbeat_at is not None
        assert [row.position for row in job.manuscripts] == list(range(1, 11))
        assert all(row.status == "PENDING" for row in job.manuscripts)
        assert all(tuple(agent.agent_id for agent in row.agents) == AGENT_ORDER for row in job.manuscripts)
        assert all(agent.status == "PENDING" and agent.attempt_count == 0 for row in job.manuscripts for agent in row.agents)

    inspector = inspect(engine)
    assert {"batch_audit_jobs", "manuscript_audit_jobs", "agent_audit_progress"} <= set(
        inspector.get_table_names()
    )
    indexed_foreign_keys = {
        table: {
            column
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
        }
        for table in ("batch_audit_jobs", "manuscript_audit_jobs", "agent_audit_progress")
    }
    assert "batch_id" in indexed_foreign_keys["batch_audit_jobs"]
    assert {"audit_job_id", "content_item_id"} <= indexed_foreign_keys["manuscript_audit_jobs"]
    assert "manuscript_job_id" in indexed_foreign_keys["agent_audit_progress"]


def test_create_or_get_active_job_reuses_one_job_across_concurrent_sessions(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        batch_id = create_batch(session, item_count=2).id

    barrier = Barrier(2)

    def create_job() -> int:
        with Session(engine) as session:
            barrier.wait()
            job = create_or_get_active_job(session, batch_id, "gpt-5.6-sol")
            session.commit()
            return job.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        job_ids = list(executor.map(lambda _index: create_job(), range(2)))

    assert len(set(job_ids)) == 1
    with Session(engine) as session:
        assert len(list(session.scalars(select(BatchAuditJob)))) == 1
        assert len(list(session.scalars(select(ManuscriptAuditJob)))) == 2
        assert len(list(session.scalars(select(AgentAuditProgress)))) == 2 * len(AGENT_ORDER)


def test_get_job_progress_calculates_counters_from_manuscript_rows(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        batch = create_batch(session, item_count=5)
        job = create_or_get_active_job(session, batch.id, "model")
        session.flush()
        statuses = ("COMPLETED", "COMPLETED", "FAILED", "SKIPPED", "RUNNING")
        for manuscript, status in zip(job.manuscripts, statuses):
            manuscript.status = status
        job.completed_count = 99
        job.failed_count = 99
        job.skipped_count = 99
        job.current_content_item_id = job.manuscripts[-1].content_item_id
        job.current_agent_id = AGENT_ORDER[2]
        session.commit()

        progress = get_job_progress(session, job.id)
        payload = progress.model_dump(mode="json")

    assert payload["total_count"] == 5
    assert payload["completed_count"] == 2
    assert payload["failed_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["running_count"] == 1
    assert payload["pending_count"] == 0
    assert payload["current_content_item_id"] == job.current_content_item_id
    assert payload["current_agent_id"] == AGENT_ORDER[2]
    assert len(payload["manuscripts"]) == 5
    assert len(payload["current_agents"]) == len(AGENT_ORDER)


def test_get_job_progress_replaces_technical_errors_without_mutating_persisted_diagnostics(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    def technical_error(level: str) -> str:
        return (
            f"{level}: POST https://oneapi.example.internal/v1/chat/completions failed; "
            f"Authorization: Bearer sk-fake-{level}-key; "
            'raw response={"error":{"message":"upstream body","code":500}}; '
            "Traceback (most recent call last): worker.py line 42 RuntimeError"
        )

    batch_error = technical_error("batch")
    manuscript_error = technical_error("manuscript")
    agent_error = technical_error("agent")
    safe_message = "审核过程中出现异常，请稍后重试或联系管理员。"

    with Session(engine) as session:
        batch = create_batch(session, item_count=1)
        job = create_or_get_active_job(session, batch.id, "model")
        manuscript = job.manuscripts[0]
        agent = manuscript.agents[0]
        job.error_summary = batch_error
        manuscript.error_summary = manuscript_error
        agent.error_summary = agent_error
        job.current_content_item_id = manuscript.content_item_id
        session.commit()
        job_id = job.id
        manuscript_id = manuscript.id
        agent_id = agent.id

        payload = get_job_progress(session, job_id).model_dump(mode="json")

        assert payload["error_summary"] == safe_message
        assert payload["manuscripts"][0]["error_summary"] == safe_message
        assert payload["manuscripts"][0]["agents"][0]["error_summary"] == safe_message
        assert payload["current_agents"][0]["error_summary"] == safe_message
        serialized = str(payload)
        for technical_fragment in (
            "https://",
            "sk-fake",
            "raw upstream response body",
            "Traceback",
            "worker.py",
            "RuntimeError",
        ):
            assert technical_fragment not in serialized

        assert session.get(BatchAuditJob, job_id).error_summary == batch_error
        assert session.get(ManuscriptAuditJob, manuscript_id).error_summary == manuscript_error
        assert session.get(AgentAuditProgress, agent_id).error_summary == agent_error


def test_interrupt_stale_jobs_releases_active_key_for_a_new_job(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    now = datetime.utcnow()
    with Session(engine) as session:
        batch = create_batch(session, item_count=1)
        stale = create_or_get_active_job(session, batch.id, "old-model")
        stale.status = "RUNNING"
        stale.heartbeat_at = now - timedelta(minutes=30)
        stale.manuscripts[0].status = "RUNNING"
        session.commit()

        interrupted = interrupt_stale_jobs(session, now - timedelta(minutes=5))
        session.commit()

        assert interrupted == 1
        assert stale.status == "INTERRUPTED"
        assert stale.active_key is None
        assert stale.completed_at is not None
        replacement = create_or_get_active_job(session, batch.id, "new-model")
        session.commit()
        assert replacement.id != stale.id
        assert replacement.active_key == f"batch:{batch.id}"


def test_interrupt_stale_jobs_leaves_recent_and_terminal_jobs_unchanged(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    now = datetime.utcnow()
    with Session(engine) as session:
        recent_batch = create_batch(session, item_count=1)
        recent = create_or_get_active_job(session, recent_batch.id, "recent")
        recent.status = "RUNNING"
        recent.heartbeat_at = now
        recent.active_key = f"batch:{recent_batch.id}"

        terminal_batch = Batch(project=recent_batch.project, supplier_id="supplier", name="terminal")
        terminal_batch.content_items.append(
            ContentItem(project=recent_batch.project, external_id="terminal", title="terminal")
        )
        session.add(terminal_batch)
        session.flush()
        terminal = create_or_get_active_job(session, terminal_batch.id, "done")
        terminal.status = "COMPLETED"
        terminal.active_key = None
        terminal.heartbeat_at = now - timedelta(hours=1)
        session.commit()

        assert interrupt_stale_jobs(session, now - timedelta(minutes=5)) == 0
        session.commit()
        assert recent.status == "RUNNING"
        assert terminal.status == "COMPLETED"


def configure_worker_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from server.db import get_db_engine, reset_db_resources

    database_url = f"sqlite:///{tmp_path / 'worker.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    reset_db_resources()
    engine = get_db_engine()
    Base.metadata.create_all(engine)
    return engine


def create_auditable_batch(session: Session, item_count: int = 2) -> Batch:
    project = seed_default_project(session)
    return submit_batch(
        session,
        project_id=project.id,
        supplier_id="worker-test",
        name="后台审核",
        contents=[
            {
                "external_id": f"worker-{index + 1}",
                "title": f"稿件 {index + 1}",
                "body": f"这是第 {index + 1} 篇用于后台审核的完整正文。",
                "payload": {"platform": "xiaohongshu"},
            }
            for index in range(item_count)
        ],
    )


class PassingReviewer:
    name = "passing-worker-reviewer"

    def review_structured(self, _row, _standards):
        return [
            {
                "agent_name": agent_id,
                "agent_id": agent_id,
                "agent_version": "tech-media-v1",
                "decision": "PASS",
                "summary": "通过",
                "score": 90,
                "status": "PASS",
                "issues": [],
                "raw_result": {},
            }
            for agent_id in AGENT_ORDER
        ]


def test_submit_audit_job_submits_only_integer_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.services import audit_executor_service

    submitted = []
    completed = Future()
    completed.set_result(None)

    def capture_submission(function, *args, **kwargs):
        submitted.append((function, args, kwargs))
        return completed

    monkeypatch.setattr(audit_executor_service._executor, "submit", capture_submission)

    audit_executor_service.submit_audit_job(41)

    assert len(submitted) == 1
    function, args, kwargs = submitted[0]
    assert function is audit_executor_service._execute_submitted_job
    assert args == (41,)
    assert kwargs == {}
    audit_executor_service._submission_slots.release()


def test_submit_returns_while_worker_commits_agent_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.db import reset_db_resources
    from server.services import audit_executor_service

    engine = configure_worker_database(tmp_path, monkeypatch)
    block_brand = Event()
    brand_started = Event()
    factory_threads = []
    caller_thread = get_ident()

    class BlockingLLM:
        def chat_json(self, prompt, _response_model):
            agent_id = next(agent for agent in AGENT_ORDER if f"Specialist: {agent}" in prompt)
            if agent_id == "BRAND":
                brand_started.set()
                assert block_brand.wait(5)
            return AgentReviewResult(
                agent_id=agent_id,
                agent_version="tech-media-v1",
                decision="PASS",
                summary="通过",
                score=90,
                confidence=0.9,
                issues=[],
            ).model_dump(mode="json")

    def reviewer_factory():
        factory_threads.append(get_ident())
        return TechMediaReviewer(llm=BlockingLLM())

    monkeypatch.setattr(audit_executor_service, "_reviewer_factory", reviewer_factory)
    try:
        with Session(engine) as request_session:
            batch = create_auditable_batch(request_session, item_count=1)
            job = create_or_get_active_job(request_session, batch.id, "model")
            request_session.commit()
            job_id = job.id

            audit_executor_service.submit_audit_job(job_id)
            assert brand_started.wait(5), "submission should return while the reviewer is blocked"

            with Session(engine) as polling_session:
                visible = polling_session.get(BatchAuditJob, job_id)
                manuscript = polling_session.scalar(
                    select(ManuscriptAuditJob).where(ManuscriptAuditJob.audit_job_id == job_id)
                )
                agents = list(polling_session.scalars(
                    select(AgentAuditProgress)
                    .where(AgentAuditProgress.manuscript_job_id == manuscript.id)
                    .order_by(AgentAuditProgress.position)
                ))
                assert visible.status == "RUNNING"
                assert manuscript.status == "RUNNING"
                assert agents[0].status == "COMPLETED"
                assert agents[0].attempt_count == 1
                assert agents[1].status == "RUNNING"
                assert visible.current_agent_id == "BRAND"

        assert factory_threads and factory_threads[0] != caller_thread
    finally:
        block_brand.set()
        audit_executor_service.wait_for_audit_jobs()
        reset_db_resources()

    with Session(engine) as polling_session:
        finished = polling_session.get(BatchAuditJob, job_id)
        assert finished.status == "COMPLETED"
        assert finished.active_key is None
        assert finished.current_content_item_id is None
        assert finished.current_agent_id is None


def test_worker_continues_after_manuscript_failure_and_sanitizes_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.db import reset_db_resources
    from server.services.audit_executor_service import run_audit_job

    engine = configure_worker_database(tmp_path, monkeypatch)

    class FailsFirstReviewer(PassingReviewer):
        def review_structured(self, row, standards):
            if row[schema.COL_ID] == "worker-1":
                raise RuntimeError(
                    "POST https://gateway.internal failed Authorization: Bearer sk-secret Traceback raw response"
                )
            return super().review_structured(row, standards)

    with Session(engine) as session:
        batch = create_auditable_batch(session, item_count=2)
        job = create_or_get_active_job(session, batch.id, "model")
        session.commit()
        job_id = job.id

    try:
        run_audit_job(job_id, FailsFirstReviewer)
        with Session(engine) as session:
            job = session.get(BatchAuditJob, job_id)
            manuscripts = list(session.scalars(
                select(ManuscriptAuditJob)
                .where(ManuscriptAuditJob.audit_job_id == job_id)
                .order_by(ManuscriptAuditJob.position)
            ))
            assert [manuscript.status for manuscript in manuscripts] == ["FAILED", "COMPLETED"]
            assert job.status == "COMPLETED_WITH_ERRORS"
            assert job.active_key is None
            assert job.failed_count == 1
            assert job.completed_count == 1
            assert manuscripts[0].error_summary == "审核过程中出现异常，请稍后重试或联系管理员。"
            assert "gateway" not in str(job.error_summary)
            assert "secret" not in str(job.error_summary)
    finally:
        reset_db_resources()


def test_worker_skips_content_with_valid_matching_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.db import reset_db_resources
    from server.services.audit_executor_service import run_audit_job
    from server.services.review_service import run_audit

    engine = configure_worker_database(tmp_path, monkeypatch)
    with Session(engine) as session:
        batch = create_auditable_batch(session, item_count=1)
        run_audit(session, batch.content_items[0].id, reviewer=PassingReviewer())
        job = create_or_get_active_job(session, batch.id, "model")
        session.commit()
        job_id = job.id

    try:
        run_audit_job(job_id, PassingReviewer)
        with Session(engine) as session:
            job = session.get(BatchAuditJob, job_id)
            manuscript = session.scalar(
                select(ManuscriptAuditJob).where(ManuscriptAuditJob.audit_job_id == job_id)
            )
            assert manuscript.status == "SKIPPED"
            assert job.status == "COMPLETED"
            assert job.skipped_count == 1
    finally:
        reset_db_resources()


def test_application_startup_interrupts_stale_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server import main
    from server.db import get_db_engine, reset_db_resources

    database_url = f"sqlite:///{tmp_path / 'startup.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "audit-admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "audit-admin-password")
    monkeypatch.setenv("SESSION_SECRET", "audit-job-test-session-secret-32-bytes")
    monkeypatch.setenv("AUDIT_JOB_STALE_SECONDS", "300")
    reset_db_resources()
    engine = get_db_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = create_batch(session, item_count=1)
        job = create_or_get_active_job(session, batch.id, "model")
        job.status = "RUNNING"
        job.heartbeat_at = datetime.utcnow() - timedelta(minutes=10)
        session.commit()
        job_id = job.id

    try:
        with TestClient(main.app):
            pass
        with Session(engine) as session:
            interrupted = session.get(BatchAuditJob, job_id)
            assert interrupted is not None
            assert interrupted.status == "INTERRUPTED"
            assert interrupted.active_key is None
    finally:
        reset_db_resources()
