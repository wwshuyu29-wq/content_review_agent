---
name: content-review-agent
description: 内容分发审核 agent。当用户需要对供应商/外部提交的图片、视频内容做上架前审核时使用：供应商内容摆渡入库（外部免费入口 -> 内网 BOS 存储，两侧隔离）、图文/视频自动审核（命中高风险类别强制转人工）、全员可认领的人工审核队列、同步到全员可见的 Ku 看板、供应商公网上传表单页面、审核通过后内容分发标记。触发场景：内容审核、图文审核、视频审核、供应商内容入库、内容分发审核、UGC/PGC 审核队列、建设内容审核流程。
---

# 内容分发审核 Agent

供应商提交的图文/视频内容，从摆渡入库到自动审核、人工复核、看板展示、分发标记的完整审核工作流。

核心原则：供应商与内网存储隔离；高风险内容永远不自动放行；全员可审核、全员可见结果；审核通过后才能进行内容分发。

详细审核标准见 references/review_standards.md，执行本工作流前必须先读一遍，判定时严格套用其中的风险分级和处置规则。

## 文件结构

```
content-review-agent/
├── SKILL.md                        # 本文档
├── references/
│   └── review_standards.md         # 审核标准（风险分级/处置规则，所有脚本的单一可信来源）
├── scripts/
│   ├── agent_loop.py               # Agent 主循环（文心视觉 API，自动拉取→分析→写回）
│   ├── auto_review.py              # 自动审核辅助（list-pending / record-verdict）
│   ├── extract_frames.py           # 视频关键帧抽取（OpenCV）
│   ├── human_queue.py              # 人工审核队列（claim / submit / list）
│   ├── sync_intake.py              # 供应商 manifest 摆渡入库（外部直链 → 内网 BOS）
│   └── sync_dashboard.py           # 同步审核状态到 Ku 数据表（全员可见看板）
└── webapp/
    ├── app.py                      # Flask 服务（审核看板 + 供应商上传入口 + 分发接口）
    └── templates/
        ├── index.html              # 内网审核看板（全员可用）
        └── upload.html             # 供应商上传表单（公网可访问，免费，无需账号）
```

## 快速启动

```bash
# 1. 安装依赖
pip install flask requests opencv-python

# 2. 设置文心 API 凭据（千帆平台 / 文心一言开放平台获取）
export ERNIE_API_KEY=your_api_key
export ERNIE_SECRET_KEY=your_secret_key

# 3. 启动 Web 服务（--upload-dir 指定供应商上传目录，与内网 BOS 隔离）
python3 webapp/app.py --queue /data/queue.jsonl --upload-dir /data/uploads --port 5000

# 4. 启动 Agent 主循环（持续监听队列，自动调用文心分析）
python3 scripts/agent_loop.py --queue /data/queue.jsonl --watch --interval 30
```

- 内网审核看板：`http://<内网IP>:5000/`
- 供应商上传入口：`http://<公网IP或域名>:5000/upload`（公网可访问，供应商无需内网账号）

## 完整工作流

### 第一步：供应商上传内容（两种方式二选一）

**方式 A：网页上传（推荐，无需技术对接）**

供应商打开 `http://<域名>/upload`，填写供应商 ID 后上传文件，支持拖拽，最大 200MB。
上传后直接进入审核队列（`status: queued_for_review`），供应商无需内网账号，两侧存储完全隔离。

**方式 B：清单摆渡（批量入库）**

拿到供应商提交清单（manifest，JSON 数组，字段见 scripts/sync_intake.py 头部说明）后执行：

```bash
python3 scripts/sync_intake.py --manifest manifest.json --queue queue.jsonl
```

脚本会下载文件、校验格式、调用 `dodo_cli bos upload` 写入内网私有存储，写入 `queue.jsonl`。
下载或上传失败的记录会标记 `status: intake_failed` 并打印失败原因，不会中断整批处理。

### 第二步：自动审核（Agent 主循环）

**推荐：启动 Agent 持续监听，无需手动干预**

```bash
python3 scripts/agent_loop.py --queue queue.jsonl --watch --interval 30
```

