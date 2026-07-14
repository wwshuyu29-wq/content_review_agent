# 科技媒体测评审核系统 V0.9 设计

## 1. 目标

V0.9 首个正式内容类型为 `TECH_MEDIA_REVIEW`，面向科技媒体号或科技大厂号发布的小度想想亲测、实测和产品评测内容。

系统需要区分四类信息：

1. 官方产品事实；
2. 实际测试观察；
3. 作者主观评价；
4. 无法验证的行业结论。

首版目标不是自动发布，而是自动识别大部分事实、合规、证据和文案问题，生成可解释建议，并将真正需要人判断的问题集中到内部审核。

## 2. 已确认决策

- 保留 FastAPI、SQLAlchemy、React + Vite 和现有不可变版本模型。
- 不迁移 Next.js，不在 V0.9 引入 Celery、Redis 或完整多租户认证。
- 采用“结构化标准包 + 不可变数据库快照”。
- 默认 Profile 为 `TECH_MEDIA_REVIEW`；艺人合作规则迁移为独立、默认不加载的 Profile。
- 普通低风险内容允许代码仲裁通过；未确认功能、关键证据、数字、竞品结论和重大事实必须人工确认。
- Agent 只返回结构化判断，不修改数据库状态，不决定最终发布。
- V0.9 不开放自动发布，只开放白名单内的低风险自动修改建议。

## 3. 标准包

### 3.1 目录

```text
data/standards/
├── global/
│   ├── compliance.md
│   ├── brand_consistency.md
│   ├── content_accuracy.md
│   ├── test_credibility.md
│   ├── content_quality.md
│   └── campaign_effectiveness.md
├── projects/xiaoduxiangxiang_tech_review/
│   ├── project.yaml
│   ├── approved_claims.yaml
│   ├── evidence_requirements.yaml
│   ├── platform_requirements.yaml
│   └── project_context.md
├── rules/
│   ├── deterministic_rules.json
│   ├── term_dictionary.json
│   └── replacement_rules.json
└── schemas/
    ├── review_result.schema.json
    ├── project_standard.schema.json
    └── test_case.schema.json
```

### 3.2 隔离元数据

每个发布快照必须包含：

```json
{
  "business_domain": "baidu_maps_marketing_review",
  "document_type": "project_standard",
  "project_code": "bdmap_xdxx_tech_review_2026",
  "content_type": "TECH_MEDIA_REVIEW",
  "version": "0.9"
}
```

审核只能读取业务域、项目、内容类型和版本完全匹配的数据。原始对话、报告生成提示词和其他项目资料不得直接进入检索上下文。

### 3.3 发布流程

```text
编辑草稿
→ JSON Schema / Pydantic 校验
→ Claim、规则和来源引用交叉校验
→ 完整性检查
→ 版本 Diff
→ 发布不可变 RuleVersion
→ 审核绑定该快照
```

仓库文件是可维护源，数据库 `RuleVersion` 快照是运行时唯一依据。前端不能绕过编译和校验直接发布任意 JSON。

## 4. 项目事实边界

默认项目为 `bdmap_xdxx_tech_review_2026`，核心事实包括：

- 百度地图；
- 小度想想；
- 百度地图中的 AI 出行相关能力；
- 多点、多约束、多交通方式和时间窗口规划；
- 少换乘、少走路、低成本等偏好；
- 往返闭环需求；
- 步行中继续询问沿途信息和周边服务；
- 雨天、带娃、散步等场景偏好；
- 行程中继续增加地点或调整需求。

当前待确认并禁止自动放行：

- AI 订酒店；
- 自动筛选、比较酒店并判断最划算；
- 记住常去地点和出行习惯；
- 所有景点均有讲解；
- 所有城市、设备、网络环境均支持全部能力。

官方 Slogan 是核心传播口径，不等于每条内容必须逐字出现，也不能推导为产品能解决所有出行问题。

## 5. 内容和证据模型

### 5.1 内容字段

