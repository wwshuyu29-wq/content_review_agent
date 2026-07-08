"""审核状态机引擎 —— 流程三/四/五 的动作与状态流转。

设计要点（对应修正后的状态机）：
  - 消歧：内容状态=已提交 时，用「格式校验」列决定去向
      格式校验≠通过 -> 流程三 内容读取
      格式校验=通过 -> 流程四 批量内容审核
  - 人工审核不回炉自动审核：管理员直接置 通过/需修改/已删除（在 CLI 外由人在表里改），
    引擎不会把 待人工审核 的行重新跑自动审核，消除死循环。
  - 供应商换图/格式退回才用「已提交+格式校验」回流，且有重试上限 MAX_ROUNDS，
    超限置「已驳回」终态。
  - 直接通过的行回填 最终标题/最终正文，保证输出列不缺。
  - 风险分级处置：low 自动改写(流程五)；mid/high/unknown 只给建议转人工；none 直接通过。

一次 run_batch 分三段串跑：流程三 -> 流程四 -> 流程五，
等待态(待人工审核/图片视频修改/待供应商补充)和终态(通过/已驳回/已删除)不被引擎触碰。
"""
from __future__ import annotations

import os
import time

from . import schema
from .reviewer import Verdict
from .standards import Standards


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def media_type(media: str) -> str:
    """按扩展名判断媒体类型：image / video / ''（无媒体或未知）。"""
    media = (media or "").strip()
    if not media:
        return ""
    ext = os.path.splitext(media.split("?")[0])[1].lower()
    if ext in schema.IMAGE_EXTS:
        return "image"
    if ext in schema.VIDEO_EXTS:
        return "video"
    return "unknown"


def _status(row: dict) -> str:
    s = (row.get(schema.COL_STATUS) or "").strip()
    return s or schema.ST_SUBMITTED  # 空状态视为已提交（新上传）


def _round(row: dict) -> int:
    try:
        return int(row.get(schema.COL_ROUND) or 0)
    except ValueError:
        return 0


def _return_to_supplier(row: dict, fmt_value: str, reason: str, wait_status: str) -> None:
    """退回供应商，带重试上限。超限 -> 已驳回终态。"""
    rnd = _round(row)
    if rnd >= schema.MAX_ROUNDS:
        row[schema.COL_STATUS] = schema.ST_REJECTED
        row[schema.COL_PROBLEM] = f"{reason}（已达重试上限 {schema.MAX_ROUNDS} 次，自动驳回）"
        return
    row[schema.COL_ROUND] = str(rnd + 1)
    row[schema.COL_STATUS] = wait_status
    row[schema.COL_PROBLEM] = reason
    if fmt_value:
        row[schema.COL_FORMAT_CHECK] = fmt_value


def _stamp_audit(row: dict, v: Verdict) -> None:
    row[schema.COL_AUDIT] = (
        f"{_now()}|model={v.model}|conf={v.confidence}|"
        f"risk={v.risk_level}|cats={','.join(v.categories)}"
    )


# ── 流程三：内容读取（格式/完整性校验）────────────────────
def process_read(row: dict) -> None:
    missing = [f for f in schema.REQUIRED_FIELDS if not (row.get(f) or "").strip()]
    mtype = media_type(row.get(schema.COL_MEDIA))

    if missing:
        _return_to_supplier(row, schema.FMT_INCOMPLETE,
                            f"缺失必填字段：{'、'.join(missing)}", schema.ST_WAIT_SUPPLIER)
        return
    if mtype == "unknown":
        _return_to_supplier(row, schema.FMT_BAD_FORMAT,
                            "图片/视频格式不合格（仅支持 JPG/PNG/WEBP/MP4/MOV）",
                            schema.ST_WAIT_SUPPLIER)
        return

    # 校验通过：格式校验=通过，内容状态保持已提交，交给流程四
    row[schema.COL_FORMAT_CHECK] = schema.FMT_PASS
    row[schema.COL_PROBLEM] = ""


# ── 流程四：批量内容审核 ───────────────────────────────────
def process_review(row: dict, reviewer, standards: Standards) -> None:
    v = reviewer.review(row, standards)
    _stamp_audit(row, v)

    # 图片需修改（多模态审核判定，本期启发式不触发；接入 ERNIE-VL 后可置位）
    if v.media_issue:
        row[schema.COL_SUGGESTION] = v.suggestion or "图片需修改"
        _return_to_supplier(row, "", v.suggestion or "图片需修改", schema.ST_MEDIA_FIX)
        return

    lvl = v.risk_level
    row[schema.COL_SUGGESTION] = v.suggestion
    row[schema.COL_PROBLEM] = "；".join(v.categories)

    if lvl in (schema.RISK_HIGH, schema.RISK_UNKNOWN, schema.RISK_MID):
        # 中/高风险、无法判断 -> 只给建议，转人工（不自动改写）
        row[schema.COL_STATUS] = schema.ST_WAIT_HUMAN
    elif lvl == schema.RISK_LOW:
        # 低风险 -> 进流程五自动改写
        row[schema.COL_STATUS] = schema.ST_NEED_MODIFY
    else:
        # 无问题 -> 直接通过，回填最终列
        row[schema.COL_STATUS] = schema.ST_PASS
        row[schema.COL_PROBLEM] = ""
        row[schema.COL_FINAL_TITLE] = row.get(schema.COL_TITLE, "")
        row[schema.COL_FINAL_BODY] = row.get(schema.COL_BODY, "")


# ── 流程五：批量内容修改（低风险自动改写）──────────────────
def process_modify(row: dict, reviewer, standards: Standards) -> None:
    new_title, new_body = reviewer.rewrite(row, standards)
    row[schema.COL_FINAL_TITLE] = new_title
    row[schema.COL_FINAL_BODY] = new_body
    row[schema.COL_STATUS] = schema.ST_PASS


# ── 可处理性判断 ───────────────────────────────────────────
def _actionable_read(row: dict) -> bool:
    return (_status(row) == schema.ST_SUBMITTED
            and (row.get(schema.COL_FORMAT_CHECK) or "").strip() != schema.FMT_PASS)


def _actionable_review(row: dict) -> bool:
    return (_status(row) == schema.ST_SUBMITTED
            and (row.get(schema.COL_FORMAT_CHECK) or "").strip() == schema.FMT_PASS)


def _actionable_modify(row: dict) -> bool:
    return _status(row) == schema.ST_NEED_MODIFY


# ── 一批跑完：流程三 -> 四 -> 五 ───────────────────────────
def run_batch(adapter, reviewer, standards: Standards) -> dict:
    rows = adapter.read_rows()
    summary = {"read": 0, "review": 0, "modify": 0, "total": len(rows)}

    for r in rows:                       # 流程三
        if _actionable_read(r):
            process_read(r)
            summary["read"] += 1
    for r in rows:                       # 流程四（含流程三新放行的行）
        if _actionable_review(r):
            process_review(r, reviewer, standards)
            summary["review"] += 1
    for r in rows:                       # 流程五（含流程四判为需修改的行）
        if _actionable_modify(r):
            process_modify(r, reviewer, standards)
            summary["modify"] += 1

    adapter.write_rows(rows)
    # 结果状态分布
    dist: dict[str, int] = {}
    for r in rows:
        s = _status(r)
        dist[s] = dist.get(s, 0) + 1
    summary["status_dist"] = dist
    return summary
