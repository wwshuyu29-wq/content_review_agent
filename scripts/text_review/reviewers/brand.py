"""品牌一致性子 agent（纯语义，依赖 LLM）。

检查品牌名/功能名/调性是否正确、是否符合项目品牌口径、有无与品牌定位不一致的表达。
无 LLM 时弃权转人工（不假装通过）。
"""
from __future__ import annotations

from .. import schema
from .base import DimensionReviewer, DimensionResult, parse_json


class BrandReviewer(DimensionReviewer):
    key = "brand"

    def review(self, row, standards, llm) -> DimensionResult:
        r = DimensionResult(dimension=self.key)
        if llm is None:
            r.needs_llm = True
            r.risk_level = schema.RISK_UNKNOWN
            r.issues.append("品牌一致性未审（未启用 LLM），转人工")
            r.confidence = 0.0
            return r

        prompt = f"""你是品牌审核员。依据下列品牌标准，检查内容的品牌名、功能名、调性是否正确，
是否符合品牌口径，有无与品牌定位不一致的表达。

【品牌标准】
{standards.dim_context('brand')}

内容：
标题：{row.get(schema.COL_TITLE, '')}
正文：{row.get(schema.COL_BODY, '')}

风险：命中明显违背品牌口径=high；轻微不一致/建议优化=mid；无问题=none。
只输出 JSON：{{"risk_level":"none|mid|high","issues":[],"evidence":[],"suggestion":""}}"""
        d = parse_json(llm.chat(prompt))
        if d is None:
            r.risk_level = schema.RISK_UNKNOWN
            r.issues.append("品牌 LLM 输出无法解析，转人工")
            r.confidence = 0.0
            return r
        lvl = d.get("risk_level", schema.RISK_NONE)
        r.risk_level = lvl if lvl in (schema.RISK_NONE, schema.RISK_MID, schema.RISK_HIGH) else schema.RISK_UNKNOWN
        r.issues = d.get("issues", [])
        if d.get("suggestion"):
            r.issues.append(d["suggestion"])
        r.evidence = d.get("evidence", [])
        r.confidence = 0.85
        return r
