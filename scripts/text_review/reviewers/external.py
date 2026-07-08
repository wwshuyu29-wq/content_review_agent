"""舆情与授权子 agent（需外部数据）。

  - 明星/IP/第三方品牌：命中 must_human_keywords -> 需人工核对是否有授权/符合审核条件
  - 近2周风险舆情：本期无实时数据源（微博抓取后接），命中舆情风险词同样转人工

这是真正"需要外部数据/工具"的子 agent，本期设计为命中即 unknown 转人工，
不做自动判断（诚实：没有授权清单/舆情数据就不能替人拍板）。
"""
from __future__ import annotations

from .. import schema
from .base import DimensionReviewer, DimensionResult, max_risk


class ExternalReviewer(DimensionReviewer):
    key = "external"

    def review(self, row, standards, llm) -> DimensionResult:
        r = DimensionResult(dimension=self.key, confidence=0.9)
        text = self._text(row)

        hit_human = [w for w in standards.must_human_keywords if w and w in text]
        if hit_human:
            r.risk_level = max_risk(r.risk_level, schema.RISK_UNKNOWN)
            r.issues.append(
                f"涉及需人工确认关键词：{'、'.join(hit_human)}，"
                f"请核对是否符合项目授权/近2周舆情"
            )
            r.evidence.extend(hit_human)
        return r
