"""orchestrator：跑全部维度子 agent，汇总成总 Verdict。"""
from __future__ import annotations

import re

from .. import schema
from .accuracy import AccuracyReviewer
from .base import DimensionResult, max_risk
from .brand import BrandReviewer
from .compliance import ComplianceReviewer
from .external import ExternalReviewer
from .quality import QualityReviewer


class MultiAgentReviewer:
    """多专项子 agent 审核器。llm=None 时离线（语义维度弃权转人工）。"""

    def __init__(self, llm=None):
        self.llm = llm
        self.name = f"multi-agent-v1({getattr(llm, 'name', 'offline')})"
        self.agents = [
            ComplianceReviewer(),
            BrandReviewer(),
            AccuracyReviewer(),
            QualityReviewer(),
            ExternalReviewer(),
        ]

    def review(self, row: dict, standards):
        from ..reviewer import Verdict  # 延迟导入避免环依赖

        results: list[DimensionResult] = [a.review(row, standards, self.llm) for a in self.agents]

        overall = schema.RISK_NONE
        categories, issues, evidence = [], [], []
        min_conf = 1.0
        for res in results:
            overall = max_risk(overall, res.risk_level)
            if res.issues:
                cn = schema.DIMENSIONS.get(res.dimension, res.dimension)
                categories.append(cn)
                issues.append(f"[{cn}] " + "；".join(res.issues))
            evidence.extend(res.evidence)
            min_conf = min(min_conf, res.confidence)

        return Verdict(
            risk_level=overall,
            categories=categories,
            issues=issues,
            suggestion="；".join(issues),
            media_issue=False,
            confidence=round(min_conf, 3),
            model=self.name,
        )

    def rewrite(self, row: dict, standards) -> tuple[str, str]:
        """低风险自动改写。有 LLM 用 LLM，否则做保守的规则清理。"""
        title = (row.get(schema.COL_TITLE) or "").strip()
        body = (row.get(schema.COL_BODY) or "").strip()

        if self.llm is not None:
            from .base import parse_json
            prompt = f"""依据下列标准，对这条低风险内容做文案润色（改错别字/语病/表达，不改原意、不夸大）。

{standards.dim_context('quality')}

标题：{title}
正文：{body}

只输出 JSON：{{"title":"","body":""}}"""
            d = parse_json(self.llm.chat(prompt))
            if d:
                return d.get("title", title), d.get("body", body)

        # 规则兜底：推荐表达替换 + 清理重复标点/多余空白
        for k, v in standards.recommended.items():
            title = title.replace(k, v)
            body = body.replace(k, v)
        body = re.sub(r"([！!？?。.])\1{2,}", r"\1", body)
        body = re.sub(r"(.)\1{4,}", r"\1\1", body)
        body = re.sub(r"[ \t]{2,}", " ", body).strip()
        return title, body
