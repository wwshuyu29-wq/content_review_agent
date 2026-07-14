from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import FormatStatus, PublishStatus, ReviewStatus


class OrmSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content_type: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name is required")
        return value


class ProjectRead(OrmSchema):
    id: int
    name: str
    code: Optional[str]
    content_type: Optional[str]
    description: Optional[str]
    current_rule_version_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class RuleVersionCreate(BaseModel):
    project_id: int
    version: int = Field(ge=1)
    dimension_standards: Dict[str, Any]
    project_facts: Dict[str, Any]
    structured_rules: Dict[str, Any]
    prompt_version: str = Field(min_length=1, max_length=100)

    @field_validator("prompt_version")
    @classmethod
    def validate_prompt_version(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt_version is required")
        return value


class RuleVersionRead(OrmSchema):
    id: int
    project_id: int
    version: int
    business_domain: Optional[str]
    document_type: Optional[str]
    project_code: Optional[str]
    content_type: Optional[str]
    package_version: Optional[str]
    dimension_standards: Dict[str, Any]
    project_facts: Dict[str, Any]
    structured_rules: Dict[str, Any]
    prompt_version: str
    created_at: datetime


class BatchCreate(BaseModel):
    project_id: int
    supplier_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class BatchRead(OrmSchema):
    id: int
    project_id: int
    supplier_id: str
    name: str
    status: str
    created_at: datetime


class ContentItemCreate(BaseModel):
    project_id: int
    batch_id: int
    external_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    format_status: FormatStatus = FormatStatus.PENDING
    review_status: ReviewStatus = ReviewStatus.NOT_STARTED
    publish_status: PublishStatus = PublishStatus.NOT_READY


class ContentItemRead(OrmSchema):
    id: int
    project_id: int
    batch_id: int
    external_id: str
    title: str
    format_status: FormatStatus
    review_status: ReviewStatus
    publish_status: PublishStatus
    created_at: datetime
    updated_at: datetime


class ContentVersionCreate(BaseModel):
    content_item_id: int
    version: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=500)
    body: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class ContentVersionRead(OrmSchema):
    id: int
    content_item_id: int
    version: int
    source: str
    title: str
    body: str
    payload: Dict[str, Any]
    created_at: datetime


class AuditRunCreate(BaseModel):
    content_item_id: int
    content_version_id: int
    rule_version_id: int
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=100)


class AuditRunRead(OrmSchema):
    id: int
    content_item_id: int
    content_version_id: int
    rule_version_id: int
    model: str
    prompt_version: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]


class AgentResultCreate(BaseModel):
    audit_run_id: int
    agent_name: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=50)
    raw_result: Dict[str, Any] = Field(default_factory=dict)


class AgentResultRead(OrmSchema):
    id: int
    audit_run_id: int
    agent_name: str
    status: str
    raw_result: Dict[str, Any]
    created_at: datetime


class IssueCreate(BaseModel):
    audit_run_id: int
    agent_result_id: Optional[int] = None
    rule_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    severity: str = Field(min_length=1, max_length=50)
    field: str = Field(min_length=1, max_length=100)
    evidence_quote: str
    reason: str
    suggestion: str
    auto_fixable: bool
    human_required: bool
    confidence: float = Field(ge=0.0, le=1.0)


class IssueRead(OrmSchema):
    id: int
    audit_run_id: int
    agent_result_id: Optional[int]
    rule_id: str
    category: str
    severity: str
    field: str
    evidence_quote: str
    reason: str
    suggestion: str
    auto_fixable: bool
    human_required: bool
    confidence: float
    created_at: datetime


class ReviewTaskCreate(BaseModel):
    content_item_id: int
    target_content_version_id: int
    audit_run_id: int
    issue_id: Optional[int] = None
    task_type: str = Field(min_length=1, max_length=100)
    assigned_to: Optional[str] = None


class ReviewTaskRead(OrmSchema):
    id: int
    content_item_id: int
    target_content_version_id: int
    audit_run_id: int
    issue_id: Optional[int]
    task_type: str
    status: str
    assigned_to: Optional[str]
    created_at: datetime
    closed_at: Optional[datetime]


class HumanDecisionCreate(BaseModel):
    review_task_id: int
    decision: str = Field(min_length=1, max_length=100)
    reviewer: str = Field(min_length=1, max_length=200)
    note: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class HumanDecisionRead(OrmSchema):
    id: int
    review_task_id: int
    decision: str
    reviewer: str
    note: Optional[str]
    payload: Dict[str, Any]
    created_at: datetime
