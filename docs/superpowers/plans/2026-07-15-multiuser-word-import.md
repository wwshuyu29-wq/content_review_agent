# 多用户审核与 Word 批量导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付管理员建号、用户登录、用户级加密 OneAPI 与模型选择、全链路数据隔离和多 Word 一键导入的内部团队公网 MVP。

**Architecture:** 认证与凭证逻辑拆为独立服务，FastAPI 使用会话依赖向所有业务路由注入当前用户；业务所有权以 `Project.owner_user_id` 为根并在关键下游表冗余，服务层和数据库升级共同校验。Word 导入复用 Excel 的随机预览 token、身份绑定和事务确认模式，但使用独立 DOCX 安全解析器。

**Tech Stack:** Python 3.9、FastAPI、SQLAlchemy 2、Pydantic 2、Argon2id、cryptography、SQLite/PostgreSQL、React 18、Vite、TypeScript、pytest。

## Global Constraints

- 不开放公众注册；只有管理员创建、停用和重置账号。
- 密码只存 Argon2id 哈希；Session Cookie 为 HttpOnly、SameSite=Lax，生产环境 Secure。
- 数据库只存 Session token/CSRF token 哈希。
- 用户 OneAPI Key 使用 `CREDENTIAL_ENCRYPTION_KEY` 认证加密；API、日志和前端不得返回明文或密文。
- OneAPI 基址只能由服务端环境配置，用户不能提交 URL。
- 业务对象越权统一返回 404；角色不足返回 403。
- 每次审核固化 actor、模型、凭证指纹、标准快照、Prompt 和 Agent 版本。
- 旧数据归属初始管理员；父子所有权不一致时启动失败。
- Word 一次 1–20 个 `.docx`；拒绝 `.doc`、`.docm`、外部关系、加密、损坏和超限文档。
- Word 预览 token 绑定用户、项目、供应商和批次，确认幂等且事务化。
- 所有行为先写失败测试；完成前运行全量后端、前端构建和真实 HTTP 主链路。

---

### Task 1: 用户、密码和安全会话

**Files:**
- Modify: `requirements.txt`
- Modify: `server/models.py`
- Modify: `server/db.py`
- Create: `server/services/auth_service.py`
- Modify: `server/main.py`
- Modify: `scripts/start_local.sh`
- Test: `tests/test_auth.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: `hash_password(password) -> str`, `verify_password(hash, password) -> bool`.
- Produces: `create_session(session, user, *, ttl) -> SessionSecrets`, `authenticate_request(request, session) -> User`.
- Produces: `require_user`, `require_admin`, `require_csrf` FastAPI dependencies.

- [ ] **Step 1: Write failing auth and migration tests**

Cover Argon2id hashing, invalid password, disabled user, session expiry/revocation, token hashes, cookie attributes, CSRF rejection, no public registration, admin-only user creation, and initial-admin migration.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_auth.py tests/test_database.py -q`
Expected: missing user/session tables and auth endpoints.

- [ ] **Step 3: Implement models and schema upgrades**

Add `User` and `UserSession`; use idempotent SQLite/PostgreSQL-compatible column/table/index upgrades. Require `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_PASSWORD`, and `SESSION_SECRET` only when no users exist; never log their values.

- [ ] **Step 4: Implement authentication endpoints and dependencies**

Implement `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, and admin user list/create/disable/reset. Use a constant-time generic login error.

- [ ] **Step 5: Secure local startup configuration**

Load ignored `.env` in `start_local.sh`; validate required secrets without printing values. Keep `.env` ignored.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_auth.py tests/test_database.py tests/test_api.py -q`
Expected: PASS.

Commit: `Add secure team authentication`

---

### Task 2: 用户级加密 OneAPI 与模型选择

**Files:**
- Modify: `server/models.py`
- Modify: `server/db.py`
- Create: `server/services/credential_service.py`
- Modify: `scripts/text_review/reviewers/llm.py`
- Modify: `server/main.py`
- Modify: `server/services/review_service.py`
- Test: `tests/test_credentials.py`
- Test: `tests/test_tech_media_agents.py`
- Test: `tests/test_review_workflow.py`

