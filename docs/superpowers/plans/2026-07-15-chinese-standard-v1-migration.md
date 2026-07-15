# 中文审核标准 V1.0 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“百度地图内容审核标准 V1.0”与现有 V0.9 安全实现合并，建立中文唯一标准源、显式六 Agent 配置、逐文件哈希快照和真实稿件评测管线。

**Architecture:** 保留现有不可变 `RuleVersion`、确定性匹配器、证据绑定和仲裁器。V1.0 以相同项目编号、语义版本 `1.0` 发布新快照；旧路径退出生产加载但历史快照不改写。中文 Agent 配置显式绑定六份标准和六份 Prompt，授权标准仅在条件命中时追加给合规/品牌 Agent。

**Tech Stack:** Python 3.9、FastAPI、SQLAlchemy 2、Pydantic 2、PyYAML、JSON Schema、React 18、Vite、TypeScript、pytest。

## Global Constraints

- 默认内容类型保持 `TECH_MEDIA_REVIEW`，不得默认启用艺人身份审核。
- 当前已校准的确定性规则、角色决策矩阵、证据所有权校验和自动修改白名单不得被 V1 示例降级。
- 全局标准只描述判断原则；具体产品功能只能进入项目结构化 claims。
- 六个 Agent 各加载一份且仅一份中文全局标准；`舆情与素材授权.md` 不是第七个 Agent。
- 标准文件缺失、非 UTF-8、JSON/YAML/Schema 无效、配置重复、哈希不一致时必须阻止新审核启动。
- 新审核引用 V1.0 快照；旧审核记录和旧 `RuleVersion` 不改写。
- 所有行为变更先写失败测试。
- 不得把合成稿件标记为真实稿件；真实评测集必须记录来源和人工期望结果。

---

### Task 1: 中文唯一标准包与显式配置

**Files:**
- Modify: `.gitignore`
- Create: `config/审核Agent配置.json`
- Replace: `data/standards/global/*.md`
- Replace: `data/standards/projects/小度想想科技测评/*`
- Replace: `data/standards/rules/*.json`
- Replace: `data/standards/schemas/*.json`
- Create: `prompts/*.md`
- Modify: `server/services/standard_package_service.py`
- Modify: `server/seed.py`
- Test: `tests/test_standard_package.py`

**Interfaces:**
- Produces: `AGENT_STANDARD_CONFIG: dict[str, AgentStandardBinding]`
- Produces: `load_standard_package(root, project_code, package_version="1.0") -> StandardPackage`
- Produces: compiled `file_hashes: dict[str, str]` and `agent_prompt_versions: dict[str, str]`.

- [ ] **Step 1: Add failing migration tests**

Assert that only the seven canonical Chinese Markdown files exist in the production global directory, all six Agent bindings are unique, the conditional authorization file is only assigned to compliance/brand, and version `1.0` loads by Chinese paths.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_standard_package.py -v`
Expected: failures for missing Chinese config/paths and V1.0 identity.

- [ ] **Step 3: Merge the uploaded package into canonical Chinese files**

Use the V1.0 files as the semantic base, retain stricter current V0.9 rules, and remove product-specific claims from global Markdown. Remove English production files after the Chinese loader is green; Git history is the V0.9 archive.

- [ ] **Step 4: Implement explicit config and fail-closed validation**

Validate exact six IDs: `COMPLIANCE`, `BRAND`, `PRODUCT_ACCURACY`, `TEST_CREDIBILITY`, `CONTENT_QUALITY`, `CAMPAIGN_EFFECTIVENESS`. Resolve paths with `Path`, reject traversal, duplicate global files, unknown Agent IDs, missing schemas, invalid UTF-8, malformed JSON/YAML, and unresolved references.

- [ ] **Step 5: Compile per-file hashes and bump default version**

Compute SHA256 from canonical UTF-8 bytes for every loaded config, standard, prompt, project, rule and schema file. Include hashes in the immutable compiled snapshot and package digest. Set default package version to `1.0` without changing the project code.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_standard_package.py tests/test_database.py -v`
Expected: PASS.

Commit: `Migrate to Chinese V1 standard package`

---

### Task 2: Claims、规则与六 Agent Prompt 合并

**Files:**
- Modify: `server/services/review_profile_service.py`
- Modify: `server/services/deterministic_rule_service.py`
- Modify: `scripts/text_review/reviewers/tech_media.py`
- Test: `tests/test_deterministic_rules.py`
- Test: `tests/test_tech_media_agents.py`
- Test: `tests/test_review_workflow.py`

**Interfaces:**
- Consumes: Chinese V1.0 package and explicit Agent bindings from Task 1.
- Produces: one shared prompt plus one configured specialist prompt per Agent.

- [ ] **Step 1: Add failing semantic merge tests**

