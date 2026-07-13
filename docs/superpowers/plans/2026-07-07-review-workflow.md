# 内容审核工作流数据库化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 CSV 原型升级为 SQLite/SQLAlchemy 驱动的项目、批次、内容版本、结构化审核、AI 建议稿确认和报告系统。

**Architecture:** FastAPI 通过 SQLAlchemy repository 管理持久化数据；现有五维审核器升级为统一结构化输出，服务层负责规则快照、代码仲裁与版本创建；React 继续使用 Vite/TS，通过新 API 展示项目、批次、问题、Diff 和报告。

**Tech Stack:** Python 3.9、FastAPI、SQLAlchemy 2.x、SQLite/PostgreSQL、Pydantic、React 18、TypeScript、Vite。

## Global Constraints

- 今晚本机完整跑通，SQLite 实际落地，`DATABASE_URL` 可切 PostgreSQL。
- 暂不实现登录/RBAC、腾讯文档、视频、Redis/Celery、SSO、BOS、如流通知。
- AI 只能生成建议版本，人工确认前不得通过。
- 高风险、事实不足、合作身份与授权问题始终人工兜底。
- 每次审核必须记录内容版本、规则版本、模型和 Prompt 版本。

---

### Task 1: 数据库模型与种子项目

**Files:**
- Create: `server/db.py`
- Create: `server/models.py`
- Create: `server/seed.py`
- Create: `server/schemas.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: `get_session()`, SQLAlchemy entities, `seed_default_project(session)`。

- [ ] 安装 SQLAlchemy 和 pytest。
- [ ] 编写数据库初始化、实体关系和 SQLite 测试。
- [ ] 预置“小度想想 × 范丞丞短期合作”项目及首个不可变标准版本。
- [ ] 运行 `pytest tests/test_database.py -v`，预期全部通过。

### Task 2: 结构化审核、仲裁与版本服务

**Files:**
- Create: `server/services/review_service.py`
- Create: `server/services/content_service.py`
- Create: `server/services/report_service.py`
- Modify: `scripts/text_review/reviewers/base.py`
- Modify: `scripts/text_review/reviewers/orchestrator.py`
- Test: `tests/test_review_workflow.py`

**Interfaces:**
- Consumes: SQLAlchemy entities、`MultiAgentReviewer`。
- Produces: `submit_batch()`, `run_audit()`, `resolve_task()`, `build_report()`。

- [ ] 统一问题结构：规则、字段、证据、风险、建议、是否可自动修复、是否需人工。
- [ ] 实现批次、V1、格式校验、审核运行、Agent 结果和问题落库。
- [ ] 实现代码仲裁：高风险/不确定转人工；纯低风险生成建议 V2 和确认任务；无问题通过。
- [ ] 实现接受/编辑接受/拒绝建议，确认后生成新版本和可发布状态。
- [ ] 运行 `pytest tests/test_review_workflow.py -v`，覆盖高风险、低风险建议、人工确认和版本历史。

### Task 3: FastAPI 新接口与兼容入口

**Files:**
- Rewrite: `server/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `/api/projects`、`/api/batches`、`/api/contents`、`/api/audit-runs`、`/api/review-tasks`、`/api/reports`、`/api/config`。

- [ ] 实现项目/标准版本/批次/内容/版本/审核/任务/报告接口。
- [ ] 上传接口保存图片到本地并创建批次和内容 V1。
- [ ] 模型 key 只从环境变量读取。
- [ ] 运行 `pytest tests/test_api.py -v`，覆盖完整 HTTP 主链路。

### Task 4: React 页面升级

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/pages/Upload.tsx`
- Modify: `web/src/pages/Review.tsx`
- Modify: `web/src/pages/Standards.tsx`
- Modify: `web/src/pages/Report.tsx`
- Create: `web/src/components/DiffView.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: Task 3 API。

- [ ] 上传页支持项目选择和批次结果。
- [ ] 审核台支持项目/批次/状态过滤、结构化问题和人工任务处理。
- [ ] 增加原文/建议稿 Diff 与接受、编辑接受、拒绝操作。
- [ ] 标准管理支持项目、当前版本、发布新版本和历史版本。
- [ ] 报告按项目统计状态、问题类别、规则命中与人工占比。
- [ ] 运行 `npm run build`，预期 TypeScript 与 Vite 构建通过。

### Task 5: 启动、迁移与最终验收

**Files:**
- Create: `requirements.txt`
- Create: `scripts/start_local.sh`
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] 补全依赖、环境变量和本机启动脚本。
- [ ] 启动 FastAPI 和前端，使用 HTTP 完成项目→上传→审核→人工确认→报告链路。
- [ ] 运行全量 pytest、前端 build、密钥扫描和 git diff 检查。
- [ ] 提交并推送 GitHub，记录服务器部署所需环境变量与 PostgreSQL 切换方式。
