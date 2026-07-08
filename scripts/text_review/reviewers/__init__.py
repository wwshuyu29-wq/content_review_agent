"""多专项子 agent 审核层。

把审核拆成多个专项子 agent，每个只管一个维度、各自吃对应的标准切片：
  - compliance  合规/广告法（确定性禁用词 + LLM 红线判断）
  - brand       品牌一致性（LLM，吃品牌标准）
  - accuracy    内容准确性（确定性必带标签 + LLM，吃项目标准）
  - quality     内容质量（确定性基础检查 + LLM 润色判断）
  - external    舆情与授权（明星/IP/第三方/近2周舆情 —— 需外部数据，本期命中即转人工）

orchestrator 汇总各子 agent 结果 -> 总 Verdict，交给状态机引擎。

诚实原则：语义维度（品牌/准确性）没有 LLM 时无法真正判断，会"弃权并标注"
（needs_llm=True, risk=unknown）转人工，绝不在无 LLM 时假装通过。
"""