Agent 会：
1. 调用 `auto_review.py list-pending` 拉取 `queued_for_review` 状态的记录
2. 图片直接 base64 传入文心视觉 API；视频先调用 `extract_frames.py` 抽关键帧再逐帧分析
3. 合并所有帧的风险判断（高风险覆盖低风险），调用 `auto_review.py record-verdict` 写回
4. 脚本自动套用 references/review_standards.md 中的规则算出最终状态：
   - `auto_passed`：自动通过
   - `auto_rejected`：自动拒绝（中风险且置信度 ≥ 0.85）
   - `needs_human`：转人工审核（高风险、unknown、中风险低置信度）

**可选：手动触发一次性处理**

```bash
python3 scripts/agent_loop.py --queue queue.jsonl
```

### 第三步：人工审核队列（全员可认领）

**方式 A：网页操作（推荐）**

打开审核看板 `http://<内网IP>:5000/`，点击"待人工审核"标签页，认领并提交结论。

**方式 B：CLI 操作**

```bash
# 查看待人工审核列表
python3 scripts/human_queue.py list --queue queue.jsonl

# 认领（reviewer 用实际的用户标识）
python3 scripts/human_queue.py claim --queue queue.jsonl --content-id <id> --reviewer <reviewer>

# 提交结论（命中高风险类别时 reason 会被校验，不能是敷衍的短语）
python3 scripts/human_queue.py submit --queue queue.jsonl --content-id <id> \
  --reviewer <reviewer> --decision approved|rejected --reason "<判断依据>"
```

全员都可以执行 `claim`，脚本保证同一时间只有一人持有认领权，避免重复劳动。

### 第四步：同步到全员可见看板

```bash
python3 scripts/sync_dashboard.py --queue queue.jsonl --dist-id <Ku数据表ID>
```

- 首次运行如果没有 `--dist-id`，脚本会提示先用 `ku-doc-manage` 创建一个数据表
- 已通过（自动通过/人工通过）的记录会展示缩略图；仍在 `needs_human` 或高风险状态的记录只展示元信息和风险标签，不展示原图
- 每次状态变化后重新运行本脚本即可增量同步（按 content_id 判断新增还是更新）

### 第五步：内容分发（审核通过后）

审核通过的内容可通过 API 触发分发流程：

```bash
curl -X POST http://localhost:5000/api/distribute \
  -H "Content-Type: application/json" \
  -d '{"content_id": "<id>", "operator": "your_user_id", "channel": "cms"}'
```

- 只有 `auto_passed` 或 `human_approved` 状态的内容可以分发，未通过审核返回 409
- 本接口只做状态标记，实际推送到 CDN / 内容中台 / 推荐系统需用户在下游系统接入
- `channel` 字段可填写分发渠道，如 `cms`、`feed`、`cdn`

## 状态机

```
intake_failed [入库失败]
    OR
queued_for_review → auto_passed / auto_rejected / needs_human
                               ↓
                     needs_human (claimed_by 置空)
                               ↓ claim
                     needs_human (claimed_by=reviewer)
                               ↓ submit
                     human_approved / human_rejected
                               ↓（仅已通过状态）
                     distributed=true（已分发标记）
```

## 文案审核工作流（视频脚本 / 图文文本）—— scripts/text_review/

面向"腾讯文档表格为载体、状态列驱动"的文本审核流水线（供应商上传→格式校验→批量审核→自动改写/人工复核→报告→规则沉淀）。与上面的图片/视频多模态审核并行，复用相同的"标准配置/人工复核/规则沉淀"理念。

### 模块结构

