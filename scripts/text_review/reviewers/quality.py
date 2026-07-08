"""内容质量子 agent。

确定性部分：标题缺失、正文过短、重复标点/字符、多余空白。
LLM 部分（可选）：错别字、语病、表达不自然（属低风险，可自动改写）。
"""
from __future__ import annotations

import re

from .. import schema
from .base import DimensionReviewer, DimensionResult, max_risk, parse_json


class QualityReviewer(DimensionReviewer):
    key = "quality"

    def review(self, row, standards, llm) -> DimensionResult:
        r = DimensionResult(dimension=self.key, confidence=0.7)
        title = (row.get(schema.COL_TITLE) or "").strip()
        body = (row.get(schema.COL_BODY) or "").strip()

        det = []
        if not title:
            det.append("标题缺失")
        if len(body) < 10:
            det.append("正文过短，信息量不足")
        if re.search(r"([！!？?。.]{3,})", body):
            det.append("存在重复标点")
        if re.search(r"(.)\1{4,}", body):
            det.append("存在重复字符")
        if re.search(r"\s{3,}", body):
            det.append("存在多余空白")
        if det:
            r.risk_level = max_risk(r.risk_level, schema.RISK_LOW)
            r.issues.extend(det)

        if llm is not None:
            prompt = f"""你是文案质量审核员。检查错别字、语病、重复表达、表达不自然。这类问题属低风险。

内容：
标题：{title}
正文：{body}

只输出 JSON：{{"has_issue":true/false,"issues":[]}}"""
            d = parse_json(llm.chat(prompt))
            if d and d.get("has_issue"):
                r.risk_level = max_risk(r.risk_level, schema.RISK_LOW)
                r.issues.extend(d.get("issues", []))
                r.confidence = 0.85
        return r
