"""内容准确性子 agent。

确定性部分：必带标签/话题是否齐全（standards.required_tags）。
LLM 部分（可选）：功能介绍、活动规则、优惠内容是否与项目标准一致。
"""
from __future__ import annotations

from .. import schema
from .base import DimensionReviewer, DimensionResult, max_risk, parse_json


class AccuracyReviewer(DimensionReviewer):
    key = "accuracy"

    def review(self, row, standards, llm) -> DimensionResult:
        r = DimensionResult(dimension=self.key)
        text = self._text(row)

        # 确定性：必带标签
        missing = [t for t in standards.required_tags if t and t not in text]
        if missing:
            r.risk_level = max_risk(r.risk_level, schema.RISK_MID)
            r.issues.append(f"缺少必带标签：{'、'.join(missing)}")

        # LLM：功能/规则/优惠准确性
        if llm is not None:
            prompt = f"""你是内容准确性审核员。依据下列项目标准，检查功能介绍、活动规则、优惠内容是否准确、有无与标准不符。

【项目标准】
{standards.dim_context('accuracy')}

内容：
标题：{row.get(schema.COL_TITLE, '')}
正文：{row.get(schema.COL_BODY, '')}

风险：与标准明显不符/编造=high；细节不清或轻微偏差=mid；无问题=none。
只输出 JSON：{{"risk_level":"none|mid|high","issues":[],"evidence":[],"suggestion":""}}"""
            d = parse_json(llm.chat(prompt))
            if d is None:
                r.risk_level = max_risk(r.risk_level, schema.RISK_UNKNOWN)
                r.issues.append("准确性 LLM 输出无法解析，转人工")
                r.confidence = 0.0
            else:
                lvl = d.get("risk_level", schema.RISK_NONE)
                if lvl not in (schema.RISK_NONE, schema.RISK_MID, schema.RISK_HIGH):
                    lvl = schema.RISK_UNKNOWN
                r.risk_level = max_risk(r.risk_level, lvl)
                r.issues.extend(d.get("issues", []))
                if d.get("suggestion"):
                    r.issues.append(d["suggestion"])
                r.evidence.extend(d.get("evidence", []))
                r.confidence = 0.85
        elif not missing:
            # 无 LLM 且标签齐全：功能/规则准确性无法语义判断，弃权转人工
            r.needs_llm = True
            r.risk_level = max_risk(r.risk_level, schema.RISK_UNKNOWN)
            r.issues.append("准确性语义未审（未启用 LLM），转人工")
            r.confidence = 0.0
        return r
