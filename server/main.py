"""FastAPI entry point for the database-backed content review workflow."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from scripts.text_review.reviewer import get_reviewer
from scripts.text_review.reviewers.llm import get_llm
from scripts.text_review.reviewers.tech_media import TechMediaReviewer
from server.db import Base, ensure_schema_upgrades, get_db_engine, get_session
from server.services.audit_executor_service import set_reviewer_factory, submit_audit_job
from server.services.audit_job_service import (
    create_or_get_active_job,
    get_job_progress,
    interrupt_stale_jobs,
)
logger = logging.getLogger(__name__)

from server.models import (
    AgentResult,
    AuditRun,
    FormatStatus,
    PublishStatus,
    ReviewStatus,
    TestCase,
    TestEvidence,
    Batch,
    BatchAuditJob,
    ContentItem,
    ContentVersion,
    HumanDecision,
    Issue,
    Project,
    ReviewTask,
    RuleVersion,
    User,
    UserSession,
)
from server.schemas import (
    AgentResultRead,
    AuditJobProgressRead,
    AuditJobStartRead,
    AuditRunRead,
    ContentTableAgent,
    ContentTableRow,
    ImportConfirm,
    ImportPreviewRead,
    BatchRead,
    ContentItemRead,
    ContentVersionRead,
    HumanDecisionRead,
    IssueRead,
    ProjectCreate,
    ProjectRead,
    ReviewTaskRead,
    RuleVersionRead,
    TestCaseRead,
)
from server.seed import seed_default_project
from server.services.auth_service import (
    SESSION_COOKIE_NAME,
    authenticate_credentials,
    authenticate_request,
    create_session,
    csrf_token_for_session,
    decrypt_secret,
    encrypt_secret,
    ensure_initial_admin,
    ensure_team_users,
    hash_password,
    normalize_username,
    require_admin,
    require_csrf,
    require_user,
    revoke_user_sessions,
    set_user_active,
    trusted_public_origin_values,
    validate_csrf_request,
)
from server.services.content_service import submit_batch
from server.services.excel_import_service import PreviewIdentity, build_import_template, preview_import, confirm_import as confirm_excel_import
from server.services.excel_export_service import export_batch
from server.services.report_service import build_report
from server.services.review_service import resolve_task, run_audit, run_batch_audit_once
from server.services.standard_package_service import load_standard_package, publish_standard_package
from server.services.severity_service import highest_severity


REPO_DIR = Path(__file__).resolve().parents[1]
ALLOWED_IMAGE_TYPES = {
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXCEL_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EVIDENCE_ZIP_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_BRIEF_UPLOAD_BYTES = 5 * 1024 * 1024
AGENT_ORDER = ("CONTENT_QUALITY", "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "CAMPAIGN_EFFECTIVENESS")
UPLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_CONFIG = {"reviewer": "heuristic", "model": ""}
DEFAULT_TEAM_MODEL = "GPT 5.6 SOL"
SUPPORTED_REVIEWERS = {"heuristic", "oneapi", "ernie"}


def _data_dir() -> Path:
    return Path(os.environ.get("CR_DATA_DIR", str(REPO_DIR / "data")))


def _uploads_dir() -> Path:
    return _data_dir() / "uploads"


def _config_path() -> Path:
    return _data_dir() / "config.json"


def _load_config() -> Dict[str, str]:
    path = _config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as stream:
        saved = json.load(stream)
    return {key: str(saved.get(key, default)) for key, default in DEFAULT_CONFIG.items()}


def _validated_config(config: Dict[str, Any]) -> Dict[str, str]:
    validated = ConfigInput.model_validate(config)
    reviewer = validated.reviewer
    model = validated.model
    if reviewer is None or model is None:
        raise ValueError("reviewer and model configuration fields are required")
    if reviewer == "oneapi" and not model:
        raise ValueError("model is required for oneapi reviewer")
    return {"reviewer": reviewer, "model": model}


def _save_config(config: Dict[str, str]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)


def get_audit_reviewer() -> Any:
    config = _validated_config(_load_config())
    if config["model"]:
        if config["reviewer"] == "oneapi":
            os.environ["ONEAPI_MODEL"] = config["model"]
        elif config["reviewer"] == "ernie":
            os.environ["ERNIE_MODEL"] = config["model"]
    return TechMediaReviewer(llm=get_llm(config["reviewer"]))


def _call_llm_factory(backend: str, **kwargs: Any) -> Any:
    try:
        return get_llm(backend, **kwargs)
    except TypeError:
        return get_llm(backend)


def _config_for_user(user: User) -> Dict[str, str]:
    fallback = _load_config()
    reviewer = user.reviewer_backend or fallback["reviewer"]
    model = user.oneapi_model or fallback["model"]
    return {"reviewer": reviewer, "model": model}


def _user_oneapi_key(user: User) -> str:
    if user.oneapi_key_ciphertext:
        return decrypt_secret(user.oneapi_key_ciphertext)
    return os.environ.get("ONEAPI_KEY", "")


def get_audit_reviewer_for_user(user: User) -> Any:
    config = _validated_config(_config_for_user(user))
    api_key = _user_oneapi_key(user) if config["reviewer"] == "oneapi" else None
    llm = _call_llm_factory(config["reviewer"], model=config["model"] or None, api_key=api_key)
    return TechMediaReviewer(llm=llm)


def get_audit_job_reviewer(session: Session, job: BatchAuditJob) -> Any:
    if job.created_by_user_id is None:
        return get_audit_reviewer()
    user = session.get(User, job.created_by_user_id)
    if user is None:
        return get_audit_reviewer()
    return get_audit_reviewer_for_user(user)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _data_dir().mkdir(parents=True, exist_ok=True)
    _uploads_dir().mkdir(parents=True, exist_ok=True)
    if not _config_path().exists():
        _save_config(dict(DEFAULT_CONFIG))
    engine = get_db_engine()
    Base.metadata.create_all(engine)
    ensure_schema_upgrades(engine)
    with Session(engine) as session:
        ensure_initial_admin(session)
        ensure_team_users(session)
        seed_default_project(session)
        stale_seconds = max(1, int(os.environ.get("AUDIT_JOB_STALE_SECONDS", "300")))
        interrupt_stale_jobs(session, datetime.utcnow() - timedelta(seconds=stale_seconds))
        session.commit()
    set_reviewer_factory(get_audit_job_reviewer)
    yield


app = FastAPI(title="内容审核后端", version="2.0", lifespan=lifespan)

PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
SAFE_METHODS = {"GET", "HEAD"}


@app.middleware("http")
async def enforce_api_authentication(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or not path.startswith("/api/"):
        return await call_next(request)
    try:
        with Session(get_db_engine()) as session:
            authenticate_request(request, session)
            if request.method not in SAFE_METHODS:
                validate_csrf_request(request)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=trusted_public_origin_values(),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


def get_request_audit_reviewer(request: Request, user: User = Depends(require_user)) -> Any:
    override = app.dependency_overrides.get(get_audit_reviewer)
    if override is not None:
        return override()
    return get_audit_reviewer_for_user(user)


class OrmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(OrmResponse):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool


class AuthResponse(BaseModel):
    user: UserRead
    csrf_token: str


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=1024)
    role: str = "REVIEWER"

    @field_validator("username")
    @classmethod
    def normalize_required_username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("display_name")
    @classmethod
    def strip_required_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value must not be blank")
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"ADMIN", "REVIEWER"}:
            raise ValueError("role must be ADMIN or REVIEWER")
        return normalized


class AdminUserUpdate(BaseModel):
    is_active: bool


class PasswordResetInput(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class RuleVersionPackageInput(BaseModel):
    project_code: str = Field(min_length=1, max_length=200)
    package_version: str = Field(min_length=1, max_length=50)


class ProjectBriefInput(BaseModel):
    description: str = Field(min_length=1, max_length=5000)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description must not be blank")
        return value


class ProjectDetail(ProjectRead):
    current_rule_version: Optional[RuleVersionRead] = None
    rule_versions: List[RuleVersionRead] = Field(default_factory=list)


class ContentSummary(ContentItemRead):
    versions: List[ContentVersionRead] = Field(default_factory=list)


class AuditDetail(AuditRunRead):
    agent_results: List[AgentResultRead] = Field(default_factory=list)
    issues: List[IssueRead] = Field(default_factory=list)


class ContentDetail(ContentSummary):
    latest_audit: Optional[AuditDetail] = None
    open_tasks: List[ReviewTaskRead] = Field(default_factory=list)


class BatchDetail(BatchRead):
    content_count: int
    contents: List[ContentSummary] = Field(default_factory=list)


class TaskResolveInput(BaseModel):
    decision: str = Field(min_length=1, max_length=100)
    reviewer: str = Field(min_length=1, max_length=200)
    note: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class ConfigInput(BaseModel):
    reviewer: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    clear_key: bool = False

    @field_validator("reviewer", "model", "api_key")
    @classmethod
    def strip_value(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None

    @field_validator("reviewer")
    @classmethod
    def validate_reviewer(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in SUPPORTED_REVIEWERS:
            raise ValueError("reviewer must be one of heuristic, oneapi, ernie")
        return value

    @model_validator(mode="after")
    def validate_oneapi_model(self) -> "ConfigInput":
        if self.reviewer == "oneapi" and self.model is not None and not self.model:
            raise ValueError("model is required for oneapi reviewer")
        return self


class ConfigResponse(BaseModel):
    reviewer: str
    model: str
    key_set: bool


class DashboardMonthMetrics(BaseModel):
    month: str
    uploaded_count: int
    audit_started_count: int
    human_decision_count: int


class DashboardWorkloadRow(BaseModel):
    user_id: int
    username: str
    display_name: str
    months: List[DashboardMonthMetrics]


class DashboardBatchQuality(BaseModel):
    batch_id: int
    batch_name: str
    total_count: int
    passed_count: int
    pass_rate: float


class DashboardQuality(BaseModel):
    total_count: int
    passed_count: int
    pass_rate: float
    batches: List[DashboardBatchQuality]


class DashboardProjectQuality(BaseModel):
    project_id: int
    project_name: str
    total_count: int
    passed_count: int
    pass_rate: float


class DashboardIssueManuscript(BaseModel):
    content_id: int
    title: str
    severity: str
    reason: str


class DashboardIssueCluster(BaseModel):
    category: str
    issue_count: int
    manuscript_count: int
    high_count: int
    manuscripts: List[DashboardIssueManuscript]


class DashboardMonthlyReview(BaseModel):
    month: str
    reviewed_count: int


class DashboardSupplierQuality(BaseModel):
    supplier_name: str
    project_names: List[str]
    total_count: int
    passed_count: int
    pass_rate: float


class DashboardOverview(BaseModel):
    month: str
    workload: List[DashboardWorkloadRow]
    quality: DashboardQuality
    project_quality: List[DashboardProjectQuality]
    monthly_reviews: List[DashboardMonthlyReview]
    supplier_quality: List[DashboardSupplierQuality]
    issue_clusters: List[DashboardIssueCluster]


class BatchAuditItemResult(BaseModel):
    content_id: int
    status: str
    audit_run_id: Optional[int] = None
    error: Optional[str] = None


class BatchAuditResponse(BaseModel):
    batch_id: int
    audited: int
    audit_run_ids: List[int]
    results: List[BatchAuditItemResult]


SAFE_SERVICE_MESSAGES = {
    "审核任务暂时无法启动，请稍后重试。",
    "提交信息有误，请检查后重试。",
    "当前内容状态不允许重复审核，请刷新后重试。",
}

DEPRECATED_PRESENTATION_RULE_IDS = {
    "TEST-COUNT-001",
    "TEST-EVIDENCE-001",
}
DEPRECATED_PRESENTATION_TERMS = ("证据", "测试", "实测", "亲测")
DASHBOARD_DIMENSIONS = {
    "CONTENT_QUALITY",
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "CAMPAIGN_EFFECTIVENESS",
}


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _is_visible_review_issue(issue: Issue) -> bool:
    if issue.category in {"system", "system_suggestion"}:
        return False
    if issue.rule_id in DEPRECATED_PRESENTATION_RULE_IDS:
        return False
    searchable = " ".join(str(value or "") for value in (issue.rule_id, issue.category, issue.reason, issue.suggestion))
    return not any(term in searchable for term in DEPRECATED_PRESENTATION_TERMS)


def _issue_dimension_key(issue: Issue) -> str:
    raw_category = str(issue.category or "")
    if raw_category in DASHBOARD_DIMENSIONS:
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


_SEVERITY_WEIGHT = {"CRITICAL": 5, "HIGH": 4, "UNKNOWN": 4, "MEDIUM": 3, "MID": 3, "LOW": 2, "NONE": 1}


def _issue_display_priority(issue: Issue) -> tuple[int, int, int]:
    severity_score = _SEVERITY_WEIGHT.get(str(issue.severity or "").upper(), 0)
    return (
        1 if issue.human_required else 0,
        severity_score,
        int(issue.confidence * 100),
    )


def _display_review_issues(issues: Iterable[Issue], *, include_low: bool = False) -> list[Issue]:
    selected: Dict[str, Issue] = {}
    for issue in issues:
        if not _is_visible_review_issue(issue):
            continue
        if not include_low and str(issue.severity or "").upper() == "LOW":
            continue
        dimension = _issue_dimension_key(issue)
        current = selected.get(dimension)
        if current is None or _issue_display_priority(issue) > _issue_display_priority(current):
            selected[dimension] = issue
    return sorted(selected.values(), key=lambda issue: (-_issue_display_priority(issue)[1], _issue_dimension_key(issue)))


def _not_found(entity: str, entity_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail="请求的数据不存在，请刷新后重试。")


def _service_error(error: ValueError) -> HTTPException:
    raw_message = str(error)
    logger.warning("service operation failed: %s", raw_message, exc_info=True)
    if raw_message in SAFE_SERVICE_MESSAGES:
        status, message = 422, raw_message
    elif "does not exist" in raw_message or "does not belong" in raw_message:
        status, message = 404, "请求的数据不存在，请刷新后重试。"
    elif "open review tasks" in raw_message or "terminal" in raw_message or "already been audited" in raw_message:
        status, message = 409, "当前内容状态不允许重复审核，请刷新后重试。"
    else:
        status, message = 422, "提交信息有误，请检查后重试。"
    return HTTPException(status_code=status, detail=message)


def _content_summary(item: ContentItem) -> ContentSummary:
    return ContentSummary.model_validate(item)


def _audit_detail(audit: AuditRun) -> AuditDetail:
    return AuditDetail(
        **AuditRunRead.model_validate(audit).model_dump(),
        agent_results=[AgentResultRead.model_validate(result) for result in audit.agent_results],
        issues=[IssueRead.model_validate(issue) for issue in _display_review_issues(audit.issues, include_low=True)],
    )


def _content_detail(item: ContentItem) -> ContentDetail:
    audits = sorted(item.audit_runs, key=lambda audit: audit.id)
    return ContentDetail(
        **ContentItemRead.model_validate(item).model_dump(),
        versions=[ContentVersionRead.model_validate(version) for version in item.versions],
        latest_audit=_audit_detail(audits[-1]) if audits else None,
        open_tasks=[ReviewTaskRead.model_validate(task) for task in item.review_tasks if task.status == "OPEN"],
    )


def _content_query():
    return select(ContentItem).options(
        selectinload(ContentItem.versions),
        selectinload(ContentItem.review_tasks),
        selectinload(ContentItem.audit_runs).selectinload(AuditRun.agent_results),
        selectinload(ContentItem.audit_runs).selectinload(AuditRun.issues),
        selectinload(ContentItem.test_cases).selectinload(TestCase.evidence).selectinload(TestEvidence.asset),
    )


def _secure_session_cookie() -> bool:
    configured = os.environ.get("SESSION_COOKIE_SECURE")
    if configured is not None:
        return configured.strip().lower() not in {"0", "false", "no", "off"}
    return os.environ.get("ENVIRONMENT", "").strip().lower() in {"production", "prod"}


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: LoginInput, response: Response, session: Session = Depends(get_session)):
    user = authenticate_credentials(session, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    secrets = create_session(session, user)
    session.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        secrets.session_token,
        max_age=int((secrets.expires_at - datetime.utcnow()).total_seconds()),
        httponly=True,
        secure=_secure_session_cookie(),
        samesite="lax",
        path="/",
    )
    return AuthResponse(user=UserRead.model_validate(user), csrf_token=secrets.csrf_token)


@app.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    _user: User = Depends(require_csrf),
    session: Session = Depends(get_session),
):
    auth_session_id = getattr(request.state.auth_session, "id", None)
    auth_session = session.get(UserSession, auth_session_id) if auth_session_id is not None else None
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.utcnow()
        session.commit()
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=_secure_session_cookie(),
        samesite="lax",
        path="/",
    )
    response.status_code = 204


@app.get("/api/auth/me", response_model=AuthResponse)
def auth_me(request: Request, user: User = Depends(require_user)):
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return AuthResponse(
        user=UserRead.model_validate(user),
        csrf_token=csrf_token_for_session(session_token),
    )


@app.get("/api/admin/users", response_model=List[UserRead])
def list_users(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return list(session.scalars(select(User).order_by(User.id)))


@app.post(
    "/api/admin/users",
    response_model=UserRead,
    status_code=201,
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
def create_user(payload: AdminUserCreate, session: Session = Depends(get_session)):
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from error
    session.refresh(user)
    return user


@app.patch(
    "/api/admin/users/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
def update_user(user_id: int, payload: AdminUserUpdate, session: Session = Depends(get_session)):
    try:
        user = set_user_active(session, user_id, payload.is_active)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    if user is None:
        session.rollback()
        raise _not_found("User", user_id)
    session.commit()
    session.refresh(user)
    return user


@app.post(
    "/api/admin/users/{user_id}/reset-password",
    status_code=204,
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
def reset_user_password(
    user_id: int,
    payload: PasswordResetInput,
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if user is None:
        raise _not_found("User", user_id)
    user.password_hash = hash_password(payload.password)
    user.session_version += 1
    revoke_user_sessions(session, user)
    session.commit()
    return Response(status_code=204)


@app.get("/api/projects", response_model=List[ProjectRead])
def list_projects(session: Session = Depends(get_session)):
    return list(session.scalars(select(Project).order_by(Project.id)))


@app.post("/api/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)):
    project = Project(
        name=payload.name.strip(),
        code=payload.code.strip() if payload.code else None,
        content_type=payload.content_type.strip() if payload.content_type else None,
        description=payload.description,
    )
    session.add(project)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Project name already exists") from error
    session.refresh(project)
    return project


@app.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, session: Session = Depends(get_session)):
    project = session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.rule_versions), selectinload(Project.current_rule_version))
    )
    if project is None:
        raise _not_found("Project", project_id)
    return ProjectDetail(
        **ProjectRead.model_validate(project).model_dump(),
        current_rule_version=project.current_rule_version,
        rule_versions=sorted(project.rule_versions, key=lambda version: version.version),
    )


@app.patch(
    "/api/projects/{project_id}/brief",
    response_model=ProjectRead,
    dependencies=[Depends(require_csrf)],
)
def update_project_brief(
    project_id: int,
    payload: ProjectBriefInput,
    _user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    project = session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    project.description = payload.description
    session.commit()
    session.refresh(project)
    return project


@app.get("/api/projects/{project_id}/rule-versions", response_model=List[RuleVersionRead])
def list_rule_versions(project_id: int, session: Session = Depends(get_session)):
    if session.get(Project, project_id) is None:
        raise _not_found("Project", project_id)
    return list(
        session.scalars(
            select(RuleVersion).where(RuleVersion.project_id == project_id).order_by(RuleVersion.version)
        )
    )


@app.post("/api/projects/{project_id}/rule-versions", response_model=RuleVersionRead)
def publish_rule_version_package(
    project_id: int,
    payload: RuleVersionPackageInput,
    session: Session = Depends(get_session),
):
    project = session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    if not project.code or not project.content_type:
        raise HTTPException(
            status_code=422,
            detail="Project must have code and content_type before publishing a standard package",
        )
    if payload.project_code != project.code:
        raise HTTPException(status_code=422, detail="project_code does not match project identity")
    try:
        package = load_standard_package(REPO_DIR / "data" / "standards", project.code, payload.package_version)
        version = publish_standard_package(session, project.id, package)
        session.commit()
        session.refresh(version)
        return version
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _parse_contents(contents: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(contents)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="contents must be valid JSON") from error
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or not parsed or not all(isinstance(row, dict) for row in parsed):
        raise HTTPException(status_code=422, detail="contents must be a non-empty object or array of objects")
    return parsed


async def _save_uploads(rows: List[Dict[str, Any]], files: Optional[List[UploadFile]]) -> List[Path]:
    uploads = files or []
    if len(uploads) != len(rows):
        raise HTTPException(status_code=422, detail="Provide exactly one file per content row, in row order")

    saved_paths: List[Path] = []
    try:
        for index, upload in enumerate(uploads):
            if not upload.filename:
                raise HTTPException(status_code=422, detail="Each content row requires a named file")
            suffix = Path(upload.filename).suffix.lower()
            if suffix not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(status_code=422, detail=f"Unsupported image format: {suffix}")
            if upload.content_type not in ALLOWED_IMAGE_TYPES[suffix]:
                raise HTTPException(status_code=422, detail=f"Unsupported image MIME type: {upload.content_type}")
            payload = rows[index].setdefault("payload", {})
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="content payload must be an object")

            filename = f"{uuid.uuid4().hex}{suffix}"
            path = _uploads_dir() / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            saved_paths.append(path)
            size = 0
            with path.open("wb") as stream:
                while True:
                    chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Image exceeds 20MB limit")
                    stream.write(chunk)
            payload["media"] = filename
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    return saved_paths




@app.get("/api/import-template")
def download_import_template():
    return Response(
        build_import_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=tech-media-import-template.xlsx"},
    )


async def _save_excel_upload(upload: UploadFile, directory: Path, allowed: set[str], limit: int) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported upload suffix")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with path.open("xb") as stream:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="Upload exceeds size limit")
                stream.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


async def _read_brief_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".txt", ".md", ".docx"}:
        raise HTTPException(status_code=422, detail="Brief 文件仅支持 .docx、.txt 或 .md")
    data = await upload.read(MAX_BRIEF_UPLOAD_BYTES + 1)
    if len(data) > MAX_BRIEF_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Brief 文件不能超过 5 MiB")
    if suffix == ".docx":
        return _extract_docx_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Brief 文件必须使用 UTF-8 编码") from exc


def _extract_docx_text(data: bytes) -> str:
    try:
        with ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            document_names = ["word/document.xml"]
            document_names.extend(sorted(name for name in names if name.startswith("word/header") and name.endswith(".xml")))
            document_names.extend(sorted(name for name in names if name.startswith("word/footer") and name.endswith(".xml")))
            paragraphs: List[str] = []
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for name in document_names:
                if name not in names:
                    continue
                root = ET.fromstring(archive.read(name))
                for paragraph in root.findall(".//w:p", namespace):
                    pieces: List[str] = []
                    for node in paragraph.iter():
                        tag = node.tag.rsplit("}", 1)[-1]
                        if tag == "t" and node.text:
                            pieces.append(node.text)
                        elif tag == "tab":
                            pieces.append("\t")
                        elif tag in {"br", "cr"}:
                            pieces.append("\n")
                    line = "".join(pieces).strip()
                    if line:
                        paragraphs.append(line)
    except (BadZipFile, ET.ParseError, KeyError, OSError) as exc:
        raise HTTPException(status_code=422, detail="Brief Word 文件解析失败") from exc
    text = "\n".join(paragraphs).strip()
    if not text:
        raise HTTPException(status_code=422, detail="Brief Word 文件未解析到文本")
    return text


def _evidence_trigger_terms(project: Project) -> tuple[str, ...]:
    structured = project.current_rule_version.structured_rules if project.current_rule_version else {}
    evidence = structured.get("evidence_requirements", {}).get("evidence_requirements", [])
    configured = tuple(
        term.strip() for requirement in evidence
        for term in requirement.get("trigger_terms", [])
        if isinstance(term, str) and term.strip()
    )
    if configured:
        return configured
    for rule in structured.get("rules", []):
        if rule.get("matcher") == "evidence_required":
            terms = tuple(term.strip() for term in rule.get("trigger_terms", []) if isinstance(term, str) and term.strip())
            if terms:
                return terms
    return ("亲测", "实测", "自用")


def _validated_tech_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    if project.content_type != "TECH_MEDIA_REVIEW":
        raise HTTPException(status_code=422, detail="Project content_type must be TECH_MEDIA_REVIEW")
    rule = project.current_rule_version
    if (
        rule is None or rule.project_id != project.id
        or rule.project_code != project.code
        or rule.content_type != project.content_type
        or not rule.package_version or not rule.package_digest
    ):
        raise HTTPException(status_code=422, detail="Project has no valid current rule snapshot")
    return project


@app.post("/api/imports/preview", response_model=ImportPreviewRead)
async def preview_excel_import(
    project_id: int = Form(...), supplier_id: str = Form(...), batch_name: str = Form(...),
    project_type: str = Form(""), owner_name: str = Form(""),
    review_brief: str = Form(""),
    excel_file: UploadFile = File(...), evidence_zip: Optional[UploadFile] = File(None),
    brief_file: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
):
    project = _validated_tech_project(session, project_id)
    supplier_id = supplier_id.strip()
    batch_name = batch_name.strip()
    project_type = project_type.strip() or project.name
    owner_name = owner_name.strip() or supplier_id
    if not supplier_id or not batch_name:
        raise HTTPException(status_code=422, detail="supplier_id and batch_name are required")
    identity = PreviewIdentity(
        project_id=project.id, project_code=project.code, content_type=project.content_type,
        package_version=project.current_rule_version.package_version,
        supplier_id=supplier_id, batch_name=batch_name, project_type=project_type, owner_name=owner_name,
    )
    root = _data_dir() / "import-previews"
    temp = root / f"upload-{uuid.uuid4().hex}"
    try:
        brief_text = review_brief
        if brief_file is not None:
            uploaded_brief = await _read_brief_upload(brief_file)
            brief_text = f"{brief_text.strip()}\n\n{uploaded_brief.strip()}".strip() if brief_text.strip() else uploaded_brief
        xlsx = await _save_excel_upload(excel_file, temp, {".xlsx"}, MAX_EXCEL_UPLOAD_BYTES)
        zip_path = None
        if evidence_zip is not None:
            raise ValueError("当前仅支持文字审核，不支持媒体 ZIP 或证据 ZIP")
        result = preview_import(
            xlsx, zip_path, root, identity=identity, review_brief=brief_text,
            trigger_terms=_evidence_trigger_terms(project),
        )
        return ImportPreviewRead(
            token=result.token,
            rows=[{**row.__dict__, "tests": [test.__dict__ for test in row.tests]} for row in result.rows],
            tests=[test.__dict__ for test in result.test_cases], errors=result.errors,
            warnings=result.warnings, total_count=result.total_count,
            valid_count=result.valid_count, error_count=result.error_count,
            test_count=result.test_count, review_brief=result.review_brief,
            brief_summary=result.brief_summary, **identity.__dict__,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        shutil.rmtree(temp, ignore_errors=True)


@app.post("/api/imports/{token}/confirm", response_model=BatchDetail)
def confirm_excel_import_endpoint(
    token: str,
    payload: ImportConfirm,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
    reviewer: Any = Depends(get_request_audit_reviewer),
):
    _validated_tech_project(session, payload.project_id)
    try:
        batch = confirm_excel_import(
            session,
            token,
            payload.project_id,
            payload.supplier_id,
            payload.batch_name,
            project_type=payload.project_type,
            owner_name=payload.owner_name,
            uploaded_by_user_id=user.id,
        )
        return BatchDetail(
            **BatchRead.model_validate(batch).model_dump(), content_count=len(batch.content_items),
            contents=[_content_summary(item) for item in batch.content_items],
        )
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/batches/{batch_id}/export")
def export_excel_batch(batch_id: int, session: Session = Depends(get_session)):
    try:
        data = export_batch(session, batch_id)
    except ValueError as error:
        raise _not_found("Batch", batch_id) from error
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=batch-{batch_id}.xlsx"},
    )

@app.get("/api/batches", response_model=List[BatchRead])
def list_batches(
    project_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    query = select(Batch).order_by(Batch.id.desc())
    if project_id is not None:
        query = query.where(Batch.project_id == project_id)
    return list(session.scalars(query))


def _batch_with_project(session: Session, batch_id: int) -> Optional[Batch]:
    return session.scalar(
        select(Batch).join(Project, Batch.project_id == Project.id).where(Batch.id == batch_id)
    )


@app.post(
    "/api/batches/{batch_id}/audit-jobs",
    response_model=AuditJobStartRead,
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def start_audit_job(
    batch_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    batch = _batch_with_project(session, batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    config = _validated_config(_config_for_user(user))
    try:
        job_model = config["model"] or config["reviewer"]
        try:
            result = create_or_get_active_job(
                session,
                batch.id,
                job_model,
                created_by_user_id=user.id,
            )
        except TypeError as error:
            if "created_by_user_id" not in str(error):
                raise
            result = create_or_get_active_job(session, batch.id, job_model)
        session.commit()
        job, created = result.job, result.created
        job_id, status = job.id, job.status
    except ValueError as error:
        session.rollback()
        raise _service_error(error) from error

    if created:
        try:
            submit_audit_job(job_id)
        except Exception:
            with Session(get_db_engine()) as recovery_session:
                queued = recovery_session.get(BatchAuditJob, job_id)
                if queued is not None and queued.status == "QUEUED":
                    queued.status = "FAILED"
                    queued.active_key = None
                    queued.completed_at = datetime.utcnow()
                    queued.error_summary = "审核任务暂时无法启动，请稍后重试。"
                    recovery_session.commit()
            raise HTTPException(status_code=503, detail="审核任务暂时无法启动，请稍后重试。")

    return AuditJobStartRead(job_id=job_id, batch_id=batch.id, status=status)


@app.get("/api/audit-jobs/{job_id}", response_model=AuditJobProgressRead)
def read_audit_job(job_id: int, _user: User = Depends(require_user), session: Session = Depends(get_session)):
    owned_job_id = session.scalar(
        select(BatchAuditJob.id)
        .join(Batch, BatchAuditJob.batch_id == Batch.id)
        .join(Project, Batch.project_id == Project.id)
        .where(BatchAuditJob.id == job_id)
    )
    if owned_job_id is None:
        raise _not_found("AuditJob", job_id)
    return get_job_progress(session, owned_job_id)


@app.get("/api/batches/{batch_id}/audit-job", response_model=Optional[AuditJobProgressRead])
def read_batch_audit_job(batch_id: int, _user: User = Depends(require_user), session: Session = Depends(get_session)):
    if _batch_with_project(session, batch_id) is None:
        raise _not_found("Batch", batch_id)
    job_id = session.scalar(
        select(BatchAuditJob.id)
        .where(BatchAuditJob.batch_id == batch_id)
        .order_by(BatchAuditJob.active_key.is_(None), BatchAuditJob.id.desc())
        .limit(1)
    )
    return get_job_progress(session, job_id) if job_id is not None else None


@app.post("/api/batches", response_model=BatchDetail, status_code=201)
async def create_batch(
    project_id: int = Form(...),
    supplier_id: str = Form(...),
    name: str = Form(...),
    project_type: str = Form(""),
    owner_name: str = Form(""),
    contents: str = Form(...),
    files: Optional[List[UploadFile]] = File(None),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    rows = await _parse_contents(contents)
    saved_paths = await _save_uploads(rows, files)
    try:
        batch = submit_batch(
            session,
            project_id=project_id,
            supplier_id=supplier_id,
            name=name,
            contents=rows,
            project_type=project_type,
            owner_name=owner_name or supplier_id,
            uploaded_by_user_id=user.id,
        )
    except IntegrityError as error:
        session.rollback()
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Batch contains duplicate content identifiers") from error
    except ValueError as error:
        session.rollback()
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise _service_error(error) from error
    except Exception:
        session.rollback()
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    return BatchDetail(
        **BatchRead.model_validate(batch).model_dump(),
        content_count=len(batch.content_items),
        contents=[_content_summary(item) for item in batch.content_items],
    )




@app.get("/api/contents/table", response_model=List[ContentTableRow])
def contents_table(
    project_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    format_status: Optional[FormatStatus] = None,
    review_status: Optional[ReviewStatus] = None,
    publish_status: Optional[PublishStatus] = None,
    session: Session = Depends(get_session),
):
    query = _content_query().order_by(ContentItem.id.desc())
    if project_id is not None:
        query = query.where(ContentItem.project_id == project_id)
    if batch_id is not None:
        query = query.where(ContentItem.batch_id == batch_id)
    if format_status is not None:
        query = query.where(ContentItem.format_status == format_status)
    if review_status is not None:
        query = query.where(ContentItem.review_status == review_status)
    if publish_status is not None:
        query = query.where(ContentItem.publish_status == publish_status)
    rows = []
    for item in session.scalars(query):
        supplier = min(item.versions, key=lambda version: version.version)
        latest = max(item.versions, key=lambda version: version.version)
        payload = dict(supplier.payload or {})
        latest_payload = dict(latest.payload or {})
        audit = max(item.audit_runs, key=lambda value: value.id or 0) if item.audit_runs else None
        issues = _display_review_issues(audit.issues if audit else [])
        open_tasks = [task for task in item.review_tasks if task.status == "OPEN"]
        agent_map = {result.agent_id or result.agent_name: result for result in (audit.agent_results if audit else [])}
        agents = []
        for agent_id in AGENT_ORDER:
            result = agent_map.get(agent_id)
            agents.append(ContentTableAgent(
                agent_id=agent_id, agent_name=result.agent_name if result else agent_id,
                agent_version=result.agent_version if result else None,
                decision=result.decision if result else None,
                summary=result.summary if result else None,
                score=result.score if result else None,
                status=result.status if result else "NOT_RUN",
            ))
        evidence_count = len({binding.asset_id for test in item.test_cases for binding in test.evidence})
        rows.append(ContentTableRow(
            id=item.id, project_id=item.project_id, batch_id=item.batch_id,
            supplier_external_id=payload.get("supplier_external_id") or item.external_id,
            campaign_theme=payload.get("campaign_theme"), account_name=payload.get("account_name"),
            account_type=payload.get("account_type"), platform=payload.get("platform"),
            original_title=supplier.title, original_body=supplier.body,
            final_title=latest.title, final_body=latest.body,
            body_summary=latest.body[:200], publish_time=payload.get("publish_time"), note=payload.get("note"),
            row_number=payload.get("row_number"), format_status=item.format_status,
            format_errors=list(payload.get("preview_errors") or []),
            review_status=item.review_status, publish_status=item.publish_status,
            issues=[IssueRead.model_validate(issue) for issue in issues], issue_count=len(issues),
            highest_severity=highest_severity((issue.severity for issue in issues)),
            categories=sorted({_issue_dimension_key(issue) for issue in issues}),
            suggestions=[issue.suggestion for issue in issues],
            open_task_count=len(open_tasks), open_task_types=sorted({task.task_type for task in open_tasks}),
            latest_audit_id=audit.id if audit else None,
            agents=agents,
            media_url=f"/api/media/{item.id}" if media else None,
            test_count=len(item.test_cases), evidence_count=evidence_count,
            evidence_status="PRESENT" if item.test_cases and all(test.evidence for test in item.test_cases) else ("MISSING" if item.test_cases else "NONE"),
        ))
    return rows

@app.get("/api/contents", response_model=List[ContentSummary])
def list_contents(
    project_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    review_status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    query = select(ContentItem).options(selectinload(ContentItem.versions)).order_by(ContentItem.id.desc())
    if project_id is not None:
        query = query.where(ContentItem.project_id == project_id)
    if batch_id is not None:
        query = query.where(ContentItem.batch_id == batch_id)
    if review_status is not None:
        query = query.where(ContentItem.review_status == review_status)
    return [_content_summary(item) for item in session.scalars(query)]


@app.get("/api/contents/{content_id}", response_model=ContentDetail)
def get_content(content_id: int, session: Session = Depends(get_session)):
    item = session.scalar(_content_query().where(ContentItem.id == content_id))
    if item is None:
        raise _not_found("ContentItem", content_id)
    return _content_detail(item)


@app.get("/api/contents/{content_id}/test-cases", response_model=List[TestCaseRead])
def get_content_test_cases(content_id: int, session: Session = Depends(get_session)):
    if session.get(ContentItem, content_id) is None:
        raise _not_found("ContentItem", content_id)
    records = list(session.scalars(
        select(TestCase)
        .join(ContentVersion, TestCase.content_version_id == ContentVersion.id)
        .where(
            TestCase.content_item_id == content_id,
            ContentVersion.content_item_id == content_id,
        )
        .options(
            selectinload(TestCase.content_version),
            selectinload(TestCase.evidence).selectinload(TestEvidence.asset),
        )
        .order_by(TestCase.id)
    ))
    return [
        record for record in records
        if all(binding.asset.content_item_id == content_id for binding in record.evidence)
    ]


@app.post("/api/contents/{content_id}/audit", response_model=AuditDetail)
def audit_content(
    content_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    reviewer: Any = Depends(get_request_audit_reviewer),
):
    config = _config_for_user(user)
    try:
        audit = run_audit(
            session,
            content_id,
            reviewer=reviewer,
            model=config["model"] or None,
            created_by_user_id=user.id,
        )
    except ValueError as error:
        session.rollback()
        raise _service_error(error) from error
    return _audit_detail(audit)


@app.post("/api/batches/{batch_id}/audit", response_model=BatchAuditResponse)
def audit_batch(
    batch_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    reviewer: Any = Depends(get_request_audit_reviewer),
):
    batch = session.scalar(
        select(Batch).where(Batch.id == batch_id).options(selectinload(Batch.content_items))
    )
    if batch is None:
        raise _not_found("Batch", batch_id)
    config = _config_for_user(user)
    batch_audit = run_batch_audit_once(
        session,
        batch,
        reviewer=reviewer,
        model=config["model"] or None,
        created_by_user_id=user.id,
    )
    if batch_audit is not None:
        audits, errors = batch_audit
        audit_ids = [audit.id for audit in audits]
        results = [
            BatchAuditItemResult(content_id=audit.content_item_id, status="success", audit_run_id=audit.id)
            for audit in audits
        ]
        results.extend(
            BatchAuditItemResult(content_id=content_id, status="error", error=error)
            for content_id, error in errors
        )
        return BatchAuditResponse(
            batch_id=batch_id,
            audited=len(audit_ids),
            audit_run_ids=audit_ids,
            results=results,
        )
    audit_ids = []
    results = []
    for item in batch.content_items:
        try:
            audit = run_audit(
                session,
                item.id,
                reviewer=reviewer,
                model=config["model"] or None,
                created_by_user_id=user.id,
            )
        except ValueError as error:
            session.rollback()
            results.append(BatchAuditItemResult(content_id=item.id, status="error", error=str(error)))
        except Exception as error:
            session.rollback()
            results.append(BatchAuditItemResult(content_id=item.id, status="error", error="Audit failed"))
        else:
            audit_ids.append(audit.id)
            results.append(BatchAuditItemResult(content_id=item.id, status="success", audit_run_id=audit.id))
    return BatchAuditResponse(
        batch_id=batch_id,
        audited=len(audit_ids),
        audit_run_ids=audit_ids,
        results=results,
    )


@app.get("/api/audit-runs", response_model=List[AuditRunRead])
def list_audit_runs(
    content_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    query = select(AuditRun).join(ContentItem).order_by(AuditRun.id.desc())
    if content_id is not None:
        query = query.where(AuditRun.content_item_id == content_id)
    if batch_id is not None:
        query = query.where(ContentItem.batch_id == batch_id)
    return list(session.scalars(query))


@app.get("/api/review-tasks", response_model=List[ReviewTaskRead])
def list_review_tasks(
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    query = select(ReviewTask).join(ContentItem).order_by(ReviewTask.id.desc())
    if status is not None:
        query = query.where(ReviewTask.status == status)
    if project_id is not None:
        query = query.where(ContentItem.project_id == project_id)
    if batch_id is not None:
        query = query.where(ContentItem.batch_id == batch_id)
    return list(session.scalars(query))


@app.post("/api/review-tasks/{task_id}/resolve", response_model=HumanDecisionRead)
def resolve_review_task(
    task_id: int,
    payload: TaskResolveInput,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    try:
        return resolve_task(
            session,
            task_id,
            decision=payload.decision,
            reviewer=payload.reviewer,
            reviewer_user_id=user.id,
            note=payload.note,
            payload=payload.payload,
        )
    except ValueError as error:
        session.rollback()
        raise _service_error(error) from error


@app.get("/api/reports")
def get_report(
    project_id: int = Query(...),
    batch_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    try:
        return build_report(session, project_id=project_id, batch_id=batch_id)
    except ValueError as error:
        raise _service_error(error) from error


@app.get("/api/config", response_model=ConfigResponse)
def get_config(user: User = Depends(require_user)):
    config = _config_for_user(user)
    return ConfigResponse(
        **config,
        key_set=bool(user.oneapi_key_ciphertext or os.environ.get("ONEAPI_KEY")),
    )


@app.put("/api/config", response_model=ConfigResponse)
def put_config(
    payload: Dict[str, Any],
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    supported_fields = set(DEFAULT_CONFIG) | {"api_key", "clear_key"}
    if set(payload) - supported_fields:
        raise HTTPException(status_code=422, detail="Unsupported configuration field")
    try:
        validated = ConfigInput.model_validate(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid configuration") from error
    stored_user = session.get(User, user.id)
    if stored_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    config = _config_for_user(stored_user)
    if validated.reviewer is not None:
        config["reviewer"] = validated.reviewer
    if validated.model is not None:
        config["model"] = validated.model
    try:
        config = _validated_config(config)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid configuration") from error
    stored_user.reviewer_backend = config["reviewer"]
    stored_user.oneapi_model = config["model"]
    if validated.clear_key:
        stored_user.oneapi_key_ciphertext = None
    if validated.api_key is not None:
        if not validated.api_key:
            raise HTTPException(status_code=422, detail="Invalid configuration")
        stored_user.oneapi_key_ciphertext = encrypt_secret(validated.api_key)
    session.commit()
    session.refresh(stored_user)
    return ConfigResponse(
        **_config_for_user(stored_user),
        key_set=bool(stored_user.oneapi_key_ciphertext or os.environ.get("ONEAPI_KEY")),
    )


def _parse_dashboard_month(month: Optional[str]) -> str:
    raw = month or datetime.utcnow().strftime("%Y-%m")
    try:
        parsed = datetime.strptime(raw, "%Y-%m")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="month must use YYYY-MM") from error
    return parsed.strftime("%Y-%m")


def _month_key(value: datetime) -> str:
    return value.strftime("%Y-%m")


def _shift_month(month: str, offset: int) -> str:
    base = datetime.strptime(month, "%Y-%m")
    zero_based = base.month - 1 + offset
    year = base.year + zero_based // 12
    month_number = zero_based % 12 + 1
    return f"{year:04d}-{month_number:02d}"


@app.get("/api/dashboard/overview", response_model=DashboardOverview)
def dashboard_overview(
    month: Optional[str] = None,
    _user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    selected_month = _parse_dashboard_month(month)
    users = list(session.scalars(select(User).order_by(User.id)))
    workload_counts: Dict[int, Dict[str, int]] = {
        user.id: {"uploaded_count": 0, "audit_started_count": 0, "human_decision_count": 0}
        for user in users
    }

    batches = list(
        session.scalars(
            select(Batch)
            .options(selectinload(Batch.content_items))
            .order_by(Batch.id)
        )
    )
    for batch in batches:
        if batch.uploaded_by_user_id in workload_counts and _month_key(batch.created_at) == selected_month:
            workload_counts[batch.uploaded_by_user_id]["uploaded_count"] += len(batch.content_items)

    audit_runs = list(session.scalars(select(AuditRun)))
    for audit in audit_runs:
        if audit.created_by_user_id in workload_counts and _month_key(audit.created_at) == selected_month:
            workload_counts[audit.created_by_user_id]["audit_started_count"] += 1

    decisions = list(session.scalars(select(HumanDecision)))
    for decision in decisions:
        if decision.reviewer_user_id in workload_counts and _month_key(decision.created_at) == selected_month:
            workload_counts[decision.reviewer_user_id]["human_decision_count"] += 1

    workload = [
        DashboardWorkloadRow(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            months=[DashboardMonthMetrics(month=selected_month, **workload_counts[user.id])],
        )
        for user in users
    ]

    monthly_review_keys = [_shift_month(selected_month, offset) for offset in range(-5, 1)]
    monthly_review_sets: Dict[str, set[int]] = {month_key: set() for month_key in monthly_review_keys}
    for audit in audit_runs:
        month_key = _month_key(audit.created_at)
        if month_key in monthly_review_sets:
            monthly_review_sets[month_key].add(audit.content_item_id)
    monthly_reviews = [
        DashboardMonthlyReview(month=month_key, reviewed_count=len(monthly_review_sets[month_key]))
        for month_key in monthly_review_keys
    ]

    monthly_items = list(
        session.scalars(
            select(ContentItem)
            .where(
                ContentItem.created_at >= datetime.strptime(selected_month, "%Y-%m"),
                ContentItem.created_at < (
                    datetime.strptime(selected_month, "%Y-%m") + timedelta(days=32)
                ).replace(day=1),
            )
            .options(selectinload(ContentItem.batch))
        )
    )
    passed_statuses = {ReviewStatus.PASSED, ReviewStatus.PASSED_WITH_SUGGESTIONS}
    total_count = len(monthly_items)
    passed_count = sum(1 for item in monthly_items if item.review_status in passed_statuses)
    batch_quality = []
    for batch in batches:
        items = [item for item in monthly_items if item.batch_id == batch.id]
        if not items:
            continue
        batch_passed = sum(1 for item in items if item.review_status in passed_statuses)
        batch_quality.append(
            DashboardBatchQuality(
                batch_id=batch.id,
                batch_name=batch.name,
                total_count=len(items),
                passed_count=batch_passed,
                pass_rate=round(batch_passed / len(items), 4),
            )
        )
    analyzed_statuses = {
        ReviewStatus.HUMAN_REVIEW_REQUIRED,
        ReviewStatus.SUPPLIER_REVISION_REQUIRED,
        ReviewStatus.AUTO_FIX_PENDING,
        ReviewStatus.PASSED,
        ReviewStatus.PASSED_WITH_SUGGESTIONS,
        ReviewStatus.BLOCKED,
        ReviewStatus.REJECTED,
    }
    supplier_groups: Dict[int, Dict[str, Any]] = {}
    for item in monthly_items:
        if item.batch is None:
            continue
        group = supplier_groups.setdefault(
            item.batch.id,
            {
                "batch_name": item.batch.name,
                "project_names": {item.batch.project_type or item.batch.name},
                "total_count": 0,
                "analyzed_count": 0,
            },
        )
        group["total_count"] += 1
        if item.review_status in analyzed_statuses:
            group["analyzed_count"] += 1
    supplier_quality = [
        DashboardSupplierQuality(
            supplier_name=payload["batch_name"],
            project_names=sorted(payload["project_names"]),
            total_count=payload["total_count"],
            passed_count=payload["analyzed_count"],
            pass_rate=round(payload["analyzed_count"] / payload["total_count"], 4)
            if payload["total_count"] else 0,
        )
        for _batch_id, payload in supplier_groups.items()
    ]
    supplier_quality.sort(key=lambda item: (-item.total_count, item.supplier_name))
    project_lookup = {project.id: project.name for project in session.scalars(select(Project).order_by(Project.id))}
    project_quality = []
    for current_project_id, project_name in project_lookup.items():
        items = [item for item in monthly_items if item.project_id == current_project_id]
        if not items:
            continue
        project_passed = sum(1 for item in items if item.review_status in passed_statuses)
        project_quality.append(
            DashboardProjectQuality(
                project_id=current_project_id,
                project_name=project_name,
                total_count=len(items),
                passed_count=project_passed,
                pass_rate=round(project_passed / len(items), 4),
            )
        )
    project_quality.sort(key=lambda item: (-item.total_count, item.project_name))

    issues = list(
        session.scalars(
            select(Issue)
            .where(
                Issue.created_at >= datetime.strptime(selected_month, "%Y-%m"),
                Issue.created_at < (
                    datetime.strptime(selected_month, "%Y-%m") + timedelta(days=32)
                ).replace(day=1),
            )
            .options(selectinload(Issue.audit_run).selectinload(AuditRun.content_item))
        )
    )
    clusters: Dict[str, Dict[str, Any]] = {}
    for issue in issues:
        if issue.category in {"system", "system_suggestion"} or not _is_visible_review_issue(issue):
            continue
        if str(issue.severity or "").upper() == "LOW":
            continue
        category_key = _issue_dimension_key(issue)
        cluster = clusters.setdefault(
            category_key,
            {"high_content_ids": set(), "manuscripts": {}},
        )
        item = issue.audit_run.content_item
        if issue.severity.upper() in {"HIGH", "UNKNOWN"}:
            cluster["high_content_ids"].add(item.id)
        cluster["manuscripts"].setdefault(
            item.id,
            DashboardIssueManuscript(
                content_id=item.id,
                title=item.title,
                severity=issue.severity,
                reason=issue.reason,
            ),
        )
    issue_clusters = [
        DashboardIssueCluster(
            category=category,
            issue_count=len(payload["manuscripts"]),
            manuscript_count=len(payload["manuscripts"]),
            high_count=len(payload["high_content_ids"]),
            manuscripts=list(payload["manuscripts"].values())[:8],
        )
        for category, payload in clusters.items()
    ]
    issue_clusters.sort(key=lambda cluster: (-cluster.issue_count, cluster.category))

    return DashboardOverview(
        month=selected_month,
        workload=workload,
        quality=DashboardQuality(
            total_count=total_count,
            passed_count=passed_count,
            pass_rate=round(passed_count / total_count, 4) if total_count else 0,
            batches=batch_quality,
        ),
        project_quality=project_quality,
        monthly_reviews=monthly_reviews,
        supplier_quality=supplier_quality,
        issue_clusters=issue_clusters,
    )


@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    session.scalar(select(func.count(Project.id)))
    return {"ok": True, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}


@app.get("/api/media/{content_id}")
def media(content_id: int, session: Session = Depends(get_session)):
    item = session.scalar(
        select(ContentItem).where(ContentItem.id == content_id).options(selectinload(ContentItem.versions))
    )
    if item is None:
        raise _not_found("ContentItem", content_id)
    media_value = item.versions[-1].payload.get("media") if item.versions else None
    filename = media_value[0] if isinstance(media_value, list) and media_value else media_value
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="Content has no media")
    path = _uploads_dir() / Path(filename).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path)