基础字段包括内容编号、项目、账号名称、账号类型、平台、标题、正文或脚本、计划发布时间和媒体资产。

科技测评字段包括：

- 是否使用实测、亲测或自用表述；
- 测试城市和时间；
- 百度地图版本；
- 设备、系统和网络环境；
- 声明的测试场景数量；
- 是否竞品对比及对比对象和方法。

### 5.2 TestCase

每个测试场景必须能独立追溯：

```json
{
  "test_case_id": "TEST-002",
  "claim": "输入两个目的地后生成了完整出行方案",
  "command": "实际输入给小度想想的内容",
  "observed_result": "实际返回结果",
  "evidence_asset_ids": ["asset_01", "asset_02"],
  "tested_at": "2026-07-14",
  "city": "北京"
}
```

`Asset` 保存文件元数据和存储引用；`TestEvidence` 保存测试场景与证据资产的绑定。证据缺失不自动判定造假，而是进入人工确认。

## 6. 四层审核

### 6.1 代码硬校验

负责文件、字段、日期、平台、重复内容、测试编号、测试数量、证据引用、资源存在性和 URL 等确定性问题，不调用模型。

### 6.2 确定性规则引擎

V0.9 支持：

- `exact_phrase`
- `phrase_list`
- `replacement_map`
- `count_consistency`
- `evidence_required`
- `required_term`

每条规则必须声明 `rule_id`、作用域、字段、Matcher、严重等级、动作、是否可自动修改和来源引用。禁止将 `semantic_topic` 退化为裸子字符串。

### 6.3 六 Agent

1. `COMPLIANCE`：合规和夸大表达；
2. `BRAND`：品牌名称、定位和调性；
3. `PRODUCT_ACCURACY`：功能事实和边界；
4. `TEST_CREDIBILITY`：实测证据和测评可信度；
5. `CONTENT_QUALITY`：文案结构、质量和测评感；
6. `CAMPAIGN_EFFECTIVENESS`：传播任务、聚焦度和平台适配。

格式检查和状态流转不是 Agent。

### 6.4 人工审核

处理未确认功能、关键证据缺失、疑似虚构、竞品比较、数字事实、证据与结论冲突以及模型无法可靠判断的内容。

## 7. Agent 协议

统一输出字段：

- `agent_id`、`agent_version`、`decision`、`summary`、`score`；
- `issues[].rule_id/category/severity/field`；
- `issues[].evidence.quote/start/end/asset_id/timestamp`；
- `issues[].reason/suggestion/source_reference`；
- `issues[].auto_fixable/human_required/confidence`。

模型参数建议为温度 `0-0.2`、强制 JSON Schema、最多重试 2 次。

审核运行按以下组合幂等：

```text
content_version_id + rule_version_id + agent_id + agent_version
```

问题按以下组合去重：

```text
rule_id + field + normalized_evidence + test_case_id
```

Agent 不得创建证据、补充功能、修改数字、改变状态或直接发布。

## 8. 仲裁与状态

### 8.1 仲裁

```text
CRITICAL、明确虚构测试或伪造证据
→ BLOCK

未确认功能、关键证据缺失、重大事实不确定
→ HUMAN_REVIEW_REQUIRED

中风险事实或文本问题
→ SUPPLIER_REVISION_REQUIRED

仅有允许自动修改的低风险问题
→ AUTO_FIX_PENDING

硬性审核通过，仅有传播或质量优化建议
→ PASS_WITH_SUGGESTIONS

没有阻塞问题
→ PASS
```

传播 Agent 的低分不直接等于合规不通过。

### 8.2 分离状态

```text
format_status:
PENDING | PASSED | INCOMPLETE | INVALID

review_status:
NOT_STARTED | AI_REVIEWING | HUMAN_REVIEW_REQUIRED
| SUPPLIER_REVISION_REQUIRED | AUTO_FIX_PENDING
| PASSED | PASSED_WITH_SUGGESTIONS | BLOCKED | REJECTED

publish_status:
NOT_READY | READY | PUBLISHED
```

