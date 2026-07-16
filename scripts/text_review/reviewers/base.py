"""评分维度结果模型与通用工具。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def allows_unavailable_score(decision: Any, issues: list[Any]) -> bool:
    """Return whether a controlled system-unavailable result may use a null score."""
    return (
        str(decision).upper() in {"HUMAN_REVIEW", "PASS_WITH_SUGGESTIONS"}
        and any(
            (issue.get("rule_id") if isinstance(issue, dict) else getattr(issue, "rule_id", None))
            == "SYSTEM-LLM-UNAVAILABLE"
            for issue in issues
        )
    )


class AgentReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    agent_id: Literal[
        "CONTENT_QUALITY", "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY",
        "CAMPAIGN_EFFECTIVENESS",
    ]
    agent_version: str
    decision: Literal["PASS", "PASS_WITH_SUGGESTIONS", "NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"]
    summary: str
    score: Optional[int] = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    issues: list[AgentIssue]

    @model_validator(mode="after")
    def require_score_for_model_results(self) -> "AgentReviewResult":
        if self.score is None and not allows_unavailable_score(self.decision, self.issues):
            raise ValueError("score may be null only for unavailable system results")
        return self


class DimensionReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    results: list[AgentReviewResult]

    @model_validator(mode="after")
    def require_complete_dimension_set(self) -> "DimensionReviewBatch":
        expected = tuple(get_args(AgentReviewResult.model_fields["agent_id"].annotation))
        actual = tuple(result.agent_id for result in self.results)
        if actual != expected:
            raise ValueError(f"results must contain the five scoring dimensions in order: {expected}")
        return self


def agent_review_protocol_contract() -> dict[str, Any]:
    """Return the strict runtime protocol used to validate the committed JSON Schema."""
    return {
        "result_fields": tuple(AgentReviewResult.model_fields),
        "issue_fields": tuple(AgentIssue.model_fields),
        "evidence_fields": tuple(EvidenceSpan.model_fields),
        "evidence_required_fields": tuple(
            name for name, field_info in EvidenceSpan.model_fields.items() if field_info.is_required()
        ),
        "agent_ids": tuple(get_args(AgentReviewResult.model_fields["agent_id"].annotation)),
        "decisions": tuple(get_args(AgentReviewResult.model_fields["decision"].annotation)),
        "severities": tuple(get_args(AgentIssue.model_fields["severity"].annotation)),
    }


def agent_review_protocol_schema() -> dict[str, Any]:
    """Return Pydantic's complete JSON Schema for runtime-significant comparison."""
    return AgentReviewResult.model_json_schema()


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
