# 内容审核 Agent

供应商内容（视频脚本 / 图文文本 + 图片）从上传到审核、人工复核、报告、规则沉淀的全流程系统。

## 组成

- **审核引擎** `scripts/text_review/`：状态机 + 多专项子 agent（合规/品牌/准确性/质量/舆情授权）+ 可插拔 LLM（OneAPI/文心/离线）
- **后端 API** `server/`：FastAPI，复用审核引擎，提供 HTTP 接口
- **网页** `web/`：React + Vite + TS，四个页面（供应商上传 / 审核台 / 标准管理 / 报告）
- **图片/视频多模态审核** `scripts/`：独立的图片/视频审核流水线（BOS 入库/看板/分发）

## 快速启动

### 1. 后端

```bash
pip install fastapi "uvicorn[standard]" python-multipart requests

# 用 OneAPI（公司内部网关）做真实审核时，先配 key（每人配自己的，不入库）
export ONEAPI_KEY=你的令牌
export ONEAPI_BASE_URL=https://oneapi-comate.baidu-int.com/v1   # 默认已是这个

uvicorn server.main:app --reload --port 8000
```

后端会在 `data/` 下自动生成审核表、标准模板、规则库（已被 .gitignore 忽略）。

### 2. 前端

```bash
cd web
npm install
npm run dev        # 打开 http://localhost:5173
```

Vite 已配置把 `/api`、`/media` 代理到后端 8000 端口。

## 使用流程

1. **标准管理**页：填入真实的全局标准（分维度）、项目标准、禁用词等。标准越具体，审核越准。
2. **审核台**页：选模型后端（OneAPI）+ 填 model 名（如 `gpt-5.5`）+ 项目名 → 点「一键跑审核」。
3. **供应商上传**页：供应商填表 + 传图，进入审核队列。
4. 审核台「待人工审核」标签：管理员对高/中风险内容点 通过 / 需修改 / 删除（不回炉自动审核）。
5. **报告**页：看审核成果 + 问题汇总；一键生成规则沉淀建议，确认后写入规则库。

## 审核状态机

- 流转/等待态：`已提交`、`待人工审核`、`图片/视频修改`、`待供应商补充`、`需修改`
- 终态：`通过`、`已驳回`、`已删除`
- `已提交` 由「格式校验」列消歧去向；人工确认后不回炉自动审核；供应商反复退回超 3 次自动驳回

## 说明

- 审核质量依赖 LLM + 标准：离线模式下语义维度（合规语义/品牌/准确性）会诚实转人工，不假装通过。真实审核请用 `--reviewer oneapi`。
- 模型可自由切换：改 model 名即可，代码不动。每个团队成员用自己的 key（环境变量）。
- 腾讯文档读写：本期未接（网页自带上传/展示）；后续可在 `scripts/text_review/table_adapter.py` 的 `TencentDocAdapter` 里实现。

详见 `SKILL.md`。
