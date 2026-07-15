from __future__ import annotations

from copy import deepcopy
from typing import Any


FORBIDDEN_FIELDS = {
    "数字",
    "日期",
    "时间",
    "城市",
    "金额",
    "比例",
    "测试结果",
    "功能上线状态",
    "覆盖范围",
    "对比结论",
    "证据内容",
    "用户隐私",
    "未确认功能",
}


def may_auto_fix(issue: dict[str, Any]) -> tuple[bool, str]:
    if not issue.get("是否可自动修改", False):
        return False, "问题规则未允许自动修改"

    if issue.get("是否需要人工", False):
        return False, "问题需要人工确认"

    category = issue.get("问题分类", "")
    if category in FORBIDDEN_FIELDS:
        return False, f"禁止自动修改字段：{category}"

    suggestion = issue.get("修改建议", "").strip()
    if not suggestion:
        return False, "没有明确修改建议"

    return True, "允许生成待确认修改稿"


def apply_exact_replacements(text: str, replacements: list[dict[str, Any]]) -> str:
    """仅执行 auto_apply=true 的精确替换，不进行开放式改写。"""
    output = text

    for rule in replacements:
        if not rule.get("auto_apply", False):
            continue
        if rule.get("类型") != "精确替换":
            continue

        old = rule.get("原文")
        new = rule.get("建议")
        if old and new:
            output = output.replace(old, new)

    return output


def create_revision(
    original: dict[str, Any],
    revised_title: str,
    revised_body: str,
    rule_ids: list[str],
) -> dict[str, Any]:
    """生成新版本，不覆盖原稿。"""
    revision = deepcopy(original)
    revision["version"] = int(original.get("version", 1)) + 1
    revision["title"] = revised_title
    revision["body"] = revised_body
    revision["revision_source"] = "AI_SUGGESTION"
    revision["revision_rule_ids"] = rule_ids
    revision["confirmation_status"] = "PENDING"
    return revision
