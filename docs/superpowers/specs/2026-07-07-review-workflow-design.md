# 内容审核工作流数据库化设计

## 目标

今晚交付一套可在本机完整演示、后续可部署到服务器的内容审核系统：项目与标准版本、供应商上传批次、内容版本、多 Agent 结构化审核、代码仲裁、AI 修改建议与 Diff、人工确认、审核报告。

## 范围

- 数据库：SQLite 实际落地，SQLAlchemy 模型兼容 PostgreSQL，连接由 `DATABASE_URL` 控制。
- 前端：保留 React + Vite + TypeScript，升级现有四页。
- 后端：保留 FastAPI 和当前 OneAPI 多 Agent 实现。
- 权限：今晚不做登录和 RBAC，API 边界保留。
- 延后：腾讯文档、SSO、Redis/Celery、视频审核、如流通知、BOS。

## 核心实体

- `Project`：项目基本信息、当前标准版本。
- `RuleVersion`：不可变标准版本，包含分维度标准、项目事实与结构化规则。
- `Batch`：一次供应商提交批次。
- `ContentItem`：内容主记录，包含 `format_status`、`review_status`、`publish_status`。
- `ContentVersion`：不可变内容版本，区分供应商原稿、AI 建议稿、人工确认稿。
- `AuditRun`：一次审核运行，记录模型、Prompt 版本、规则版本和状态。
- `AgentResult`：各专项 Agent 的结构化原始结果。
- `Issue`：规则、字段、证据、原因、建议、风险、置信度、可否自动修复、是否需人工。
- `ReviewTask`：人工风险审核、AI 建议确认、供应商修改等开放任务。
- `HumanDecision`：人工处理结论和备注。

## 状态模型

```text
format_status: PENDING | PASSED | INCOMPLETE | INVALID
review_status: NOT_STARTED | AI_REVIEWING | MANUAL_REQUIRED | FIX_PROPOSED | APPROVED | REJECTED
publish_status: NOT_READY | READY | PUBLISHED
```

一条内容可同时存在多个开放任务，避免单一状态覆盖文案、素材和人工确认问题。

## 规则与事实

每次审核固定引用一个不可变 `RuleVersion`。版本内容包括：

- 五个维度的全局标准；
- 项目事实库；
- 禁用词、推荐表达、必须人工确认词、必带标签；
- Prompt 版本。

首个项目预置“百度地图小度想想 × 范丞丞短期合作”，明确范丞丞不是代言人、官方 Slogan 和已确认功能点。

## 审核流程

1. 创建项目和标准版本。
2. 供应商上传产生批次、内容和 V1。
3. 代码执行字段/文件格式校验。
4. 多 Agent 对 V1 和固定规则版本审核。
5. Agent 返回结构化问题，代码仲裁器决定状态和开放任务。
6. 只有纯低风险且全部问题可安全修复时，生成 AI 建议版本 V2，状态为 `FIX_PROPOSED`，不自动通过。
7. 人工接受、编辑后接受或拒绝建议；接受后生成确认版本并置 `APPROVED/READY`。
8. 高风险、事实不足、授权、合作身份、合规问题始终创建人工任务。

## Agent 输出

每个问题必须保存：`rule_id`、`category`、`severity`、`field`、`evidence_quote`、`reason`、`suggestion`、`auto_fixable`、`human_required`、`confidence`。

LLM 负责理解和建议；代码负责状态、优先级、审计和最终分流。

## 前端

- 供应商上传：选择项目、创建批次、上传内容和图片、查看格式结果。
- 审核台：项目/批次/状态过滤；查看结构化问题和 Agent 证据；处理人工任务；一键审核。
- 标准管理：项目创建、标准编辑、发布新版本、查看历史版本。
- 报告：项目与批次统计、问题分类、规则命中、人工审核占比。
- 修改建议：显示原文与建议稿 Diff，支持接受、编辑后接受、拒绝。

## 验收

- SQLite 数据持久化，`DATABASE_URL` 可切 PostgreSQL。
- FastAPI `/docs` 展示正式 API。
- 真实 OneAPI 审核可产生结构化问题。
- 低风险产生建议稿和 Diff，未确认前不通过。
- 人工确认后生成新内容版本并变为可发布。
- 后端测试、前端类型检查与生产构建通过。
