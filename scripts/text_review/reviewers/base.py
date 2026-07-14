"""维度子 agent 基类与通用工具。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .. import schema


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    quote: str
    start: Optional[int] = None
    end: Optional[int] = None
    asset_id: Optional[str] = None
    timestamp: Optional[str] = None


class AgentIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rule_id: str
    category: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    field: str
    evidence: EvidenceSpan
    reason: str
    suggestion: str
    source_reference: list[str]
    auto_fixable: bool
    human_required: bool
    confidence: float = Field(ge=0, le=1)


class AgentReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    agent_id: Literal[
        "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
        "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
    ]
    agent_version: str
    decision: Literal["PASS", "PASS_WITH_SUGGESTIONS", "NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"]
    summary: str
    score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    issues: list[AgentIssue]


@dataclass
class StructuredIssue:
    rule_id: str
    category: str
    severity: str
    field: str
    evidence_quote: str
    reason: str
    suggestion: str
    auto_fixable: bool
    human_required: bool
    confidence: float


@dataclass
class DimensionResult:
    dimension: str                       # schema.DIMENSIONS 的 key
    risk_level: str = schema.RISK_NONE
    issues: list[str] = field(default_factory=list)     # 问题点
    evidence: list[str] = field(default_factory=list)   # 证据/命中片段
    structured_issues: list[StructuredIssue] = field(default_factory=list)
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