`DRAFT`、`SUBMITTED`、`RE_REVIEWING` 等界面流程状态由版本、审核运行和开放任务推导。

普通低风险内容可由后端仲裁为 `PASSED`。只有无开放阻塞任务的 `PASSED` 或 `PASSED_WITH_SUGGESTIONS` 可以进入 `READY`。`PUBLISHED` 必须由有权限的人操作。

## 9. 自动修改边界

允许生成建议版本：

- 错别字和标点；
- 已确认品牌名替换；
- 重复表达；
- 明确绝对词的安全改写；
- 标题测试数量与正文编号对齐。

禁止自动补充或改变：

- 产品功能；
- 测试过程、结果和证据；
- 数字；
- 竞品结论；
- 未确认事实。

所有修改生成新 `ContentVersion`，不得覆盖供应商原稿。

## 10. Excel 批量流程

模板包含三个工作表：

1. `内容清单`：账号、平台、标题、正文、时间等；
2. `测试场景`：内容编号、测试编号、指令、结果、环境和证据文件名；
3. `字段说明`：仅供阅读。

证据 ZIP 按安全文件名精确匹配。预览阶段执行跨表校验：

- 内容和测试编号存在且唯一；
- 标题声明数量与测试场景数量一致；
- 使用实测触发词时至少有指令、结果和证据；
- 证据文件存在且绑定明确；
- 错误行保留，但不进入六 Agent 审核。

现有安全预览、确认导入、图片落盘、幂等和导出服务继续复用。尚未完成的旧 Excel API Task 3 并入新模型，不单独按旧字段继续开发。

## 11. React + Vite 页面

- 项目列表：内容类型、标准版本、供应商和状态统计；
- 批量上传：项目、Excel/ZIP、预检、测试场景、证据绑定、确认；
- 批次看板：表格筛选和单条详情；
- 人工审核台：左侧内容/Diff，中间证据，右侧六 Agent/任务；
- 标准管理：全局标准、项目事实、功能口径、实测证据、确定性规则、平台要求；
- 报告中心：证据缺失率、待确认功能、返工次数、Agent 命中、人工纠错和建议采纳。

页面保持工作台式布局，不改为营销落地页。

## 12. 模块边界

```text
standard_package      标准读取、校验、编译和发布
review_profiles       按 content_type 选择 Agent 和策略
deterministic_engine  确定性 Matcher
tech_media_agents     六 Agent 结构化审核
evidence_service      测试场景和证据绑定
review_arbiter        去重、分级、任务和状态
workflow_service      提交、返修、重审、通过和发布
excel_import/export   内容、测试场景和结果表格
```

## 13. 迁移顺序

1. 标准包、Schema、默认项目和危险 seed 清理；
2. 内容类型 Profile、规则引擎、统一协议和六 Agent；
3. 测试场景、证据模型、任务和状态闭环；
4. 多工作表 Excel 与 FastAPI 接口；
5. React 上传、看板、审核台、标准和报告；
6. 真实稿件回归、误报统计和灰度。

## 14. V0.9 非目标

- 自动发布；
- 自动认可待确认功能；
- 自动生成测试或证据；
- 视频理解和多模态模型处理；
- 完整多租户、组织权限和公司统一登录；
- Redis/Celery 分布式队列；
- 自动规则沉淀和自动发布规则。

## 15. 验收标准

- 默认科技测评项目不加载任何艺人规则；
- 标准包可校验、编译并发布不可变版本；
- 危险子字符串规则不再导致“不是代言人”等误报；
- 六 Agent 使用统一 Schema，保留来源引用和证据位置；
- 实测触发词能够检查测试指令、结果和证据；
- 未确认功能和关键证据进入人工任务；
- 传播建议不会错误阻断事实合规内容；
- Excel 可导入内容和测试场景并精确绑定证据；
- 审核页面可查看原稿、证据、六 Agent、问题、Diff 和人工决定；
- 所有内容版本、规则版本、审核运行和人工决定可追溯；
- 后端全量测试和前端生产构建通过。
