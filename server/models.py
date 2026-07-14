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
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    FIX_PROPOSED = "FIX_PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


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
    status: Mapped[str] = mapped_column(String(50), default="SUBMITTED", nullable=False)
    import_token: Mapped[Optional[str]] = mapped_column(String(128), unique=True)

    project: Mapped[Project] = relationship(back_populates="batches")
    content_items: Mapped[List["ContentItem"]] = relationship(
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


class AuditRun(TimestampMixin, Base):
    __tablename__ = "audit_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True, nullable=False)
    content_version_id: Mapped[int] = mapped_column(ForeignKey("content_versions.id"), index=True, nullable=False)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"), index=True, nullable=False)
    review_key: Mapped[Optional[str]] = mapped_column(String(200), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    content_item: Mapped[ContentItem] = relationship(back_populates="audit_runs")
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


class ReviewTask(TimestampMixin, Base):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey("content_items.id"), index=True, nullable=False)
    target_content_version_id: Mapped[int] = mapped_column(
        ForeignKey("content_versions.id"), index=True, nullable=False
    )
    audit_run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id"), index=True, nullable=False)
    issue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("issues.id"), unique=True)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(200))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    content_item: Mapped[ContentItem] = relationship(back_populates="review_tasks")
    target_content_version: Mapped[ContentVersion] = relationship(back_populates="review_tasks")
    audit_run: Mapped[AuditRun] = relationship(back_populates="review_tasks")
    issue: Mapped[Optional[Issue]] = relationship(back_populates="review_task")
    human_decisions: Mapped[List["HumanDecision"]] = relationship(
        back_populates="review_task", cascade="all, delete-orphan"
    )


class HumanDecision(TimestampMixin, Base):
    __tablename__ = "human_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_task_id: Mapped[int] = mapped_column(ForeignKey("review_tasks.id"), index=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    review_task: Mapped[ReviewTask] = relationship(back_populates="human_decisions")


def _reject_version_update(mapper, _connection, target) -> None:
    state = inspect(target)
    if any(state.attrs[column.key].history.has_changes() for column in mapper.columns):
        raise InvalidRequestError(f"{target.__class__.__name__} records are immutable")


def _reject_version_delete(_mapper, _connection, target) -> None:
    raise InvalidRequestError(f"{target.__class__.__name__} records are immutable")


for immutable_model in (RuleVersion, ContentVersion):
    event.listen(immutable_model, "before_update", _reject_version_update)
    event.listen(immutable_model, "before_delete", _reject_version_delete)