```
scripts/text_review/
├── schema.py          # 表字段、状态、风险等级、审核维度常量（单一可信来源）
├── standards.py       # 标准仓库（分维度全局标准 + 项目标准）+ 规则库；Ku 适配层预留
├── table_adapter.py   # 表格 I/O；本地 CSV 默认，腾讯文档/dodo 适配层预留
├── reviewer.py        # 对外接口（Verdict / get_reviewer）
├── reviewers/         # 多专项子 agent 审核层
│   ├── llm.py         #   LLM 客户端（ernie / 离线）
│   ├── base.py        #   维度子 agent 基类 + 结果结构
│   ├── compliance.py  #   合规/广告法（确定性禁用词 + LLM 红线）
│   ├── brand.py       #   品牌一致性（LLM）
│   ├── accuracy.py    #   内容准确性（确定性必带标签 + LLM）
│   ├── quality.py     #   内容质量（确定性 + LLM）
│   ├── external.py    #   舆情与授权（命中关键词转人工，需外部数据）
│   └── orchestrator.py#   汇总各子 agent -> 总 Verdict
├── engine.py          # 状态机引擎（流程三/四/五 + 消歧 + 重试上限）
├── report.py          # 流程六 报告 + 流程七 规则沉淀
└── run_review.py      # 命令行入口（手动批量触发）
```

全局标准模板见 references/text_review_standards.md（概览）；分维度标准用 `init-standards` 生成后填写。

### 审核大脑：多专项子 agent

审核拆成 5 个专项子 agent，各吃对应标准切片，orchestrator 汇总为总判定：

- **compliance 合规/广告法**：禁用词精确匹配（确定性，无需 LLM）+ 广告法红线语义判断（LLM）
- **brand 品牌一致性**：品牌名/功能名/调性/口径（LLM）
- **accuracy 内容准确性**：必带标签齐全性（确定性）+ 功能/规则/优惠准确性（LLM）
- **quality 内容质量**：错别字/语病/格式（确定性 + LLM），属低风险可自动改写
- **external 舆情与授权**：明星/IP/第三方/近2周舆情命中即转人工（需外部数据）

设计原则：
- **确定性 + LLM 混合**：能用代码精确判的（禁用词、必带标签、字段/格式）用代码，又快又稳；语义判断交给 LLM。
- **诚实弃权**：无 LLM 时，语义维度（合规语义/品牌/准确性）不会假装通过，而是标注"未审(未启用 LLM)"并转人工。因此**真正的审核必须 `--reviewer ernie`**（或接入 dodo），离线模式仅用于跑通流程与确定性检查。
- **审核质量取决于标准**：`init-standards` 生成分维度标准模板，需管理员填入真实的品牌口径/合规红线/项目卖点/授权清单等；标准写得越具体，判得越准。

### 状态机（内容状态）

- 流转/等待态：`已提交`、`待人工审核`、`图片/视频修改`、`待供应商补充`、`需修改`
- 终态：`通过`、`已驳回`、`已删除`
- 消歧：`已提交` 行由「格式校验」列决定去向（≠通过→内容读取；=通过→批量审核）
- 人工审核后管理员直接置 `通过`/`需修改`/`已删除`，**不回炉自动审核**（避免死循环）
- 供应商退回累计超过 3 次（MAX_ROUNDS）自动 `已驳回`
- 风险处置：`low` 自动改写；`mid`/`high`/`unknown` 只给建议转人工；`none` 直接通过并回填最终列

### 命令行用法

```bash
# 0. 首次：生成分维度标准模板，填入真实要求
python3 -m scripts.text_review.run_review init-standards --standards-dir data/standards

# 1. 跑一批（真实审核用文心；需 ERNIE_API_KEY / ERNIE_SECRET_KEY）
python3 -m scripts.text_review.run_review run-batch \
    --table data/review.csv --project 五一KOL --standards-dir data/standards --reviewer ernie

# 离线跑通流程（语义维度会转人工，仅用于验证管道/确定性检查）
python3 -m scripts.text_review.run_review run-batch --table data/review.csv --reviewer heuristic

# 列出待人工审核内容（管理员在表里处理后重新跑 run-batch）
python3 -m scripts.text_review.run_review list-human --table data/review.csv

# 输出审核报告（成果 + 问题汇总）
python3 -m scripts.text_review.run_review report --table data/review.csv --out report.md

# 规则沉淀：先看建议，管理员确认后 --confirm 写入规则库
python3 -m scripts.text_review.run_review distill-rules --table data/review.csv
python3 -m scripts.text_review.run_review distill-rules --table data/review.csv --confirm
```

后端切换：`--backend csv|tencent`、`--reviewer heuristic|ernie`、`--standards-backend local|ku`。

### 待接入项（预留适配层，接入后其余流程不变）

