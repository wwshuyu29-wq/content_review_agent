# 科技媒体测评审核 V0.9 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有通用/艺人项目审核原型升级为以 `TECH_MEDIA_REVIEW` 为默认 Profile、支持测试场景和证据、六 Agent 统一输出、确定性规则和人工闭环的科技媒体测评审核系统。

**Architecture:** 结构化标准文件经 Schema 校验后编译为不可变 `RuleVersion` 快照；代码硬校验和确定性规则先执行，六 Agent 只消费与内容类型匹配的标准切片，后端仲裁器统一生成问题、任务和状态。现有安全 Excel 预览/确认/导出、FastAPI、SQLAlchemy 和 React + Vite 继续复用。

**Tech Stack:** Python 3.9、FastAPI、SQLAlchemy 2、Pydantic 2、openpyxl、PyYAML、JSON Schema、React 18、Vite、TypeScript。

## Global Constraints

- 默认内容类型必须是 `TECH_MEDIA_REVIEW`，不得默认加载艺人、代言或授权 Profile。
- 运行时标准必须匹配 `business_domain + project_code + content_type + version`。
- Agent 不得修改数据库状态、创造产品功能、测试结果或证据。
- 未确认功能、关键证据、数字、竞品结论和重大事实必须进入人工审核。
- 传播策略低分不能单独阻断事实合规内容。
- 所有 AI 修改必须创建新 `ContentVersion`，不得覆盖供应商原稿。
- V0.9 不允许 Agent 自动发布内容。
- API Key 只从后端环境变量读取。
- 所有行为变更先写失败测试，再写实现。

---

### Task 1: 标准包、Schema 与默认项目

**Files:**
- Modify: `.gitignore`
- Create: `data/standards/global/*.md`
- Create: `data/standards/projects/xiaoduxiangxiang_tech_review/*.{yaml,md}`
- Create: `data/standards/rules/*.json`
- Create: `data/standards/schemas/*.json`
- Create: `server/services/standard_package_service.py`
- Modify: `server/seed.py`
- Modify: `server/models.py`
- Modify: `server/schemas.py`
- Modify: `requirements.txt`
- Test: `tests/test_standard_package.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: `load_standard_package(root: Path, project_code: str) -> StandardPackage`
- Produces: `compile_standard_package(package: StandardPackage) -> dict[str, Any]`
- Produces: `publish_standard_package(session: Session, project_id: int, package: StandardPackage) -> RuleVersion`

- [ ] **Step 1: Write failing tests for package validation and isolation**

```python
def test_loads_only_matching_tech_media_package(standards_root):
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")
    assert package.metadata.content_type == "TECH_MEDIA_REVIEW"
    assert "PENDING-002" in {claim.claim_id for claim in package.pending_claims}


