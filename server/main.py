"""FastAPI entry point for the database-backed content review workflow."""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from scripts.text_review.reviewer import get_reviewer
from server.db import Base, get_db_engine, get_session
from server.models import (
    AgentResult,
    AuditRun,
    Batch,
    ContentItem,
    ContentVersion,
    HumanDecision,
    Issue,
    Project,
    ReviewTask,
    RuleVersion,
)
from server.schemas import (
    AgentResultRead,
    AuditRunRead,
    BatchRead,
    ContentItemRead,
    ContentVersionRead,
    HumanDecisionRead,
    IssueRead,
    ProjectCreate,
    ProjectRead,
    ReviewTaskRead,
    RuleVersionRead,
)
from server.seed import seed_default_project
from server.services.content_service import submit_batch
from server.services.report_service import build_report
from server.services.review_service import resolve_task, run_audit


REPO_DIR = Path(__file__).resolve().parents[1]
ALLOWED_IMAGE_TYPES = {
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_CONFIG = {"reviewer": "offline", "model": ""}


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


def _save_config(config: Dict[str, str]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)


def get_audit_reviewer() -> Any:
    config = _load_config()
    if config["model"]:
        os.environ["ONEAPI_MODEL"] = config["model"]
    return get_reviewer(config["reviewer"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _data_dir().mkdir(parents=True, exist_ok=True)
    _uploads_dir().mkdir(parents=True, exist_ok=True)
    if not _config_path().exists():
        _save_config(dict(DEFAULT_CONFIG))
    engine = get_db_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_default_project(session)
        session.commit()
    yield


app = FastAPI(title="内容审核后端", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RuleVersionInput(BaseModel):
    dimension_standards: Dict[str, Any] = Field(default_factory=dict)
    project_facts: Dict[str, Any] = Field(default_factory=dict)
    structured_rules: Dict[str, Any] = Field(default_factory=dict)
    prompt_version: str = Field(min_length=1, max_length=100)


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
    elif "open review tasks" in message:
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
    )


@app.get("/api/projects", response_model=List[ProjectRead])
def list_projects(session: Session = Depends(get_session)):
    return list(session.scalars(select(Project).order_by(Project.id)))


@app.post("/api/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)):
    project = Project(name=payload.name.strip(), description=payload.description)
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


@app.post("/api/projects/{project_id}/rule-versions", response_model=RuleVersionRead, status_code=201)
def create_rule_version(
    project_id: int,
    payload: RuleVersionInput,
    session: Session = Depends(get_session),
):
    project = session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    latest = session.scalar(select(func.max(RuleVersion.version)).where(RuleVersion.project_id == project_id)) or 0
    version = RuleVersion(project=project, version=latest + 1, **payload.model_dump())
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


@app.post("/api/projects/{project_id}/rule-versions/{rule_version_id}/publish", response_model=ProjectRead)
def publish_rule_version(
    project_id: int,
    rule_version_id: int,
    session: Session = Depends(get_session),
):
    project = session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    version = session.get(RuleVersion, rule_version_id)
    if version is None or version.project_id != project_id:
        raise _not_found("RuleVersion", rule_version_id)
    project.current_rule_version = version
    session.commit()
    session.refresh(project)
    return project


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


@app.get("/api/batches", response_model=List[BatchRead])
def list_batches(
    project_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    query = select(Batch).order_by(Batch.id.desc())
    if project_id is not None:
        query = query.where(Batch.project_id == project_id)
    return list(session.scalars(query))


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
    for key, value in validated.model_dump(exclude_none=True).items():
        config[key] = value
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
