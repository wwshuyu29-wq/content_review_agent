# 内容审核 Agent

基于 FastAPI、SQLAlchemy 和 React 的内容审核工作流。系统管理项目与不可变规则版本、供应商上传批次、内容版本、多 Agent 审核、人工任务和统计报告。

## 本地环境

需要 Python 3.9+、Node.js 18+ 和 npm。以下命令从仓库根目录执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd web && npm ci && cd ..
./scripts/start_local.sh
```

启动脚本同时运行：

- FastAPI：`http://127.0.0.1:8000`，OpenAPI 文档位于 `/docs`
- React/Vite：`http://127.0.0.1:5173`
- Vite 将 `/api` 和 `/media` 转发到 FastAPI

按 `Ctrl-C` 会停止两个进程。脚本不会安装依赖，也不会读取或写入密钥。可用 `BACKEND_HOST`、`BACKEND_PORT`、`FRONTEND_HOST`、`FRONTEND_PORT` 和 `PYTHON_BIN` 覆盖默认值；脚本会自动设置匹配后端端口的 `VITE_API_TARGET`。

也可以分别启动：

```bash
python -m uvicorn server.main:app --reload --host 127.0.0.1 --port 8000
cd web
VITE_API_TARGET=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

## 配置

### 数据库

`DATABASE_URL` 未设置时默认使用仓库内的 SQLite 数据库：

```text
sqlite:////绝对路径/content-review-agent/data/review.db
```

本地可显式指定其他 SQLite 文件：

```bash
export DATABASE_URL="sqlite:////tmp/content-review.db"
```

生产 PostgreSQL 示例（驱动已包含在 `requirements.txt`）：

```bash
export DATABASE_URL="postgresql+psycopg://review_user:password@db.example.com:5432/content_review"
```

应用启动时通过 SQLAlchemy 创建缺失表并幂等写入默认项目。当前没有 Alembic 迁移；已有生产数据库发生模型变更时，需要在部署前补充受控迁移。

### OneAPI

默认审核后端是 `offline`，不会发起网络请求。离线审核仍执行确定性规则；需要语义判断的内容会转人工，不会伪装成自动通过。

真实 OneAPI 审核需要先在审核台将审核后端保存为 `oneapi`，并只从进程环境读取密钥：

```bash
export ONEAPI_KEY="从密钥管理系统注入的令牌"
export ONEAPI_MODEL="实际可用的模型名"
export ONEAPI_BASE_URL="https://oneapi-comate.baidu-int.com/v1"
```

`ONEAPI_BASE_URL` 默认是 `https://oneapi-comate.baidu-int.com/v1`。`ONEAPI_MODEL` 和 base URL 也可在审核台保存为非敏感运行配置；审核台的非空保存值优先于对应环境变量。`ONEAPI_KEY` 不在 API、网页配置或数据库中接收、返回或持久化。不要将密钥写入仓库文件或命令历史。

## 使用流程

1. 在“标准管理”选择默认项目或创建项目，发布规则版本。
2. 在“供应商上传”创建 multipart 批次并上传文案与可选图片。
3. 在“审核台”按内容或批次触发审核。
4. 处理风险人工任务，或接受、编辑接受、拒绝 AI 建议稿。
5. 在“报告”查看状态、问题类别、规则命中和人工占比。

默认种子项目为“百度地图小度想想 × 范丞丞短期合作”。上传文件和 SQLite 数据保存在 `data/`，该目录已被 Git 忽略。

## 验证

```bash
python -m pytest -q
python -m compileall -q server scripts tests webapp
cd web && npm run build
```

## 生产构建与运行

构建前端静态资源：

```bash
cd web
npm ci
npm run build
```

产物位于 `web/dist/`。生产环境应使用 Nginx、CDN 或其他静态服务器提供该目录，并将 `/api` 和 `/media` 反向代理到 FastAPI。Vite 开发服务器和 `start_local.sh` 仅用于本地开发。

后端示例：

```bash
export DATABASE_URL="postgresql+psycopg://review_user:password@db.example.com:5432/content_review"
export ONEAPI_KEY="由部署平台注入"
export ONEAPI_MODEL="实际可用的模型名"
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 2
```

生产环境还应在反向代理层配置 TLS、认证、上传大小限制和访问日志。当前版本不包含登录/RBAC、异步任务队列、对象存储或数据库迁移框架。

旧版 CSV/多模态脚本仍位于 `scripts/` 和 `webapp/`；数据库化 Web 主链路使用 `server/` 与 `web/`。
