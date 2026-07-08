"""审核模型入口 —— 保持对外接口 Verdict / get_reviewer 不变。

真正的审核逻辑在 reviewers/ 子包（多专项子 agent）：
  - reviewers/orchestrator.py  MultiAgentReviewer 汇总各维度
  - reviewers/{compliance,brand,accuracy,quality,external}.py  各维度子 agent
  - reviewers/llm.py  LLM 客户端（ernie / 离线）

引擎只依赖：
  reviewer.review(row, standards) -> Verdict
  reviewer.rewrite(row, standards) -> (title, body)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .reviewers.llm import get_llm
from .reviewers.orchestrator import MultiAgentReviewer


@dataclass
class Verdict:
    risk_level: str = "none"
    categories: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestion: str = ""
    media_issue: bool = False
    confidence: float = 1.0
    model: str = ""


def get_reviewer(backend: str):
    """backend:
    'oneapi' -> 多 agent + 公司 OneAPI 网关（推荐）
    'ernie'  -> 多 agent + 文心开放平台
    其它     -> 多 agent + 离线（语义维度转人工）
    """
    return MultiAgentReviewer(llm=get_llm(backend))