**Interfaces:**
- Produces: `encrypt_api_key`, `decrypt_api_key`, `credential_fingerprint`.
- Produces: `list_oneapi_models(api_key, base_url) -> list[str]`.
- Produces: `OpenAICompatLLM(api_key: str, model: str, base_url: str)` without process-global environment mutation.

- [ ] **Step 1: Write failing credential tests**

Cover encrypted round-trip, random nonce, wrong master key, redacted API responses, model endpoint errors, model selection validation, per-user separation, and key absence from logs/response bodies.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_credentials.py tests/test_tech_media_agents.py -q`
Expected: missing credential service and user endpoints.

- [ ] **Step 3: Implement credential storage and APIs**

Add one credential row per user. Implement PUT/DELETE credential, GET models, and PATCH default model. Validate the Key against the trusted server-side base URL before saving.

- [ ] **Step 4: Make LLM configuration instance-scoped**

Remove runtime `os.environ` mutation from review requests. Construct reviewer instances from current-user credentials. Preserve strict JSON Schema and fail-closed behavior.

- [ ] **Step 5: Persist actual review identity**

Add actor user and credential fingerprint to `AuditRun`; preserve actual model, Prompt, rule and Agent versions.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_credentials.py tests/test_tech_media_agents.py tests/test_review_workflow.py tests/test_api.py -q`
Expected: PASS.

Commit: `Add user OneAPI credentials and models`

---

### Task 3: 全链路用户所有权与越权防护

**Files:**
- Modify: `server/models.py`
- Modify: `server/db.py`
- Modify: `server/seed.py`
- Modify: `server/services/content_service.py`
- Modify: `server/services/evidence_service.py`
- Modify: `server/services/review_service.py`
- Modify: `server/services/report_service.py`
- Modify: `server/services/excel_import_service.py`
- Modify: `server/services/excel_export_service.py`
- Modify: `server/main.py`
- Test: `tests/test_tenant_isolation.py`
- Modify: existing API/workflow tests.

**Interfaces:**
- Produces: ownership-aware service signatures with required `owner_user_id`.
- Produces: `owned_project`, `owned_batch`, `owned_content`, `owned_task` query helpers.

- [ ] **Step 1: Write two-user IDOR tests**

For every list/detail/create/update/download/preview/confirm/review/task/report endpoint, create Alice and Bob data and assert Bob gets 404 for Alice IDs and cannot infer file paths or token validity.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_tenant_isolation.py -q`
Expected: current unauthenticated/global queries expose cross-user data.

- [ ] **Step 3: Add ownership columns and migration**

Add owner to project and critical descendants. Backfill old rows to initial admin in parent-first order; validate every descendant matches its root owner. Change project code/name uniqueness to user scope.

- [ ] **Step 4: Enforce ownership in services and routes**

Filter ownership in the SQL query, not after loading. Bind Excel preview manifests and idempotency tokens to owner. Gate media and exports after ownership lookup.

- [ ] **Step 5: Seed per-user default project**

When an account first needs review data, publish the current V1.0 package to that user's own default project without mutating another user's project.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_tenant_isolation.py tests/test_api.py tests/test_excel_api.py tests/test_excel_workflow.py tests/test_evidence_workflow.py tests/test_review_workflow.py -q`
Expected: PASS.

Commit: `Enforce review data tenant isolation`

---

### Task 4: 安全 DOCX 多文件预览与一键导入

**Files:**
- Create: `server/services/docx_import_service.py`
- Modify: `server/main.py`
- Modify: `server/services/content_service.py`
- Create: `tests/test_docx_import.py`
- Create: `tests/test_docx_api.py`

**Interfaces:**
- Produces: `preview_docx_import(files, temp_root, identity) -> DocxImportPreview`.
- Produces: `confirm_docx_import(session, token, owner_user_id, project_id, supplier_id, batch_name) -> Batch`.

- [ ] **Step 1: Write failing DOCX tests**

