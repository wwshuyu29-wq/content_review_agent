from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


PRIORITY = {
    "PASS": 0,
    "PASS_WITH_SUGGESTIONS": 1,
    "AUTO_FIX_PENDING_CONFIRMATION": 2,
    "NEED_TEXT_FIX": 3,
    "NEED_MEDIA_FIX": 4,
    "HUMAN_REVIEW": 5,
    "BLOCK": 6,
}


@dataclass(frozen=True)
class AgentResult:
    agent: str
    decision: str
    issues: list[dict]


def arbitrate(results: Iterable[AgentResult]) -> dict:
    results = list(results)
    if not results:
        raise ValueError("没有 Agent 审核结果")

    for result in results:
        if result.decision not in PRIORITY:
            raise ValueError(f"未知审核结论：{result.decision}")

    final_decision = max(results, key=lambda item: PRIORITY[item.decision]).decision

    issues = []
    seen = set()

    for result in results:
        for issue in result.issues:
            evidence = issue.get("证据", {})
            dedup_key = (
                issue.get("字段"),
                evidence.get("起始位置"),
                evidence.get("结束位置"),
                issue.get("问题分类"),
            )

            if dedup_key in seen:
                continue

            seen.add(dedup_key)
            merged = dict(issue)
            merged["来源Agent"] = result.agent
            issues.append(merged)

    open_tasks = []
    if final_decision == "BLOCK":
        review_status = "REJECTED"
    elif final_decision == "HUMAN_REVIEW":
        review_status = "HUMAN_REVIEW_REQUIRED"
        open_tasks.append("HUMAN_REVIEW")
    elif final_decision == "NEED_MEDIA_FIX":
        review_status = "SUPPLIER_REVISION_REQUIRED"
        open_tasks.append("MEDIA_FIX")
    elif final_decision == "NEED_TEXT_FIX":
        review_status = "SUPPLIER_REVISION_REQUIRED"
        open_tasks.append("TEXT_FIX")
    elif final_decision == "AUTO_FIX_PENDING_CONFIRMATION":
        review_status = "AUTO_FIX_PENDING"
        open_tasks.append("REVISION_CONFIRMATION")
    else:
        review_status = "PASSED"

    return {
        "final_decision": final_decision,
        "review_status": review_status,
        "open_tasks": open_tasks,
        "issues": issues,
    }
