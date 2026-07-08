"""维度子 agent 基类与通用工具。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import schema


@dataclass
class DimensionResult:
    dimension: str                       # schema.DIMENSIONS 的 key
    risk_level: str = schema.RISK_NONE
    issues: list[str] = field(default_factory=list)     # 问题点
    evidence: list[str] = field(default_factory=list)   # 证据/命中片段
    confidence: float = 1.0
    needs_llm: bool = False               # True=该维度需 LLM 但当前不可用（弃权）


_RISK_ORDER = {
    schema.RISK_NONE: 0, schema.RISK_LOW: 1, schema.RISK_MID: 2,
    schema.RISK_UNKNOWN: 3, schema.RISK_HIGH: 4,
}


def max_risk(a: str, b: str) -> str:
    return a if _RISK_ORDER[a] >= _RISK_ORDER[b] else b


def parse_json(text: str) -> dict | None:
    """从 LLM 回复里抽第一个 JSON 块。"""
    text = (text or "").strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1:
        try:
            return json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            return None
    return None


class DimensionReviewer:
    """维度子 agent 基类。子类实现 review()。"""
    key: str = ""

    def review(self, row: dict, standards, llm) -> DimensionResult:
        raise NotImplementedError

    # 便捷：取标题+正文
    @staticmethod
    def _text(row: dict) -> str:
        return f"{row.get(schema.COL_TITLE, '')}\n{row.get(schema.COL_BODY, '')}".strip()
