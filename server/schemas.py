from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import AssetKind, FormatStatus, PublishStatus, ReviewStatus


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
    package_digest: Optional[str]
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


class AgentAuditProgressRead(OrmSchema):
    id: int
    manuscript_job_id: int
    agent_id: str
    position: int
    status: str
    attempt_count: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_ms: Optional[int]
    decision: Optional[str]
    score: Optional[int]
    error_summary: Optional[str]


class ManuscriptAuditProgressRead(OrmSchema):
    id: int
    audit_job_id: int
    content_item_id: int
    position: int
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_summary: Optional[str]
    agents: List[AgentAuditProgressRead] = Field(default_factory=list)


class AuditJobProgressRead(OrmSchema):
    id: int
    batch_id: int
    model: str
    status: str
    total_count: int
    completed_count: int
    failed_count: int
    skipped_count: int
    running_count: int
    pending_count: int
    current_content_item_id: Optional[int]
    current_agent_id: Optional[str]
    heartbeat_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_summary: Optional[str]
    created_at: datetime
    updated_at: datetime
    manuscripts: List[ManuscriptAuditProgressRead] = Field(default_factory=list)
    current_agents: List[AgentAuditProgressRead] = Field(default_factory=list)


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
    review_key: Optional[str] = Field(default=None, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=100)


class AuditRunRead(OrmSchema):
    id: int
    content_item_id: int
    content_version_id: int
    rule_version_id: int
    review_key: Optional[str]
    model: str
    prompt_version: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]


class AgentResultCreate(BaseModel):
    audit_run_id: int
    agent_name: str = Field(min_length=1, max_length=100)
    agent_id: Optional[str] = None
    agent_version: Optional[str] = None
    decision: Optional[str] = None
    summary: Optional[str] = None
    score: Optional[int] = Field(default=None, ge=0, le=100)
    status: str = Field(min_length=1, max_length=50)
    raw_result: Dict[str, Any] = Field(default_factory=dict)


class AgentResultRead(OrmSchema):
    id: int
    audit_run_id: int
    agent_name: str
    agent_id: Optional[str]
    agent_version: Optional[str]
    decision: Optional[str]
    summary: Optional[str]
    score: Optional[int]
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
    evidence_start: Optional[int] = None
    evidence_end: Optional[int] = None
    evidence_asset_id: Optional[str] = None
    evidence_timestamp: Optional[str] = None
    source_reference: List[str] = Field(default_factory=list)
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
    evidence_start: Optional[int]
    evidence_end: Optional[int]
    evidence_asset_id: Optional[str]
    evidence_timestamp: Optional[str]
    source_reference: List[str] = Field(default_factory=list)
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
    issue_ids: List[int] = Field(default_factory=list)
    task_key: Optional[str]
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


class AssetCreate(BaseModel):
    content_item_id: int
    asset_id: str = Field(min_length=1, max_length=200)
    external_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    kind: AssetKind
    filename: str = Field(min_length=1, max_length=500)
    storage_key: Optional[str] = Field(default=None, max_length=1000)
    mime_type: Optional[str] = Field(default=None, max_length=200)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AssetRead(OrmSchema):
    id: int
    content_item_id: int
    asset_id: str
    external_id: Optional[str]
    kind: AssetKind
    filename: str
    storage_key: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]
    asset_metadata: Dict[str, Any]
    created_at: datetime


class TestCaseCreate(BaseModel):
    content_item_id: int
    content_version_id: int
    external_test_case_id: str = Field(min_length=1, max_length=200)
    claim: str = Field(min_length=1)
    command: str = Field(min_length=1)
    observed_result: str = Field(min_length=1)
    city: Optional[str] = None
    tested_at: Optional[datetime] = None
    app_version: Optional[str] = None
    device: Optional[str] = None
    operating_system: Optional[str] = None
    network_environment: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TestEvidenceRead(OrmSchema):
    id: int
    test_case_id: int
    asset_id: int
    asset: AssetRead


class TestCaseRead(OrmSchema):
    id: int
    content_item_id: int
    content_version_id: int
    external_test_case_id: str
    claim: str
    command: str
    observed_result: str
    city: Optional[str]
    tested_at: Optional[datetime]
    app_version: Optional[str]
    device: Optional[str]
    operating_system: Optional[str]
    network_environment: Optional[str]
    test_metadata: Dict[str, Any]
    evidence: List[TestEvidenceRead] = Field(default_factory=list)


class TestEvidenceCreate(BaseModel):
    test_case_id: int
    asset_id: int


class ImportConfirm(BaseModel):
    project_id: int = Field(gt=0)
    supplier_id: str = Field(min_length=1, max_length=200)
    batch_name: str = Field(min_length=1, max_length=200)

    @field_validator("supplier_id", "batch_name")
    @classmethod
    def strip_import_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("import identity cannot be blank")
        return value


class ImportTestPreviewRead(BaseModel):
    content_external_id: str
    external_test_case_id: str
    claim: Optional[str]
    command: Optional[str]
    observed_result: Optional[str]
    city: Optional[str]
    tested_at: Optional[str]
    app_version: Optional[str]
    device: Optional[str]
    operating_system: Optional[str]
    network_environment: Optional[str]
    evidence_filenames: List[str] = Field(default_factory=list)


class ImportRowPreviewRead(BaseModel):
    manuscript_index: int
    row_number: int
    normalized: Dict[str, Any]
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    valid: bool
    tests: List[ImportTestPreviewRead] = Field(default_factory=list)


class ImportPreviewRead(BaseModel):
    token: str
    rows: List[ImportRowPreviewRead]
    tests: List[ImportTestPreviewRead] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    total_count: int
    valid_count: int
    error_count: int
    test_count: int
    project_id: int
    project_code: str
    content_type: str
    package_version: str
    supplier_id: str
    batch_name: str


class ContentTableAgent(BaseModel):
    agent_id: str
    agent_name: str
    agent_version: Optional[str] = None
    decision: Optional[str] = None
    summary: Optional[str] = None
    score: Optional[int] = None
    status: str


class ContentTableRow(BaseModel):
    id: int
    project_id: int
    batch_id: int
    supplier_external_id: str
    campaign_theme: Optional[str]
    account_name: Optional[str]
    account_type: Optional[str]
    platform: Optional[str]
    original_title: str
    original_body: str
    final_title: str
    final_body: str
    body_summary: str
    image_filename: Optional[str]
    publish_time: Optional[str]
    note: Optional[str]
    row_number: Optional[int]
    format_status: FormatStatus
    review_status: ReviewStatus
    publish_status: PublishStatus
    issues: List[IssueRead] = Field(default_factory=list)
    issue_count: int
    highest_severity: Optional[str]
    categories: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    open_task_count: int
    open_task_types: List[str] = Field(default_factory=list)
    latest_audit_id: Optional[int]
    agents: List[ContentTableAgent] = Field(default_factory=list)
    media_url: Optional[str]
    test_count: int
    evidence_count: int
    evidence_status: str
