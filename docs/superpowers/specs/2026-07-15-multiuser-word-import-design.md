# 多用户审核与 Word 批量导入设计

## 目标

把当前单机、全局 OneAPI 配置的审核工具升级为内部团队公网 MVP：管理员创建账号，用户登录后配置自己的 OneAPI Key 和默认模型；所有业务数据按用户隔离；每次审核固化实际模型与操作者；供应商可一次上传约 10 篇 `.docx` 稿件并一键导入。

## 安全边界

- 不开放公众注册。首个管理员由部署环境初始化，后续账号只能由管理员创建、停用和重置密码。
- 密码使用 Argon2id 哈希。会话使用高熵随机 token，数据库只保存 token SHA-256；Cookie 为 HttpOnly、SameSite=Lax，生产环境 Secure。
- 状态修改请求必须携带会话绑定的 CSRF token；后端同时校验 Origin/Host。
- OneAPI 基址只能由服务器环境配置。用户只能提交 Key 和选择该 Key 可见的模型，不能提供任意 URL。
- OneAPI Key 使用 `CREDENTIAL_ENCRYPTION_KEY` 进行 AES-GCM/Fernet 认证加密。API 只返回是否配置、尾号和验证时间，不返回密文或明文。
- 日志、异常、模型请求体、数据库导出和前端状态不得包含 Key。
- 用户只能访问自己的项目和所有下游数据。管理员管理账号，但默认不跨用户浏览业务内容。

## 数据模型

新增：

- `User`：用户名、显示名、Argon2id 密码哈希、`ADMIN/REVIEWER`、启用状态、会话版本。
- `UserSession`：用户、token 哈希、CSRF 哈希、过期时间、撤销时间、最后使用时间。
- `OneAPICredential`：用户唯一、Key 密文、Key 尾号、默认模型、验证时间。

所有权使用 `owner_user_id`：

- 直接存储于 `Project`、`Batch`、`ContentItem`、`Asset`、`TestCase`、`AuditRun`、`ReviewTask`、`HumanDecision`。
- `ContentVersion`、`AgentResult`、`Issue`、`TestEvidence` 可由父对象推导，但服务层同时验证父链所有权，禁止跨用户关系。
- `Project.name/code` 唯一性改为用户内唯一。每个新用户创建自己的默认科技测评项目和不可变标准快照。
- 旧数据在升级时归属首个管理员。迁移校验发现父子所有权不一致时启动失败。

`AuditRun` 新增 `actor_user_id`、`credential_fingerprint`，并继续保存实际 `model`、`prompt_version`、`rule_version_id`。不保存 Key。

## 认证 API

- `POST /api/auth/login`：用户名密码登录，设置 Session Cookie，返回用户与 CSRF token。
- `POST /api/auth/logout`：撤销当前会话。
- `GET /api/auth/me`：当前用户、角色和 OneAPI 配置摘要。
- `GET/POST/PATCH /api/admin/users`：管理员创建、停用、重置账号。
- 除登录和健康检查外，业务 API 全部要求会话。

认证失败返回 401；对象不属于当前用户时统一返回 404，避免泄露 ID 是否存在；角色不足返回 403。

## 用户 OneAPI

- `PUT /api/account/oneapi`：验证 Key，成功后加密保存并设置默认模型。
- `DELETE /api/account/oneapi`：删除用户凭证。
- `GET /api/account/oneapi/models`：后端使用当前用户解密后的 Key 调用受信任网关 `/models`，返回模型 ID；不缓存 Key。
- `PATCH /api/account/oneapi/model`：默认模型必须来自最近一次模型列表或由实时验证确认。

审核开始前要求当前用户已配置 Key 和模型。服务创建绑定当前凭证的 `OpenAICompatLLM(api_key, model, base_url)`，不得修改进程全局环境变量。六个 Agent 使用同一个审核运行模型。网关或模型不支持严格 JSON Schema 时失败关闭并创建人工任务，不自动通过。

## 数据隔离

所有查询入口以当前用户为第一过滤条件：项目、规则发布、批次、内容详情、内容表、媒体、测试场景、证据、审核、任务、报告、Excel 导出、预览确认。

服务方法接收 `owner_user_id` 或已验证的拥有者对象。禁止在路由层先 `session.get(id)` 再补判断。文件路径通过数据库对象所有权后才解析和返回。

Excel/Word 预览 manifest 写入 `owner_user_id`，确认时必须与会话用户一致。幂等 token 查询同时带所有者条件。

## Word 批量导入

上传入口支持 1–20 个 `.docx`，产品目标为一次约 10 篇。拒绝 `.doc`、`.docm`、加密、损坏、含外部关系或超限文件。

限制：单文件 10 MiB、总上传 100 MiB、单文档解压后 50 MiB、OOXML 条目最多 2,000。ZIP 读取防目录穿越、绝对路径、符号链接和压缩炸弹。

每个文档生成一条内容：

- `external_id`：规范化文件名 stem；重复时预检报错，不自动覆盖。
- 标题：优先 Word `Title/标题` 样式；否则使用首个非空段落；仍为空则报错。
- 正文：标题之外的非空段落，按文档顺序连接；表格以行/单元格文本插入。正文为空报错。
- payload：原始文件名、导入方式 `DOCX_BATCH`、共同平台/账号信息、预览身份。

流程：选择项目、供应商、批次和共同元数据 → 多选 Word → 预览每篇标题/字数/错误 → 全部有效时一键确认 → 一个批次内创建多条 V1。确认幂等且事务化。

## 前端

新增登录页和认证上下文。未登录只能访问登录页；401 自动清理会话并跳转。

顶部显示当前用户和退出按钮。新增“账号设置”：配置/替换/删除 OneAPI Key，动态刷新模型列表，选择默认模型。管理员额外看到账号管理。

上传页使用 `Excel 导入 / Word 批量导入` 标签。Word 页支持多文件选择、文件列表、预检表和一键确认。审核页移除全局 reviewer/model 配置，展示当前用户模型；未配置凭证时引导到账号设置。

## 测试与验收

- 密码不明文、Session/CSRF/Cookie 属性、登出/停用/过期会话。
- Key 加密往返、错误主密钥失败、API/日志不泄露、动态模型、模型归属。
- 两用户对所有业务 API 和文件 URL 的 IDOR 测试。
- 旧数据归属初始管理员，新用户获得独立默认项目。
- 审核记录固化用户、模型、凭证指纹、规则和 Prompt。
- DOCX 标题/正文/表格提取，10 文件成功，重复/损坏/宏/外链/压缩炸弹/超限失败，跨用户 token 失败。
- 全量 pytest、React 构建、真实登录→配置模型→Word 导入→审核主链路。

## 部署边界

本地完成后再安装 CNAP CLI。生产使用 PostgreSQL、持久化文件存储、平台 Secret 注入 `CREDENTIAL_ENCRYPTION_KEY` 和首个管理员凭证、HTTPS 入口。CNAP 账号、环境、集群、公网入口和域名依赖用户的百度内部权限。
