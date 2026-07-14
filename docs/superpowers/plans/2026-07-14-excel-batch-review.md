# Excel 批量内容审核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Excel + 图片 ZIP 批量导入、预检确认、审核台表格/卡片双视图和审核结果 Excel 导出。

**Architecture:** 后端使用 `openpyxl` 解析/生成工作簿，ZIP 安全解析和图片文件名匹配由独立服务完成；预览文件存入临时导入目录并由随机 token 引用，确认后通过现有数据库服务事务性创建批次。前端默认批量上传和表格审核，复用现有内容详情、任务和 Diff API。

**Tech Stack:** Python 3.9、FastAPI、SQLAlchemy、openpyxl、zipfile、React 18、TypeScript、Vite。

## Global Constraints

- 主入口是 Excel + 可选图片 ZIP；单条表单仅作临时补录。
- 图片按 Excel 的“图片文件名”精确匹配，每条第一版最多一张图。
- Excel 最多 500 行；单图最大 20 MiB；ZIP 最大 200 MiB。
- 导入先预览，确认后才创建批次；确认必须幂等。
- 格式错误行保留在批次中，但不得进入自动审核。
- 审核台默认表格视图，同时保留卡片/详情视图。
- 导出保留原始字段并追加状态、问题、建议和最终稿。

---

### Task 1: Excel 模板、解析和 ZIP 安全预览服务

**Files:**
- Create: `server/services/excel_import_service.py`
- Modify: `requirements.txt`
- Test: `tests/test_excel_import.py`

**Interfaces:**
- Produces: `build_import_template() -> bytes`、`preview_import(xlsx_path, zip_path, temp_root) -> ImportPreview`、`load_preview(token) -> ImportPreview`。

- [ ] 编写失败测试，覆盖模板列、缺表头、重复编号、错误日期、缺图片、未引用图片警告、路径穿越和 500 行限制。
- [ ] 安装并声明 `openpyxl`。
- [ ] 实现模板生成、标准化行解析、ZIP 元数据安全检查、精确文件名匹配和临时 token 保存。
- [ ] 运行 `python3 -m pytest tests/test_excel_import.py -v`，预期全部通过。
- [ ] 提交独立 commit。

### Task 2: 确认导入和 Excel 导出服务

**Files:**
- Create: `server/services/excel_export_service.py`
- Modify: `server/services/excel_import_service.py`
- Modify: `server/services/content_service.py`
- Test: `tests/test_excel_workflow.py`

**Interfaces:**
- Produces: `confirm_import(session, token, supplier_id, batch_name) -> Batch`、`export_batch(session, batch_id) -> bytes`。

- [ ] 编写失败测试，覆盖多行确认、错误行保留、图片保存、重复确认幂等和完整导出列。
- [ ] 实现事务性确认导入，使用现有 `submit_batch`，按内容编号关联图片。
- [ ] 实现批次导出，聚合最新版本、最新审核问题、开放任务、人工决定和规则/模型信息。
- [ ] 设置 Excel 自动筛选、冻结首行、列宽和长文本自动换行。
- [ ] 运行 `python3 -m pytest tests/test_excel_workflow.py -v`，预期全部通过。
- [ ] 提交独立 commit。

### Task 3: FastAPI 批量导入、导出和扁平表格 API

**Files:**
- Modify: `server/main.py`
- Modify: `server/schemas.py`
- Test: `tests/test_excel_api.py`

**Interfaces:**
- Produces: `GET /api/import-template`、`POST /api/imports/preview`、`POST /api/imports/{token}/confirm`、`GET /api/batches/{id}/export`、`GET /api/contents/table`。

- [ ] 编写 HTTP 失败测试和完整预览→确认→审核→导出链路测试。
- [ ] 实现流式上传限制、临时文件清理、预览响应和幂等确认。
- [ ] 实现表格列表扁平响应和 `.xlsx` 文件下载响应。
- [ ] 运行 `python3 -m pytest tests/test_excel_api.py -v` 和全量 pytest。
- [ ] 提交独立 commit。

### Task 4: React 批量上传页

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/pages/Upload.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: Task 3 导入 API。

- [ ] 增加模板下载、Excel/ZIP 上传、预览、错误筛选和确认导入 API 类型。
- [ ] 默认展示批量导入，显示总数/有效/错误/警告和逐行预览。
- [ ] 将单条表单移入“临时补录”折叠区域。
- [ ] 运行 `npm run build`，预期 TypeScript 与 Vite 构建通过。
- [ ] 提交独立 commit。

### Task 5: 审核台表格/卡片双视图和导出

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/pages/Review.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/contents/table`、`GET /api/batches/{id}/export` 和现有详情/任务 API。

- [ ] 增加表格扁平数据和导出 API 类型。
- [ ] 默认表格视图，提供表格/卡片切换，展示状态、问题、最终稿和开放任务。
- [ ] 点击行加载现有详情和任务处理；批次操作区增加“导出审核结果”。
- [ ] 验证筛选、横向滚动和移动端布局。
- [ ] 运行 `npm run build`，预期通过。
- [ ] 提交独立 commit。

### Task 6: 最终集成、文档和推送

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `.gitignore`

- [ ] 文档补充模板字段、ZIP 命名规则、导入/审核/导出操作步骤。
- [ ] 启动真实前后端，用样例 Excel + ZIP 完成预览、确认、审核、双视图和导出。
- [ ] 打开导出工作簿验证行数、表头、最终标题/正文和问题列。
- [ ] 运行全量 pytest、Python compile、前端 build、secret scan 和 git diff check。
- [ ] 独立代码审查并修复 Critical/Important 问题。
- [ ] 推送 GitHub。
