"""FastAPI entry point for the database-backed content review workflow."""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    ensure_initial_admin,
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
from server.services.review_service import resolve_task, run_audit
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
AGENT_ORDER = ("COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY", "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS")
UPLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_CONFIG = {"reviewer": "heuristic", "model": ""}
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
        seed_default_project(session)
        stale_seconds = max(1, int(os.environ.get("AUDIT_JOB_STALE_SECONDS", "300")))
        interrupt_stale_jobs(session, datetime.utcnow() - timedelta(seconds=stale_seconds))
        session.commit()
    set_reviewer_factory(get_audit_reviewer)
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

    @field_validator("reviewer", "model")
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


def _not_found(entity: str, entity_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{entity} {entity_id} not found")


def _service_error(error: ValueError) -> HTTPException:
    message = str(error)
    if "does not exist" in message or "does not belong" in message:
        status = 404
    elif "open review tasks" in message or "terminal" in message or "already been audited" in message:
        status = 409
    else:
        status = 422
    return HTTPException(status_code=status, detail=message)


def _content_summary(item: ContentItem) -> ContentSummary:
    return ContentSummary.model_validate(item)


def _audit_detail(audit: AuditRun) -> AuditDetail:
    return AuditDetail.model_validate(audit)


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
    auth_session = session.get(UserSession, request.state.auth_session.id)
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
    excel_file: UploadFile = File(...), evidence_zip: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
):
    project = _validated_tech_project(session, project_id)
    supplier_id = supplier_id.strip()
    batch_name = batch_name.strip()
    if not supplier_id or not batch_name:
        raise HTTPException(status_code=422, detail="supplier_id and batch_name are required")
    identity = PreviewIdentity(
        project_id=project.id, project_code=project.code, content_type=project.content_type,
        package_version=project.current_rule_version.package_version,
        supplier_id=supplier_id, batch_name=batch_name,
    )
    root = _data_dir() / "import-previews"
    temp = root / f"upload-{uuid.uuid4().hex}"
    try:
        xlsx = await _save_excel_upload(excel_file, temp, {".xlsx"}, MAX_EXCEL_UPLOAD_BYTES)
        zip_path = None
        if evidence_zip is not None:
            zip_path = await _save_excel_upload(evidence_zip, temp, {".zip"}, MAX_EVIDENCE_ZIP_UPLOAD_BYTES)
        result = preview_import(
            xlsx, zip_path, root, identity=identity,
            trigger_terms=_evidence_trigger_terms(project),
        )
        return ImportPreviewRead(
            token=result.token,
            rows=[{**row.__dict__, "tests": [test.__dict__ for test in row.tests]} for row in result.rows],
            tests=[test.__dict__ for test in result.test_cases], errors=result.errors,
            warnings=result.warnings, total_count=result.total_count,
            valid_count=result.valid_count, error_count=result.error_count,
            test_count=result.test_count, **identity.__dict__,
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
    reviewer: Any = Depends(get_audit_reviewer),
):
    _validated_tech_project(session, payload.project_id)
    try:
        batch = confirm_excel_import(
            session,
            token,
            payload.project_id,
            payload.supplier_id,
            payload.batch_name,
            image_llm=getattr(reviewer, "llm", None),
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
def start_audit_job(batch_id: int, session: Session = Depends(get_session)):
    batch = _batch_with_project(session, batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    config = _validated_config(_load_config())
    try:
        result = create_or_get_active_job(session, batch.id, config["model"] or config["reviewer"])
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
    contents: str = Form(...),
    files: Optional[List[UploadFile]] = File(None),
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
        issues = list(audit.issues) if audit else []
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
        media = latest_payload.get("media", payload.get("media"))
        evidence_count = len({binding.asset_id for test in item.test_cases for binding in test.evidence})
        rows.append(ContentTableRow(
            id=item.id, project_id=item.project_id, batch_id=item.batch_id,
            supplier_external_id=payload.get("supplier_external_id") or item.external_id,
            campaign_theme=payload.get("campaign_theme"), account_name=payload.get("account_name"),
            account_type=payload.get("account_type"), platform=payload.get("platform"),
            original_title=supplier.title, original_body=supplier.body,
            final_title=latest.title, final_body=latest.body,
            body_summary=latest.body[:200], image_filename=payload.get("image_filename"),
            publish_time=payload.get("publish_time"), note=payload.get("note"),
            row_number=payload.get("row_number"), format_status=item.format_status,
            review_status=item.review_status, publish_status=item.publish_status,
            issues=[IssueRead.model_validate(issue) for issue in issues], issue_count=len(issues),
            highest_severity=highest_severity((issue.severity for issue in issues)),
            categories=sorted({issue.category for issue in issues}),
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
    session: Session = Depends(get_session),
    reviewer: Any = Depends(get_audit_reviewer),
):
    config = _load_config()
    try:
        audit = run_audit(session, content_id, reviewer=reviewer, model=config["model"] or None)
    except ValueError as error:
        session.rollback()
        raise _service_error(error) from error
    return _audit_detail(audit)


@app.post("/api/batches/{batch_id}/audit", response_model=BatchAuditResponse)
def audit_batch(
    batch_id: int,
    session: Session = Depends(get_session),
    reviewer: Any = Depends(get_audit_reviewer),
):
    batch = session.scalar(
        select(Batch).where(Batch.id == batch_id).options(selectinload(Batch.content_items))
    )
    if batch is None:
        raise _not_found("Batch", batch_id)
    config = _load_config()
    audit_ids = []
    results = []
    for item in batch.content_items:
        try:
            audit = run_audit(session, item.id, reviewer=reviewer, model=config["model"] or None)
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
    session: Session = Depends(get_session),
):
    try:
        return resolve_task(
            session,
            task_id,
            decision=payload.decision,
            reviewer=payload.reviewer,
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
def get_config():
    return ConfigResponse(**_load_config(), key_set=bool(os.environ.get("ONEAPI_KEY")))


@app.put("/api/config", response_model=ConfigResponse)
def put_config(payload: Dict[str, Any]):
    if set(payload) - set(DEFAULT_CONFIG):
        raise HTTPException(status_code=422, detail="Unsupported configuration field")
    try:
        validated = ConfigInput.model_validate(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid configuration") from error
    config = _load_config()
    config.update(validated.model_dump(exclude_none=True))
    try:
        config = _validated_config(config)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid configuration") from error
    _save_config(config)
    return ConfigResponse(**config, key_set=bool(os.environ.get("ONEAPI_KEY")))


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