def test_rejects_cross_domain_or_unresolved_rule_reference(standards_root):
    mutate_package(standards_root, business_domain="other")
    with pytest.raises(ValueError, match="business_domain"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/test_standard_package.py -v`
Expected: import/file failures because the package service and standard files do not exist.

- [ ] **Step 3: Add typed Pydantic package models and validators**

```python
class StandardMetadata(BaseModel):
    business_domain: Literal["baidu_maps_marketing_review"]
    document_type: Literal["project_standard"]
    project_code: str
    content_type: Literal["TECH_MEDIA_REVIEW"]
    version: str

class StandardPackage(BaseModel):
    metadata: StandardMetadata
    project: ProjectStandard
    approved_claims: list[ApprovedClaim]
    pending_claims: list[PendingClaim]
    evidence_requirements: EvidenceRequirements
    platform_requirements: dict[str, PlatformRequirement]
    deterministic_rules: list[DeterministicRule]
    global_standards: dict[str, str]
```

- [ ] **Step 4: Add the V0.9 files from the approved design**

Change `.gitignore` from a blanket `data/` rule to:

```gitignore
data/*
!data/standards/
!data/standards/**
```

Include six global standards, project facts, approved/pending Claims, evidence triggers, empty `PENDING` platform requirements, deterministic rules, term dictionary, replacement rules and JSON Schemas. No celebrity terms belong to this package. Runtime databases, uploads, preview files and secrets remain ignored.

- [ ] **Step 5: Compile and publish immutable snapshots**

Store compiled metadata and content under `RuleVersion.dimension_standards`, `project_facts` and `structured_rules`. Add `Project.code` and `Project.content_type`, with a schema upgrade for existing databases.

- [ ] **Step 6: Replace the default seed**

Seed `bdmap_xdxx_tech_review_2026` as the default active project. Preserve the old artist project only when it already exists; do not attach its rules to the tech project.

- [ ] **Step 7: Verify and commit**

Run: `python3 -m pytest tests/test_standard_package.py tests/test_database.py -v`
Expected: PASS.

Commit: `Implement tech review standard package`

---

### Task 2: 确定性规则引擎与内容 Profile

**Files:**
- Create: `server/services/deterministic_rule_service.py`
- Create: `server/services/review_profile_service.py`
- Modify: `scripts/text_review/standards.py`
- Modify: `server/services/review_service.py`
- Test: `tests/test_deterministic_rules.py`

**Interfaces:**
- Produces: `evaluate_rules(profile: ReviewProfile, context: ReviewContext) -> list[StructuredIssue]`
- Produces: `get_review_profile(rule_version: RuleVersion) -> ReviewProfile`

- [ ] **Step 1: Write failing tests for scoped matching**

```python
def test_tech_profile_does_not_load_celebrity_rules(profile):
    assert profile.content_type == "TECH_MEDIA_REVIEW"
    assert all("范丞丞" not in json.dumps(rule.model_dump(), ensure_ascii=False) for rule in profile.rules)


def test_evidence_and_count_rules_are_deterministic(profile):
    context = ReviewContext(title="亲测：5个测试", body="1. 场景一\n2. 场景二", test_cases=[])
    ids = {issue.rule_id for issue in evaluate_rules(profile, context)}
    assert {"TEST-COUNT-001", "TEST-EVIDENCE-001"} <= ids
```

- [ ] **Step 2: Confirm RED**

Run: `python3 -m pytest tests/test_deterministic_rules.py -v`
Expected: missing service imports.

- [ ] **Step 3: Implement explicit Matcher dispatch**

```python
_MATCHERS = {
    "exact_phrase": match_exact_phrase,
    "phrase_list": match_phrase_list,
    "replacement_map": match_replacement_map,
    "count_consistency": match_count_consistency,
    "evidence_required": match_evidence_required,
    "required_term": match_required_term,
}
```

Reject unknown Matcher types. Apply project/content/platform/field scope before matching. Preserve `source_reference` and replacement guidance.

- [ ] **Step 4: Remove raw four-array consumption from the database path**

The active FastAPI workflow must not turn `deny_words`, `must_human_keywords` or `required_tags` into substring checks. Keep legacy adapters only for old CLI compatibility.

- [ ] **Step 5: Verify and commit**

Run: `python3 -m pytest tests/test_deterministic_rules.py tests/test_review_workflow.py -v`
Expected: PASS with updated workflow expectations.

Commit: `Add scoped deterministic review rules`

---

### Task 3: 六 Agent 统一协议

**Files:**
- Create: `scripts/text_review/reviewers/tech_media.py`
- Modify: `scripts/text_review/reviewers/base.py`
- Modify: `scripts/text_review/reviewers/orchestrator.py`
- Modify: `scripts/text_review/reviewers/llm.py`
- Modify: `server/models.py`
- Modify: `server/schemas.py`
- Test: `tests/test_tech_media_agents.py`

**Interfaces:**
- Produces: `AgentReviewResult` and `AgentIssue` Pydantic models.
- Produces: `TechMediaReviewer.review_structured(context, profile) -> list[AgentReviewResult]`.

- [ ] **Step 1: Write failing protocol tests**

```python
def test_all_six_agents_return_the_same_schema(reviewer, context, profile):
    results = reviewer.review_structured(context, profile)
    assert [result.agent_id for result in results] == [
        "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY",
        "TEST_CREDIBILITY", "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
    ]
    assert all(issue.source_reference for result in results for issue in result.issues)
```

- [ ] **Step 2: Confirm RED**

Run: `python3 -m pytest tests/test_tech_media_agents.py -v`
Expected: missing protocol and reviewer.

- [ ] **Step 3: Define strict output models**

```python
class EvidenceSpan(BaseModel):
    quote: str
    start: int | None = None
    end: int | None = None
    asset_id: str | None = None
    timestamp: str | None = None

class AgentIssue(BaseModel):
    rule_id: str
    category: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    field: str
    evidence: EvidenceSpan
    reason: str
    suggestion: str
    source_reference: list[str]
    auto_fixable: bool
    human_required: bool
    confidence: float = Field(ge=0, le=1)
```

- [ ] **Step 4: Add shared prompt and six specialist prompts**

All prompts must distinguish official facts, test observations, subjective judgments and unsupported industry conclusions. `TEST_CREDIBILITY` receives test cases and evidence manifest; other agents receive only needed slices.

- [ ] **Step 5: Enforce JSON parsing and retry**

Temperature `0-0.2`, JSON-only output and maximum two retries. Parsing failure becomes an agent error requiring human review; it cannot become PASS.

- [ ] **Step 6: Persist source and evidence fields**

Extend `Issue` without losing existing `evidence_quote` compatibility. Add schema upgrade for current databases.

- [ ] **Step 7: Verify and commit**

Run: `python3 -m pytest tests/test_tech_media_agents.py tests/test_review_workflow.py -v`
Expected: PASS.

Commit: `Implement six tech media review agents`

---

### Task 4: 测试场景、证据与仲裁状态机

**Files:**
- Modify: `server/models.py`
- Modify: `server/schemas.py`
- Modify: `server/db.py`
- Create: `server/services/evidence_service.py`
- Create: `server/services/review_arbiter_service.py`
- Modify: `server/services/review_service.py`
- Test: `tests/test_evidence_workflow.py`
- Test: `tests/test_review_workflow.py`

**Interfaces:**
- Produces: `create_test_case(...) -> TestCase`
- Produces: `attach_evidence(...) -> TestEvidence`
- Produces: `arbitrate_review(agent_results, deterministic_issues) -> ArbitrationResult`

- [ ] **Step 1: Write failing model and arbitration tests**

```python
def test_missing_evidence_routes_to_human_not_fraud(arbiter):
    result = arbiter([issue("TEST-EVIDENCE-001", "HIGH", human_required=True)])
    assert result.review_status == ReviewStatus.HUMAN_REVIEW_REQUIRED


def test_campaign_score_alone_does_not_block(arbiter):
    result = arbiter([], campaign_score=45, suggestions=["开头缺少测试问题"])
    assert result.review_status == ReviewStatus.PASSED_WITH_SUGGESTIONS
```

- [ ] **Step 2: Confirm RED**

Run: `python3 -m pytest tests/test_evidence_workflow.py -v`
Expected: missing models/services/statuses.

- [ ] **Step 3: Add Asset, TestCase and TestEvidence**

Use foreign keys from content item/version to test cases and evidence. Preserve immutable supplier and AI versions. Add unique constraints for `(content_item_id, test_case_id)` and evidence bindings.

- [ ] **Step 4: Expand review status and schema upgrades**

Add `HUMAN_REVIEW_REQUIRED`, `SUPPLIER_REVISION_REQUIRED`, `AUTO_FIX_PENDING`, `PASSED`, `PASSED_WITH_SUGGESTIONS` and `BLOCKED`. Map legacy `APPROVED`, `MANUAL_REQUIRED` and `FIX_PROPOSED` records safely during startup upgrade.

- [ ] **Step 5: Implement arbitration and task generation**

Only allow-listed LOW issues create AI proposals. HIGH unknown facts/evidence create human tasks. Medium text issues create supplier revision tasks. No Agent may set `PUBLISHED`.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m pytest tests/test_evidence_workflow.py tests/test_review_workflow.py tests/test_database.py -v`
Expected: PASS.

Commit: `Add evidence-aware review workflow`

---

### Task 5: 多工作表 Excel 与 FastAPI 接口

**Files:**
- Modify: `server/services/excel_import_service.py`
- Modify: `server/services/excel_export_service.py`
- Modify: `server/main.py`
- Test: `tests/test_excel_import.py`
- Test: `tests/test_excel_workflow.py`
- Create: `tests/test_excel_api.py`

**Interfaces:**
- Preserves: existing token lifecycle, ZIP safety, idempotent confirm and export.
- Produces API: `/api/import-template`, `/api/imports/preview`, `/api/imports/{token}/confirm`, `/api/batches/{id}/export`, `/api/contents/table`.

- [ ] **Step 1: Write failing multi-sheet and API tests**

```python
def test_template_contains_content_test_case_and_help_sheets():
    workbook = load_workbook(BytesIO(build_import_template()))
    assert workbook.sheetnames == ["内容清单", "测试场景", "字段说明"]


def test_preview_rejects_unbound_evidence(client, workbook, evidence_zip):
    response = client.post("/api/imports/preview", files=...)
    assert response.status_code == 200
    assert "证据文件不存在" in response.json()["rows"][0]["errors"]
```

- [ ] **Step 2: Confirm RED**

Run: `python3 -m pytest tests/test_excel_api.py tests/test_excel_import.py -v`
Expected: missing routes/sheets.

- [ ] **Step 3: Extend template and preview manifest**

Parse only the named `内容清单` and `测试场景` sheets. Preserve current limits and security checks. Validate cross-sheet IDs, counts, commands, results and evidence basenames.

- [ ] **Step 4: Confirm into content/test/evidence records**

Import invalid rows with non-PASSED format status. Only valid rows can enter review. Keep confirmation idempotent by import token.

- [ ] **Step 5: Add download, preview, confirm, table and export routes**

Use stable preview storage under `CR_DATA_DIR`, temporary upload cleanup, safe attachment filenames and JSON response models.

- [ ] **Step 6: Extend exports**

Add test count, evidence count, evidence status and six Agent summaries while retaining formula-injection protection.

- [ ] **Step 7: Verify and commit**

Run: `python3 -m pytest tests/test_excel_import.py tests/test_excel_workflow.py tests/test_excel_api.py tests/test_api.py -v`
Expected: PASS.

Commit: `Add tech review Excel and API workflow`

---

### Task 6: React 科技测评工作台

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/pages/Upload.tsx`
- Modify: `web/src/pages/Review.tsx`
- Modify: `web/src/pages/Standards.tsx`
- Modify: `web/src/pages/Report.tsx`
- Create: `web/src/components/TestEvidencePanel.tsx`
- Create: `web/src/components/AgentResultPanel.tsx`
- Test: existing frontend type/build verification.

**Interfaces:**
- Consumes Task 5 APIs and existing content/task APIs.

- [ ] **Step 1: Add typed API contracts**

Define `ImportPreview`, `ContentTableRow`, `TestCase`, `EvidenceAsset`, `AgentReviewResult` and new review statuses. Add blob download helpers for template/export.

- [ ] **Step 2: Replace single-entry upload as the primary flow**

Build a stable stepper for project selection, Excel/ZIP upload, preview, test/evidence validation and confirmation. Keep manual single upload collapsed as a secondary tool.

- [ ] **Step 3: Implement table/detail review modes**

Default to a dense table with status/risk/evidence/task filters. Detail view uses three columns: content/Diff, evidence, Agent results/actions.

- [ ] **Step 4: Upgrade standards management**

Show six tabs and validation errors. Published versions are read-only. Draft edits must show a Diff before publishing.

- [ ] **Step 5: Upgrade reports**

Add evidence missing rate, pending claim count, rework count, Agent hit distribution, human correction rate and suggestion acceptance rate.

- [ ] **Step 6: Verify and commit**

Run: `npm run build` in `web`.
Expected: TypeScript and Vite production build PASS.

Commit: `Build tech media review workspace`

---

### Task 7: 集成、回归与文档

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Add focused fixtures under existing test modules only when required.

- [ ] **Step 1: Add a representative tech-review regression fixture**

Cover “5 个测试/正文 4 项”、“全赢”、“最优解”、“AI 订酒店”、“亲测无证据”和传播建议不阻断等 cases.

- [ ] **Step 2: Run full backend verification**

Run: `python3 -m pytest`
Expected: all tests pass with zero failures.

- [ ] **Step 3: Run frontend build**

Run: `npm run build` in `web`.
Expected: production build succeeds.

- [ ] **Step 4: Run static checks**

Run: `python3 -m compileall server scripts tests && git diff --check`
Expected: exit code 0 and no whitespace errors.

- [ ] **Step 5: Start local servers and verify integration**

Run the existing local start script with available ports. Verify health, project list, template download, preview, confirmation, table view, audit and export.

- [ ] **Step 6: Perform independent code review**

Review the full range from `1b4e576` to HEAD. Fix all Critical and Important findings, rerun affected tests and re-review.

- [ ] **Step 7: Update docs and commit**

Document the default tech-review profile, standard package, evidence workflow, OneAPI configuration and current V0.9 limitations.

Commit: `Complete tech media review V0.9`
