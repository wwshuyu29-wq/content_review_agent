from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event, inspect
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class FormatStatus(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


class ReviewStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    AI_REVIEWING = "AI_REVIEWING"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    SUPPLIER_REVISION_REQUIRED = "SUPPLIER_REVISION_REQUIRED"
    AUTO_FIX_PENDING = "AUTO_FIX_PENDING"
    PASSED = "PASSED"
    PASSED_WITH_SUGGESTIONS = "PASSED_WITH_SUGGESTIONS"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


class AssetKind(str, Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    SCREENSHOT = "SCREENSHOT"
    SCREEN_RECORDING = "SCREEN_RECORDING"
    TEST_LOG = "TEST_LOG"


class PublishStatus(str, Enum):
    NOT_READY = "NOT_READY"
    READY = "READY"
    PUBLISHED = "PUBLISHED"


def enum_column(enum_type: type[Enum], default: Enum) -> Mapped[Any]:
    return mapped_column(
        SqlEnum(
            enum_type,
            native_enum=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=default,
        nullable=False,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="REVIEWER", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reviewer_backend: Mapped[Optional[str]] = mapped_column(String(50))
    oneapi_model: Mapped[Optional[str]] = mapped_column(String(200))
    oneapi_key_ciphertext: Mapped[Optional[str]] = mapped_column(Text)

    sessions: Mapped[List["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    uploaded_batches: Mapped[List["Batch"]] = relationship(back_populates="uploaded_by_user")
    audit_jobs: Mapped[List["BatchAuditJob"]] = relationship(back_populates="created_by_user")
    audit_runs: Mapped[List["AuditRun"]] = relationship(back_populates="created_by_user")
    human_decisions: Mapped[List["HumanDecision"]] = relationship(back_populates="reviewer_user")


class UserSession(TimestampMixin, Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(200), unique=True, index=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    current_rule_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("rule_versions.id", use_alter=True, name="fk_projects_current_rule_version"),
        index=True,
    )

    rule_versions: Mapped[List["RuleVersion"]] = relationship(
        back_populates="project",
        foreign_keys="RuleVersion.project_id",
        cascade="all, delete-orphan",
    )
    current_rule_version: Mapped[Optional["RuleVersion"]] = relationship(
        foreign_keys=[current_rule_version_id], post_update=True
    )
    batches: Mapped[List["Batch"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    content_items: Mapped[List["ContentItem"]] = relationship(back_populates="project")


class RuleVersion(TimestampMixin, Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        UniqueConstraint("project_id", "package_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    business_domain: Mapped[Optional[str]] = mapped_column(String(200))
    document_type: Mapped[Optional[str]] = mapped_column(String(100))
    project_code: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    package_version: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    package_digest: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    dimension_standards: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    project_facts: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    structured_rules: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)

    project: Mapped[Project] = relationship(back_populates="rule_versions", foreign_keys=[project_id])
    audit_runs: Mapped[List["AuditRun"]] = relationship(back_populates="rule_version")


class Batch(TimestampMixin, Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    supplier_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_type: Mapped[Optional[str]] = mapped_column(String(200))
    owner_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(50), default="SUBMITTED", nullable=False)
    import_token: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    review_brief: Mapped[Optional[str]] = mapped_column(Text)
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)

    project: Mapped[Project] = relationship(back_populates="batches")
    uploaded_by_user: Mapped[Optional[User]] = relationship(back_populates="uploaded_batches")
    content_items: Mapped[List["ContentItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )
    audit_jobs: Mapped[List["BatchAuditJob"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ContentItem(TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (UniqueConstraint("batch_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    format_status: Mapped[FormatStatus] = enum_column(FormatStatus, FormatStatus.PENDING)
    review_status: Mapped[ReviewStatus] = enum_column(ReviewStatus, ReviewStatus.NOT_STARTED)
    publish_status: Mapped[PublishStatus] = enum_column(PublishStatus, PublishStatus.NOT_READY)

    project: Mapped[Project] = relationship(back_populates="content_items")
    batch: Mapped[Batch] = relationship(back_populates="content_items")
    versions: Mapped[List["ContentVersion"]] = relationship(
        back_populates="content_item", cascade="all, delete-orphan", order_by="ContentVersion.version"
    )
    audit_runs: Mapped[List["AuditRun"]] = relationship(back_populates="content_item")
    review_tasks: Mapped[List["ReviewTask"]] = relationship(back_populates="content_item")
    assets: Mapped[List["Asset"]] = relationship(
        back_populates="content_item", cascade="all, delete-orphan", order_by="Asset.id"
    )
    test_cases: Mapped[List["TestCase"]] = relationship(
        back_populates="content_item", cascade="all, delete-orphan", order_by="TestCase.id"
    )
    manuscript_audit_jobs: Mapped[List["ManuscriptAuditJob"]] = relationship(
        back_populates="content_item"
    )


class ContentVersion(TimestampMixin, Base):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("content_item_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    content_item: Mapped[ContentItem] = relationship(back_populates="versions")
    audit_runs: Mapped[List["AuditRun"]] = relationship(back_populates="content_version")
    review_tasks: Mapped[List["ReviewTask"]] = relationship(back_populates="target_content_version")
    test_cases: Mapped[List["TestCase"]] = relationship(back_populates="content_version")


class BatchAuditJob(TimestampMixin, Base):
    __tablename__ = "batch_audit_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True, nullable=False)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    active_key: Mapped[Optional[str]] = mapped_column(String(200), unique=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="QUEUED", index=True, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_content_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("content_items.id"), index=True
    )
    current_agent_id: Mapped[Optional[str]] = mapped_column(String(100))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)

    batch: Mapped[Batch] = relationship(back_populates="audit_jobs")
    created_by_user: Mapped[Optional[User]] = relationship(back_populates="audit_jobs")
    manuscripts: Mapped[List["ManuscriptAuditJob"]] = relationship(
        back_populates="audit_job",
        cascade="all, delete-orphan",
        order_by="ManuscriptAuditJob.position",
    )


class ManuscriptAuditJob(TimestampMixin, Base):
    __tablename__ = "manuscript_audit_jobs"
    __table_args__ = (
        UniqueConstraint("audit_job_id", "content_item_id"),
        UniqueConstraint("audit_job_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_job_id: Mapped[int] = mapped_column(
        ForeignKey("batch_audit_jobs.id"), index=True, nullable=False
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)

    audit_job: Mapped[BatchAuditJob] = relationship(back_populates="manuscripts")
    content_item: Mapped[ContentItem] = relationship(back_populates="manuscript_audit_jobs")
    agents: Mapped[List["AgentAuditProgress"]] = relationship(
        back_populates="manuscript_job",
        cascade="all, delete-orphan",
        order_by="AgentAuditProgress.position",
    )


class AgentAuditProgress(TimestampMixin, Base):
    __tablename__ = "agent_audit_progress"
    __table_args__ = (
        UniqueConstraint("manuscript_job_id", "agent_id"),
        UniqueConstraint("manuscript_job_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    manuscript_job_id: Mapped[int] = mapped_column(
        ForeignKey("manuscript_audit_jobs.id"), index=True, nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    decision: Mapped[Optional[str]] = mapped_column(String(50))
    score: Mapped[Optional[int]] = mapped_column(Integer)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)

    manuscript_job: Mapped[ManuscriptAuditJob] = relationship(back_populates="agents")


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("content_item_id", "asset_id"),
        UniqueConstraint("content_item_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True, nullable=False)
    asset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(200))
    kind: Mapped[AssetKind] = enum_column(AssetKind, AssetKind.IMAGE)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[Optional[str]] = mapped_column(String(1000))
    mime_type: Mapped[Optional[str]] = mapped_column(String(200))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    asset_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)

    content_item: Mapped[ContentItem] = relationship(back_populates="assets")
    evidence: Mapped[List["TestEvidence"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class TestCase(TimestampMixin, Base):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("content_item_id", "external_test_case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True, nullable=False)
    content_version_id: Mapped[int] = mapped_column(ForeignKey("content_versions.id"), index=True, nullable=False)
    external_test_case_id: Mapped[str] = mapped_column(String(200), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    observed_result: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(200))
    tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    app_version: Mapped[Optional[str]] = mapped_column(String(200))
    device: Mapped[Optional[str]] = mapped_column(String(500))
    operating_system: Mapped[Optional[str]] = mapped_column(String(500))
    network_environment: Mapped[Optional[str]] = mapped_column(String(500))
    test_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)

    content_item: Mapped[ContentItem] = relationship(back_populates="test_cases")
    content_version: Mapped[ContentVersion] = relationship(back_populates="test_cases")
    evidence: Mapped[List["TestEvidence"]] = relationship(
        back_populates="test_case", cascade="all, delete-orphan", order_by="TestEvidence.id"
    )


class TestEvidence(TimestampMixin, Base):
    __tablename__ = "test_evidence"
    __table_args__ = (UniqueConstraint("test_case_id", "asset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id"), index=True, nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True, nullable=False)

    test_case: Mapped[TestCase] = relationship(back_populates="evidence")
    asset: Mapped[Asset] = relationship(back_populates="evidence")


class AuditRun(TimestampMixin, Base):
    __tablename__ = "audit_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True, nullable=False)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    content_version_id: Mapped[int] = mapped_column(ForeignKey("content_versions.id"), index=True, nullable=False)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"), index=True, nullable=False)
    review_key: Mapped[Optional[str]] = mapped_column(String(200), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    content_item: Mapped[ContentItem] = relationship(back_populates="audit_runs")
    created_by_user: Mapped[Optional[User]] = relationship(back_populates="audit_runs")
    content_version: Mapped[ContentVersion] = relationship(back_populates="audit_runs")
    rule_version: Mapped[RuleVersion] = relationship(back_populates="audit_runs")
    agent_results: Mapped[List["AgentResult"]] = relationship(
        back_populates="audit_run", cascade="all, delete-orphan", order_by="AgentResult.id"
    )
    issues: Mapped[List["Issue"]] = relationship(back_populates="audit_run", cascade="all, delete-orphan")
    review_tasks: Mapped[List["ReviewTask"]] = relationship(back_populates="audit_run")


class AgentResult(TimestampMixin, Base):
    __tablename__ = "agent_results"
    __table_args__ = (
        UniqueConstraint("audit_run_id", "agent_name"),
        UniqueConstraint("audit_run_id", "agent_id", "agent_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(String(100))
    agent_version: Mapped[Optional[str]] = mapped_column(String(100))
    decision: Mapped[Optional[str]] = mapped_column(String(50))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_result: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    audit_run: Mapped[AuditRun] = relationship(back_populates="agent_results")
    issues: Mapped[List["Issue"]] = relationship(back_populates="agent_result")


class Issue(TimestampMixin, Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id"), index=True, nullable=False)
    agent_result_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_results.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_start: Mapped[Optional[int]] = mapped_column(Integer)
    evidence_end: Mapped[Optional[int]] = mapped_column(Integer)
    evidence_asset_id: Mapped[Optional[str]] = mapped_column(String(200))
    evidence_timestamp: Mapped[Optional[str]] = mapped_column(String(100))
    source_reference: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    auto_fixable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    human_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    audit_run: Mapped[AuditRun] = relationship(back_populates="issues")
    agent_result: Mapped[Optional[AgentResult]] = relationship(back_populates="issues")
    review_task: Mapped[Optional["ReviewTask"]] = relationship(back_populates="issue", uselist=False)
    task_links: Mapped[List["ReviewTaskIssue"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan"
    )
    review_tasks: Mapped[List["ReviewTask"]] = relationship(
        secondary="review_task_issues", back_populates="issues", viewonly=True
    )


class ReviewTaskIssue(TimestampMixin, Base):
    __tablename__ = "review_task_issues"
    __table_args__ = (UniqueConstraint("review_task_id", "issue_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    review_task_id: Mapped[int] = mapped_column(ForeignKey("review_tasks.id"), index=True, nullable=False)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True, nullable=False)

    review_task: Mapped["ReviewTask"] = relationship(back_populates="issue_links")
    issue: Mapped[Issue] = relationship(back_populates="task_links")


class ReviewTask(TimestampMixin, Base):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True, nullable=False)
    target_content_version_id: Mapped[int] = mapped_column(
        ForeignKey("content_versions.id"), index=True, nullable=False
    )
    audit_run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id"), index=True, nullable=False)
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("issues.id"), unique=True)
    task_key: Mapped[Optional[str]] = mapped_column(String(300), unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(200))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    content_item: Mapped[ContentItem] = relationship(back_populates="review_tasks")
    target_content_version: Mapped[ContentVersion] = relationship(back_populates="review_tasks")
    audit_run: Mapped[AuditRun] = relationship(back_populates="review_tasks")
    issue: Mapped[Optional[Issue]] = relationship(back_populates="review_task")
    issue_links: Mapped[List[ReviewTaskIssue]] = relationship(
        back_populates="review_task", cascade="all, delete-orphan"
    )
    issues: Mapped[List[Issue]] = relationship(
        secondary="review_task_issues", back_populates="review_tasks", viewonly=True
    )
    human_decisions: Mapped[List["HumanDecision"]] = relationship(
        back_populates="review_task", cascade="all, delete-orphan"
    )

    @property
    def issue_ids(self) -> list[int]:
        ids = {issue.id for issue in self.issues}
        if self.issue_id is not None:
            ids.add(self.issue_id)
        return sorted(ids)


class HumanDecision(TimestampMixin, Base):
    __tablename__ = "human_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_task_id: Mapped[int] = mapped_column(ForeignKey("review_tasks.id"), index=True, nullable=False)
    reviewer_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    review_task: Mapped[ReviewTask] = relationship(back_populates="human_decisions")
    reviewer_user: Mapped[Optional[User]] = relationship(back_populates="human_decisions")


def _reject_version_update(mapper, _connection, target) -> None:
    state = inspect(target)
    if any(state.attrs[column.key].history.has_changes() for column in mapper.columns):
        raise InvalidRequestError(f"{target.__class__.__name__} records are immutable")


def _reject_version_delete(_mapper, _connection, target) -> None:
    raise InvalidRequestError(f"{target.__class__.__name__} records are immutable")


for immutable_model in (RuleVersion, ContentVersion):
    event.listen(immutable_model, "before_update", _reject_version_update)
    event.listen(immutable_model, "before_delete", _reject_version_delete)
