"""合规/广告法子 agent。

确定性部分（不依赖 LLM，快且稳）：禁用词精确命中 -> high。
LLM 部分（可选）：判断政治敏感/色情低俗/暴力血腥/虚假信息/夸大功能/版权侵权/人身攻击。
"""
from __future__ import annotations

from .. import schema
from .base import DimensionReviewer, DimensionResult, max_risk, parse_json

_CATS = "政治敏感、色情低俗、暴力血腥、虚假信息、夸大产品功能、版权侵权、人身攻击"


class ComplianceReviewer(DimensionReviewer):
    key = "compliance"

    def review(self, row, standards, llm) -> DimensionResult:
        r = DimensionResult(dimension=self.key)
        text = self._text(row)

        # 确定性：禁用词
        hit = [w for w in standards.deny_words if w and w in text]
        if hit:
            r.risk_level = max_risk(r.risk_level, schema.RISK_HIGH)
            r.issues.append(f"命中禁用词：{'、'.join(hit)}")
            r.evidence.extend(hit)

        # LLM：广告法红线语义判断
        if llm is not None:
            prompt = f"""你是广告法合规审核员。依据下列合规标准，判断内容是否触及高风险表达（{_CATS}）。

【合规标准】
{standards.dim_context('compliance')}

内容：
{text}

只输出 JSON：{{"hit":true/false,"categories":[],"evidence":[],"reason":""}}"""
            d = parse_json(llm.chat(prompt))
            if d is None:
                r.risk_level = max_risk(r.risk_level, schema.RISK_UNKNOWN)
                r.issues.append("合规 LLM 输出无法解析，转人工")
                r.confidence = 0.0
            elif d.get("hit"):
                r.risk_level = max_risk(r.risk_level, schema.RISK_HIGH)
                cats = d.get("categories", [])
                r.issues.append(f"广告法风险：{'、'.join(cats) or d.get('reason', '')}")
                r.evidence.extend(d.get("evidence", []))
                r.confidence = 0.85
        elif not hit:
            # 无 LLM 且无禁用词命中：语义合规无法判断，弃权转人工（诚实）
            r.needs_llm = True
            r.risk_level = max_risk(r.risk_level, schema.RISK_UNKNOWN)
            r.issues.append("合规语义未审（未启用 LLM），转人工")
            r.confidence = 0.0
        return r
