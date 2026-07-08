"""流程六 审核报告 + 流程七 规则沉淀。

报告（两份）：
  - 审核成果：各内容状态数量、各流程处理量
  - 问题汇总：按问题类别/命中项统计分布与高频错误

规则沉淀：
  - 扫描表中问题，找出高频问题类别与高频禁用词候选
  - 产出「新增规则建议」，管理员确认后（--confirm）写入规则库（standards.add_rules）
    未确认时只打印建议，绝不自动改规则库（对应流程七 Step2 需管理员确认）
"""
from __future__ import annotations

import re
from collections import Counter

from . import schema


def build_reports(rows: list[dict]) -> dict:
    status_dist = Counter(_status(r) for r in rows)

    # 问题类别分布（来自「问题原因」列，以「；」分隔）
    cat_counter: Counter = Counter()
    for r in rows:
        for cat in _split(r.get(schema.COL_PROBLEM)):
            cat_counter[cat] += 1

    # 高频命中项（从「修改建议」里粗提取）
    issue_counter: Counter = Counter()
    for r in rows:
        for iss in _split(r.get(schema.COL_SUGGESTION)):
            issue_counter[iss] += 1

    total = len(rows)
    passed = status_dist.get(schema.ST_PASS, 0)
    return {
        "审核成果": {
            "内容总数": total,
            "通过": passed,
            "通过率": round(passed / total, 3) if total else 0,
            "待人工审核": status_dist.get(schema.ST_WAIT_HUMAN, 0),
            "需修改": status_dist.get(schema.ST_NEED_MODIFY, 0),
            "图片/视频修改": status_dist.get(schema.ST_MEDIA_FIX, 0),
            "待供应商补充": status_dist.get(schema.ST_WAIT_SUPPLIER, 0),
            "已驳回": status_dist.get(schema.ST_REJECTED, 0),
            "已删除": status_dist.get(schema.ST_DELETED, 0),
            "状态分布": dict(status_dist),
        },
        "问题汇总": {
            "问题类别分布": dict(cat_counter.most_common()),
            "高频问题Top10": issue_counter.most_common(10),
        },
    }


def render_reports_md(reports: dict) -> str:
    r1 = reports["审核成果"]
    r2 = reports["问题汇总"]
    lines = ["# 审核报告", "", "## 一、审核成果"]
    for k, v in r1.items():
        if k == "状态分布":
            continue
        lines.append(f"- {k}：{v}")
    lines += ["", "### 状态分布"]
    for s, c in r1["状态分布"].items():
        lines.append(f"- {s}：{c}")
    lines += ["", "## 二、问题汇总", "", "### 问题类别分布"]
    for cat, c in r2["问题类别分布"].items():
        lines.append(f"- {cat}：{c}")
    lines += ["", "### 高频问题 Top10"]
    for iss, c in r2["高频问题Top10"]:
        lines.append(f"- ({c}次) {iss}")
    return "\n".join(lines)


# ── 流程七：规则沉淀 ───────────────────────────────────────
def distill_rule_proposals(rows: list[dict], min_freq: int = 2) -> dict:
    """扫描表，产出新增规则建议（不落库，需管理员确认）。"""
    cat_counter: Counter = Counter()
    deny_candidates: Counter = Counter()

    for r in rows:
        for cat in _split(r.get(schema.COL_PROBLEM)):
            cat_counter[cat] += 1
        # 从命中禁用词的建议里提取候选词：「命中禁用词：A、B」
        for iss in _split(r.get(schema.COL_SUGGESTION)):
            m = re.search(r"命中禁用词[：:]\s*(.+)", iss)
            if m:
                for w in re.split(r"[、,，]", m.group(1)):
                    w = w.strip()
                    if w:
                        deny_candidates[w] += 1

    high_freq_cats = [c for c, n in cat_counter.items() if n >= min_freq]
    must_human_from_cats = [c for c in high_freq_cats if "人工确认" in c]

    return {
        "高频问题类别": {c: n for c, n in cat_counter.most_common() if n >= min_freq},
        "建议新增必须人工确认类型": must_human_from_cats,
        "高频禁用词候选": {w: n for w, n in deny_candidates.most_common() if n >= min_freq},
    }


def apply_rule_proposals(repo, proposals: dict) -> dict:
    """管理员确认后写入规则库。"""
    return repo.add_rules(
        deny_words=list(proposals.get("高频禁用词候选", {}).keys()),
        must_human_keywords=proposals.get("建议新增必须人工确认类型", []),
    )


# ── helpers ───────────────────────────────────────────────
def _status(row: dict) -> str:
    return (row.get(schema.COL_STATUS) or "").strip() or schema.ST_SUBMITTED


def _split(val) -> list[str]:
    if not val:
        return []
    return [p.strip() for p in re.split(r"[；;]", str(val)) if p.strip()]
