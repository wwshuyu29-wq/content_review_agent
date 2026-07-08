"""内容审核 Web 后端（FastAPI）。

复用 scripts/text_review 的审核引擎、标准仓库、状态机，Flask/CLI/网页共用同一套逻辑与数据。
本层只做 HTTP 路由，不重复实现业务逻辑。

数据（默认 data/，已被 .gitignore 忽略）：
  data/review.csv       审核表（LocalCsvAdapter 载体）
  data/standards/       分维度标准 + 项目标准 + rules.json
  data/uploads/         供应商上传的图片
  data/config.json      审核配置（模型后端/model/项目；key 只走环境变量）

运行：
  pip install fastapi "uvicorn[standard]" python-multipart
  uvicorn server.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.text_review import engine, report, schema  # noqa: E402
from scripts.text_review.reviewer import get_reviewer  # noqa: E402
from scripts.text_review.standards import get_standards_repo  # noqa: E402
from scripts.text_review.table_adapter import LocalCsvAdapter  # noqa: E402

# ── 路径 ───────────────────────────────────────────────────
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("CR_DATA_DIR", os.path.join(REPO_DIR, "data"))
TABLE = os.path.join(DATA_DIR, "review.csv")
STANDARDS_DIR = os.path.join(DATA_DIR, "standards")
UPLOADS = os.path.join(DATA_DIR, "uploads")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_CONFIG = {"reviewer": "heuristic", "model": "", "base_url": "", "project": ""}

app = FastAPI(title="内容审核后端")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


# ── 初始化 ─────────────────────────────────────────────────
def _init():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOADS, exist_ok=True)
    repo = get_standards_repo("local", STANDARDS_DIR)
    repo.scaffold()  # 首次生成分维度标准模板 + rules.json
    if not os.path.exists(CONFIG_PATH):
        _save_config(DEFAULT_CONFIG)
    if not os.path.exists(TABLE):
        LocalCsvAdapter(TABLE).write_rows([])


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return {**DEFAULT_CONFIG, **cfg}


def _save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _adapter():
    return LocalCsvAdapter(TABLE)


def _repo():
    return get_standards_repo("local", STANDARDS_DIR)


def _public(row: dict) -> dict:
    r = dict(row)
    r.pop(schema.COL_AUDIT, None)  # 留痕内部字段，列表不返回
    # 用 media 字段是否有值判断有无图，不暴露服务器本地路径
    return r


_init()


# ── 内容 / 审核 ────────────────────────────────────────────
@app.get("/api/rows")
def list_rows(status: Optional[str] = None):
    rows = _adapter().read_rows()
    if status:
        rows = [r for r in rows if (r.get(schema.COL_STATUS) or "").strip() == status]
    rows.sort(key=lambda r: r.get(schema.COL_ID, ""), reverse=True)
    return {"count": len(rows), "rows": [_public(r) for r in rows]}


@app.get("/api/rows/{content_id}")
def get_row(content_id: str):
    for r in _adapter().read_rows():
        if r.get(schema.COL_ID) == content_id:
            return _public(r)
    raise HTTPException(404, "未找到该记录")


@app.post("/api/upload")
async def upload(
    supplier_id: str = Form(...),
    theme: str = Form(""),
    platform: str = Form(""),
    title: str = Form(...),
    body: str = Form(...),
    publish_time: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    """供应商上传：填表 + 传图，生成一行待审记录。"""
    content_id = uuid.uuid4().hex[:12]
    media = ""
    if file is not None and file.filename:
        ext = os.path.splitext(file.filename.lower())[1]
        if ext not in ALLOWED_IMAGE:
            raise HTTPException(400, f"不支持的图片格式 {ext}，仅支持 JPG/PNG/WEBP")
        media = f"{content_id}{ext}"
        with open(os.path.join(UPLOADS, media), "wb") as f:
            f.write(await file.read())

    adapter = _adapter()
    rows = adapter.read_rows()
    row = {c: "" for c in schema.ALL_COLUMNS}
    row.update({
        schema.COL_ID: content_id,
        schema.COL_THEME: theme,
        schema.COL_PLATFORM: platform,
        schema.COL_TITLE: title,
        schema.COL_BODY: body,
        schema.COL_MEDIA: media,
        schema.COL_PUBLISH_TIME: publish_time,
        schema.COL_STATUS: schema.ST_SUBMITTED,
    })
    rows.append(row)
    adapter.write_rows(rows)
    return {"content_id": content_id, "status": schema.ST_SUBMITTED}


@app.post("/api/run-batch")
def run_batch():
    """一键跑审核：流程三 -> 四 -> 五。模型/项目取自配置。"""
    cfg = _load_config()
    if cfg.get("model"):
        os.environ["ONEAPI_MODEL"] = cfg["model"]
    if cfg.get("base_url"):
        os.environ["ONEAPI_BASE_URL"] = cfg["base_url"]
    reviewer = get_reviewer(cfg.get("reviewer", "heuristic"))
    standards = _repo().load(cfg.get("project") or None)
    summary = engine.run_batch(_adapter(), reviewer, standards)
    return summary


@app.get("/api/human-queue")
def human_queue():
    rows = [r for r in _adapter().read_rows()
            if (r.get(schema.COL_STATUS) or "").strip() == schema.ST_WAIT_HUMAN]
    return {"count": len(rows), "rows": [_public(r) for r in rows]}


@app.post("/api/rows/{content_id}/human")
def human_decision(content_id: str, payload: dict):
    """人工审核结论：approved/需修改/deleted。不回炉自动审核。"""
    decision = payload.get("decision")
    reason = (payload.get("reason") or "").strip()
    manual = (payload.get("manual_content") or "").strip()
    status_map = {
        "approved": schema.ST_PASS,
        "need_modify": schema.ST_NEED_MODIFY,
        "deleted": schema.ST_DELETED,
    }
    if decision not in status_map:
        raise HTTPException(400, "decision 必须是 approved / need_modify / deleted")

    adapter = _adapter()
    rows = adapter.read_rows()
    for r in rows:
        if r.get(schema.COL_ID) == content_id:
            r[schema.COL_STATUS] = status_map[decision]
            if reason:
                r[schema.COL_SUGGESTION] = reason
            if manual:
                r[schema.COL_MANUAL_CONTENT] = manual
            if decision == "approved":
                # 保留：回填最终列（优先人工内容）
                r[schema.COL_FINAL_TITLE] = r.get(schema.COL_TITLE, "")
                r[schema.COL_FINAL_BODY] = manual or r.get(schema.COL_BODY, "")
            adapter.write_rows(rows)
            return {"content_id": content_id, "status": r[schema.COL_STATUS]}
    raise HTTPException(404, "未找到该记录")


@app.get("/media/{content_id}")
def media(content_id: str):
    for r in _adapter().read_rows():
        if r.get(schema.COL_ID) == content_id:
            m = (r.get(schema.COL_MEDIA) or "").strip()
            fp = os.path.join(UPLOADS, m)
            if m and os.path.exists(fp):
                return FileResponse(fp)
            raise HTTPException(404, "无图片")
    raise HTTPException(404, "未找到该记录")


# ── 标准管理（流程一）──────────────────────────────────────
@app.get("/api/standards")
def get_standards():
    repo = _repo()
    dims = []
    gdir = os.path.join(STANDARDS_DIR, "global")
    for key, fname in schema.DIMENSION_FILES.items():
        fp = os.path.join(gdir, fname)
        content = ""
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
        dims.append({"key": key, "name": schema.DIMENSIONS[key], "content": content})

    pdir = os.path.join(STANDARDS_DIR, "projects")
    projects = []
    if os.path.isdir(pdir):
        projects = [os.path.splitext(f)[0] for f in os.listdir(pdir) if f.endswith(".md")]

    rules = repo._load_rules()
    return {"dimensions": dims, "projects": projects, "rules": rules}


@app.put("/api/standards/dimension/{key}")
def save_dimension(key: str, payload: dict):
    if key not in schema.DIMENSION_FILES:
        raise HTTPException(404, "未知维度")
    gdir = os.path.join(STANDARDS_DIR, "global")
    os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, schema.DIMENSION_FILES[key]), "w", encoding="utf-8") as f:
        f.write(payload.get("content", ""))
    return {"ok": True}


@app.get("/api/standards/project/{name}")
def get_project(name: str):
    fp = os.path.join(STANDARDS_DIR, "projects", f"{name}.md")
    content = ""
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            content = f.read()
    return {"name": name, "content": content}


@app.put("/api/standards/project/{name}")
def save_project(name: str, payload: dict):
    pdir = os.path.join(STANDARDS_DIR, "projects")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write(payload.get("content", ""))
    return {"ok": True}


@app.put("/api/rules")
def save_rules(payload: dict):
    repo = _repo()
    rules = repo._load_rules()
    for k in ("deny_words", "recommended", "must_human_keywords", "required_tags"):
        if k in payload:
            rules[k] = payload[k]
    repo._save_rules(rules)
    return {"ok": True, "rules": rules}


# ── 报告（流程六）+ 规则沉淀（流程七）─────────────────────
@app.get("/api/report")
def get_report():
    return report.build_reports(_adapter().read_rows())


@app.post("/api/distill-rules")
def distill(confirm: bool = False, min_freq: int = 2):
    rows = _adapter().read_rows()
    proposals = report.distill_rule_proposals(rows, min_freq=min_freq)
    if not confirm:
        return {"proposals": proposals, "applied": None}
    added = report.apply_rule_proposals(_repo(), proposals)
    return {"proposals": proposals, "applied": added}


# ── 配置（模型/项目；key 只读环境变量）────────────────────
@app.get("/api/config")
def get_config():
    cfg = _load_config()
    return {**cfg, "key_set": bool(os.environ.get("ONEAPI_KEY"))}


@app.put("/api/config")
def put_config(payload: dict):
    cfg = _load_config()
    for k in ("reviewer", "model", "base_url", "project"):
        if k in payload:
            cfg[k] = payload[k]
    _save_config(cfg)
    return {**cfg, "key_set": bool(os.environ.get("ONEAPI_KEY"))}


@app.get("/api/health")
def health():
    return {"ok": True, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