- **腾讯文档读写**：`TencentDocAdapter` 是桩。读取用 dodo-happywork-v1-internal 在线文档能力；回写状态列需腾讯文档开放平台写接口。当前用 `--backend csv` 跑通全流程。
- **Ku 标准仓库**：`KuStandardsRepo` 是桩，接入时用 ku-doc-manage 实现 load()/add_rules()。当前用 `--standards-backend local`。
- **图片多模态审核**：文本审核已完整；图片可复用 agent_loop.py 的 ERNIE-VL 路径，在 reviewer 里置 `media_issue` 触发"图片/视频修改"分支。
- **近2周风险舆情**：管理员先手动维护 rules.json 的必须人工确认关键词，微博抓取后接。
- **如流群通知**：本期不做，退回/转人工的行由 list-human/report 汇总，人工处理。

## 关键限制（必须告知用户，不要隐藏）

- 自动审核依赖文心视觉 API（需要 ERNIE_API_KEY + ERNIE_SECRET_KEY），不是内置本地模型；如公司有内部内容安全 API，可替换 agent_loop.py 的分析调用，其余流程不变
- 视频审核基于关键帧抽样，无法覆盖音频、字幕、快速闪现画面，这是结构性限制
- 供应商上传入口（/upload）没有做用户身份认证，任何能访问该 URL 的人都可以上传；如需限制，用户需在部署时加反向代理白名单或 Token 校验
- webapp/app.py 内置的是 Flask 开发服务器，内网长期对外提供服务需用 gunicorn/uwsgi 承载
- 内网审核看板的身份识别只是手动输入工号，没有真实鉴权，需要用户自行接入公司 SSO
- dodo_cli（BOS 存储）与 ku-doc-manage skill（看板同步）需用户自行配置，本 skill 不内置


# 内容分发审核 Agent

供应商提交的图文/视频内容，从摆渡入库到自动审核、人工复核、看板展示的完整审核工作流。核心原则：供应商与内网存储隔离；高风险内容永远不自动放行；全员可审核、全员可见结果。

详细审核标准见 references/review_standards.md，执行本工作流前必须先读一遍，判定时严格套用其中的风险分级和处置规则。

## 前置准备

首次使用前确保脚本可执行：
```bash
chmod +x scripts/*.py
```

依赖 `dodo_cli`（BOS 存储）与 `ku-doc-manage` skill（看板）。图片/关键帧使用 opencv-python 处理，无需系统 ffmpeg。

## 完整工作流

### 第一步：供应商摆渡入库

拿到供应商提交清单（manifest，JSON 数组，字段见 scripts/sync_intake.py 头部说明）后执行：

```bash
python3 scripts/sync_intake.py --manifest <manifest.json> --queue <queue.jsonl>
```

- 若用户还没有真实的供应商上传入口，先建议一种免费方案（如腾讯文档收集表/问卷星表单收附件，或供应商自备对象存储直链），本脚本只消费"清单+直链"，不关心供应商具体用什么平台
- 脚本会下载文件、校验格式、调用 `dodo_cli bos upload` 写入内网私有存储，写入 `queue.jsonl`
- 下载或上传失败的记录会标记 `status: intake_failed` 并打印失败原因，不会中断整批处理

### 第二步：自动审核（图文 + 视频）

```bash
python3 scripts/auto_review.py list-pending --queue <queue.jsonl> --frames-dir <frames_dir>
```

- 输出每条待审记录可分析的路径：图片是本地文件路径，视频会自动抽关键帧并返回帧路径列表
- **拿到路径后，你（agent）必须用 Read 工具逐张真实读图分析**，禁止编造判断结果。分析时严格对照 references/review_standards.md 的风险类别定义，判断命中哪些类别、风险等级、置信度
- 视频记录会附带 `video_note` 提示"关键帧抽样审核，无法覆盖音频/字幕"，分析结论中要带上这条限制说明
- 每条记录分析完成后，写回决策结果：

```bash
python3 scripts/auto_review.py record-verdict --queue <queue.jsonl> --content-id <id> \
  --risk-categories <逗号分隔类别，无风险留空> --confidence <0-1> --reason "<判断依据>"
```