Create OOXML fixtures in tests. Cover Title style, first-paragraph fallback, ordered paragraphs/tables, 10-file import, duplicate stems, empty body, `.doc/.docm`, encrypted/corrupt ZIP, traversal, external relationships, compression ratio, entry count, size limits, expired token, cross-user token, idempotent confirm and rollback.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_docx_import.py tests/test_docx_api.py -q`
Expected: module/endpoints missing.

- [ ] **Step 3: Implement safe OOXML parser**

Use `zipfile` and `xml.etree.ElementTree` with explicit OOXML namespaces; never execute macros or resolve external relationships. Enforce all limits before XML parsing.

- [ ] **Step 4: Implement restart-safe preview and confirmation**

Persist manifest with owner identity and TTL. Use random token, exact directory containment, atomic manifest write, one-time/idemponent confirmation and database transaction.

- [ ] **Step 5: Add FastAPI endpoints**

Implement `POST /api/docx-imports/preview` and `POST /api/docx-imports/{token}/confirm` with 1–20 multipart `.docx` files and common metadata.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_docx_import.py tests/test_docx_api.py tests/test_tenant_isolation.py -q`
Expected: PASS.

Commit: `Add secure Word batch import`

---

### Task 5: React 登录、账号模型设置和 Word 上传

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/App.tsx`
- Create: `web/src/AuthContext.tsx`
- Create: `web/src/pages/Login.tsx`
- Create: `web/src/pages/Account.tsx`
- Create: `web/src/pages/AdminUsers.tsx`
- Modify: `web/src/pages/Upload.tsx`
- Modify: `web/src/pages/Review.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: auth, admin user, credential/model, DOCX preview/confirm and tenant-scoped APIs.
- Produces: authenticated routing and account-scoped operational UI.

- [ ] **Step 1: Extend typed API client**

Use `credentials: "include"` for all calls, attach CSRF header for mutations, normalize 401, and define exact auth/credential/DOCX contracts.

- [ ] **Step 2: Implement authentication shell**

Add login, current-user bootstrap, logout, protected routes and admin-only route. Never store session token or OneAPI Key in localStorage.

- [ ] **Step 3: Implement account and admin pages**

Provide password-safe account creation/reset, masked credential status, replace/delete Key, refresh models and choose default model. Clear Key input immediately after successful save.

- [ ] **Step 4: Implement Word import tab**

Add Excel/Word segmented control, 1–20 multi-file selection, common metadata, per-file preview table, errors and one-click confirm. Preserve Excel flow.

- [ ] **Step 5: Remove global model controls**

Review page shows current user's configured model and routes missing configuration to Account; it cannot modify server-global config.

- [ ] **Step 6: Verify and commit**

Run: `npm run build` in `web`.
Expected: TypeScript and Vite production build pass.

Commit: `Build authenticated review workspace`

---

### Task 6: 安全、回归和真实主链路

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `.env.example` only if it already exists; otherwise document variables in README.

- [ ] **Step 1: Run focused security suites**

Run: `python3 -m pytest tests/test_auth.py tests/test_credentials.py tests/test_tenant_isolation.py tests/test_docx_import.py tests/test_docx_api.py -q`
Expected: zero failures.

- [ ] **Step 2: Run full backend tests**

Run: `python3 -m pytest -q`
Expected: zero failures.

- [ ] **Step 3: Run frontend and static verification**

Run: `npm run build` in `web`, then `python3 -m compileall server scripts tests && git diff --check`.
Expected: exit code 0.

- [ ] **Step 4: Run real HTTP workflow**

With an isolated database: initialize admin, login, create reviewer, configure a test OneAPI credential via stubbed gateway or approved live key, list models, upload 10 DOCX files, confirm, run review, verify audit model/actor snapshot, verify second user receives 404, logout and verify 401.

- [ ] **Step 5: Independent whole-range review**

Review auth, crypto, IDOR, CSRF, upload parser, secrets, migration and backward compatibility. Fix all Critical/Important findings and re-review.

- [ ] **Step 6: Document and commit**

Document deployment secrets, initial-admin rotation, PostgreSQL/storage requirements, CNAP readiness, backup and rollback.

Commit: `Complete multi-user Word review MVP`