Cover approved functions `PRE-001` through `WALK-005`, pending functions `PENDING-001` through `PENDING-004`, prohibited safety claims, V0.9 representative regression, conditional authorization activation, and exact one-prompt-per-Agent loading.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_deterministic_rules.py tests/test_tech_media_agents.py tests/test_review_workflow.py -v`
Expected: failures for missing V1 claims/prompts/config activation.

- [ ] **Step 3: Normalize V1 project facts into current typed claims**

Preserve official fact, allowed paraphrases, prohibited expansion, scene and source reference. Pending capabilities always require human review. Prohibited safety claims cannot be converted into safe facts by the model.

- [ ] **Step 4: Merge deterministic rules without weakening calibrated matchers**

Every executable rule receives a stable `rule_id`. Retain guarded claim and hotel capability matchers, negation controls, exact evidence binding and trusted replacement allowlist. Unsupported V1 semantic matcher examples become Agent guidance unless implemented with tested deterministic semantics.

- [ ] **Step 5: Load configured Chinese prompts**

Compose public constraints, configured specialist prompt, exactly one global standard, relevant project slices and conditional authorization content. Persist prompt version/hash and enforce the existing role decision matrix.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_deterministic_rules.py tests/test_tech_media_agents.py tests/test_review_workflow.py tests/test_evidence_workflow.py -v`
Expected: PASS.

Commit: `Merge V1 claims rules and agent prompts`

---

### Task 3: 标准追溯、启动校验与历史兼容

**Files:**
- Modify: `server/models.py`
- Modify: `server/db.py`
- Modify: `server/services/standard_package_service.py`
- Modify: `server/services/review_service.py`
- Modify: `server/main.py`
- Test: `tests/test_database.py`
- Test: `tests/test_api.py`
- Test: `tests/test_standard_package.py`

**Interfaces:**
- Produces: API-visible package version, package digest, per-file hashes, prompt version and model version for each audit.

- [ ] **Step 1: Add failing traceability and startup tests**

Assert fail-closed startup for missing/invalid standards, immutable V0.9 history, V1.0 current binding, per-file hashes in the snapshot, and audit prompt/model/agent version traceability.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_database.py tests/test_api.py tests/test_standard_package.py -v`
Expected: failures for missing V1 traceability fields or responses.

- [ ] **Step 3: Implement minimal schema/API additions**

Reuse JSON snapshot columns when sufficient; add database columns only when queryability requires them. Never mutate an existing `RuleVersion` digest or prior `AuditRun`.

- [ ] **Step 4: Verify and commit**

Run: `python3 -m pytest tests/test_database.py tests/test_api.py tests/test_standard_package.py tests/test_review_workflow.py -v`
Expected: PASS.

Commit: `Add V1 standard traceability and validation`

---

### Task 4: 真实稿件评测管线

**Files:**
- Create: `scripts/text_review/evaluate_corpus.py`
- Create: `tests/fixtures/review_corpus.schema.json`
- Create: `tests/test_review_corpus.py`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes JSONL records with `case_id`, `source_type="REAL"`, title/body, structured evidence references, and human expected per-Agent/aggregate outcomes.
- Produces JSON summary with per-Agent false positives, false negatives, decision mismatches and rule hit deltas.

- [ ] **Step 1: Add failing corpus validation tests**

Reject missing human expectations, duplicate IDs, synthetic records labeled `REAL`, fewer than 30 or more than 50 records in a release evaluation, and records containing secrets or local absolute paths.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest tests/test_review_corpus.py -v`
Expected: missing evaluator/schema failures.

- [ ] **Step 3: Implement corpus validator and evaluator**

Support dry validation without an LLM and full six-Agent evaluation when OneAPI is configured. Store private real稿件 under ignored `data/evaluation/private/`; commit only schema, anonymization rules and non-real unit fixtures.

- [ ] **Step 4: Import available real drafts**

Query the current project database/export. If fewer than 30真实稿件 exist, report the exact count and keep release tuning blocked; do not manufacture records. Each imported record must be anonymized and receive human expected results before model scoring.

- [ ] **Step 5: Verify and commit**

Run: `python3 -m pytest tests/test_review_corpus.py tests/test_tech_media_agents.py -v`
Expected: PASS for validator/evaluator; release corpus readiness is true only with 30–50 valid real records.

Commit: `Add real draft evaluation pipeline`

---

### Task 5: 全量验证与独立审查

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`

- [ ] **Step 1: Run full backend tests**

Run: `python3 -m pytest`
Expected: zero failures.

- [ ] **Step 2: Run frontend build**

Run: `npm run build` in `web`.
Expected: production build succeeds.

- [ ] **Step 3: Run static checks**

Run: `python3 -m compileall server scripts tests && git diff --check`
Expected: exit code 0.

- [ ] **Step 4: Verify package hashes and UTF-8 paths**

Load V1.0 on the current platform, compare compiled file hashes with direct SHA256, and verify no production reference to the retired English filenames remains.

- [ ] **Step 5: Perform independent review**

Review the full V1 migration range. Fix all Critical and Important findings, rerun affected tests and re-review.

- [ ] **Step 6: Commit documentation**

Document Chinese canonical paths, immutable version publication, Agent mappings, corpus readiness rules, rollback and limitations.

Commit: `Complete Chinese V1 standard migration`