- 该脚本会自动套用 references/review_standards.md 中的规则算出最终 `pass`/`reject`/`needs_human`，你不需要也不能自行覆盖这个决策，只需要如实提供 risk_categories 和 confidence

### 第三步：人工审核队列（全员可认领）

```bash
# 查看待人工审核列表
python3 scripts/human_queue.py list --queue <queue.jsonl>

# 认领（reviewer 用实际的用户标识）
python3 scripts/human_queue.py claim --queue <queue.jsonl> --content-id <id> --reviewer <reviewer>

# 提交结论（命中高风险类别时 reason 会被校验，不能是敷衍的短语）
python3 scripts/human_queue.py submit --queue <queue.jsonl> --content-id <id> \
  --reviewer <reviewer> --decision approved|rejected --reason "<判断依据>"
```

- 全员都可以执行 `claim`，脚本保证同一时间只有一人持有认领权，避免重复劳动
- 高风险类别（illegal / porn_vulgar / violence_terror）没有强制二审机制，当前设计是单人审核+留痕；如需二审，需在此脚本基础上扩展，不在当前范围内

### 第四步：同步到全员可见看板

```bash
python3 scripts/sync_dashboard.py --queue <queue.jsonl> --dist-id <Ku数据表ID>
```

- 首次运行如果没有 `--dist-id`，脚本会提示先用 `ku-doc-manage` 创建一个数据表，创建后把返回的 dist-id 传入
- 已通过（自动通过/人工通过）的记录会展示缩略图；仍在 `needs_human` 或高风险状态的记录只展示元信息和风险标签，不展示原图，避免看板本身变成违规内容的二次扩散渠道——这是默认的安全策略，如需调整需明确告知用户风险后再改
- 每次状态变化后重新运行本脚本即可增量同步（按 content_id 判断新增还是更新）

## 可选：可交互审核网页（webapp/）

第三步的人工审核（认领/提交结论）也可以通过一个真实可交互的网页完成，而不必逐条敲 CLI 命令，方便内网同学共享使用。网页直接复用 `scripts/auto_review.py` 的决策逻辑与 `queue.jsonl` 数据文件，CLI 和网页看到的是同一份状态，互不冲突，可以混用。

```bash
pip install flask
python3 webapp/app.py --queue <queue.jsonl> --host 0.0.0.0 --port 5000
```

- 打开 `http://<部署机器IP>:<port>/` 即可看到全部内容列表和"待人工审核"标签页
- 页面顶部手动输入工号作为审核人身份（存本地 localStorage），未接入真实 SSO，先跑通再按需升级
- 点击卡片可查看详情、认领、填写理由后提交通过/拒绝；命中高风险类别时理由过短会被拒绝提交，规则与 CLI 一致
- 图片安全策略：仅 `auto_passed`/`human_approved` 状态的内容允许通过 `/media/<content_id>` 查看原图，其余状态服务端强制返回 403，与看板策略一致，避免未审核/高风险内容曝光
- **部署与安全提醒**：`webapp/app.py` 自带的是 Flask 开发服务器，仅用于本地预览验证；如需在内网长期对外提供服务，需要用户自行部署到内网服务器/BRCC 等发布渠道，并用 gunicorn/uwsgi 等生产级 WSGI 服务器承载。当前身份识别只是手动输入工号，没有任何真实鉴权，`--host 0.0.0.0` 会让内网所有能访问到该端口的人都可以操作认领/提交，如果需要真正的访问控制，需要在部署时自行接入公司 SSO 或加一层反向代理鉴权

## 关键限制（必须告知用户，不要隐藏）

- 供应商侧免费上传入口本身需要用户在对应平台实际开通，本 skill 不能代为创建外部账号/存储桶
- 自动审核依赖 agent 自身的多模态理解能力，不是接入了某个专门的内容安全 API；如果公司内部有现成的内容安全网关，应替换 auto_review.py 的判断输入来源，其余流程不变
- 视频审核基于关键帧抽样，无法覆盖音频、字幕、快速闪现画面，这是结构性限制
- 可交互审核网页（webapp/）当前没有真实身份鉴权，仅做了手动工号输入，部署到内网前必须让用户知晓这一限制
