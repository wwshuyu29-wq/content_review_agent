# 2026-07-17 本地登录与数据误判教训

## 事故

本地恢复原库后端时，启动命令没有带 `SESSION_SECRET`。这导致 `/api/auth/me` 在鉴权阶段抛出 `SESSION_SECRET is required`，返回 500，前端登录状态异常。

随后又临时启动了一份干净 SQLite 预览库，导致页面看起来像“数据变新了、已审核稿件丢了、密码也变了”。这个判断路径误导了用户。

## 影响

- 用户无法用预期密码登录。
- 用户看到的不是原来的已审核数据，而是临时库数据。
- 排查过程中把“启动环境错误”和“账号密码状态”混在一起，增加了用户的不信任和额外沟通成本。

## 根因

- 启动受鉴权保护的 FastAPI 服务前，没有先确认必要环境变量，尤其是 `SESSION_SECRET`。
- 临时库和原库同时存在时，没有在用户可见结论里明确区分当前后端连接的是哪一个数据库。
- 没有先用 API 登录和内容表数量验证当前服务，再告诉用户可以登录和查看数据。

## 必须记住

以后只要启动这个项目的后端，必须先确认：

```bash
echo "${SESSION_SECRET:?SESSION_SECRET is required}"
```

如果使用已有用户库，不能随便带 `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` 启动，因为这会刷新已有管理员密码。

启动后必须验证：

```bash
curl -fsS http://127.0.0.1:8000/docs
sqlite3 data/review.db "select count(*) from content_items; select count(*) from audit_runs;"
```

需要确认登录链路时，必须用真实后端完成一次 `/api/auth/login`，再带 cookie 请求 `/api/contents/table`，不能只看前端页面是否打开。

## 下次正确做法

1. 先停掉临时后端，确认 8000 端口只剩一个目标服务。
2. 明确声明当前使用的 `DATABASE_URL` 或默认 `data/review.db`。
3. 带固定 `SESSION_SECRET` 启动后端。
4. 不带 `INITIAL_ADMIN_PASSWORD` 启动已有用户库，避免刷新密码。
5. 用 API 验证登录、内容数量和审核记录数量。
6. 再把可登录账号、数据条数和访问地址告诉用户。
