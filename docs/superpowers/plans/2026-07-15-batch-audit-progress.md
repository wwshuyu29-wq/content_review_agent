# Batch Audit Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make six-Agent batch review asynchronous and observable, repair OneAPI strict structured output, and present all business-facing review results in Chinese.

**Architecture:** Persist batch, manuscript, and Agent progress in SQLAlchemy; execute jobs through a controlled background executor using independent database Sessions; expose start and polling APIs; render progress and translated result details in React. Keep strict Pydantic validation while adapting its JSON Schema to OneAPI strict-mode requirements.

**Tech Stack:** Python 3.9, FastAPI, SQLAlchemy 2, Pydantic 2, pytest, React 18, TypeScript 5.6, Vite 5.

## Global Constraints

- Process one manuscript at a time in V1 to avoid OneAPI rate-limit pressure.
- Return a batch job ID within one second and poll every two seconds.
- Never share a request SQLAlchemy Session with a background thread.
- Never expose API keys, gateway URLs, raw response bodies, stack traces, raw JSON, or internal rule IDs in business-facing UI.
- Show `未评分` when semantic review is unavailable; never use synthetic `0 分`.
- Preserve strict `AgentReviewResult.model_validate` validation after the model response.
- Existing failed audits are not valid semantic reviews and must be rerun after the compatibility fix.

---

### Task 1: OneAPI Strict Schema Compatibility

**Files:**
- Modify: `scripts/text_review/reviewers/llm.py`
- Modify: `scripts/text_review/reviewers/tech_media.py`
- Test: `tests/test_image_evidence_service.py`
- Test: `tests/test_tech_media_agents.py`

**Interfaces:**
- Produces: `oneapi_strict_schema(schema: dict[str, Any]) -> dict[str, Any]`
- Produces: sanitized Chinese unavailable results with `score=None` support.

- [ ] **Step 1: Write failing schema tests**

Assert every object recursively has `required == properties.keys()`, formerly optional fields such as `EvidenceSpan.asset_id` are nullable, and source Pydantic Schema is not mutated.

```python
def test_oneapi_schema_requires_nullable_optional_properties():
    source = AgentReviewResult.model_json_schema()
    adapted = oneapi_strict_schema(source)
    evidence = adapted["$defs"]["EvidenceSpan"]
    assert set(evidence["required"]) == set(evidence["properties"])
    assert "null" in evidence["properties"]["asset_id"]["anyOf"][-1]["type"]
    assert "asset_id" not in source["$defs"]["EvidenceSpan"].get("required", [])
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_image_evidence_service.py tests/test_tech_media_agents.py`

Expected: import or assertion failure because the adapter does not exist.

- [ ] **Step 3: Implement the recursive adapter and useful HTTP errors**

Deep-copy the Schema, recursively set object `required`, preserve `additionalProperties: false`, and retain nullable `anyOf` branches. In `_request_content`, parse a failed response body into a sanitized exception without including URLs or credentials.

```python
def oneapi_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    adapted = copy.deepcopy(schema)
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(adapted)
    return adapted
```

- [ ] **Step 4: Replace fallback score and copy**

Change unavailable Agent results to a nullable score contract and Chinese summary/reason/suggestion. Ensure Pydantic and API types accept `score: Optional[int]` only for unavailable system results, while successful model results still require `0..100`.

- [ ] **Step 5: Verify GREEN and real gateway compatibility**

Run focused tests, then run a single sanitized real OneAPI probe with `AgentReviewResult` and assert HTTP 200 plus successful `model_validate`.

- [ ] **Step 6: Commit**

```bash
git add scripts/text_review/reviewers/llm.py scripts/text_review/reviewers/tech_media.py tests/test_image_evidence_service.py tests/test_tech_media_agents.py
git commit -m "Fix OneAPI strict review schema"
```

### Task 2: Persistent Audit Job Models

**Files:**
- Modify: `server/models.py`
- Modify: `server/db.py`
- Modify: `server/schemas.py`
- Create: `server/services/audit_job_service.py`
- Create: `tests/test_audit_jobs.py`

**Interfaces:**
- Produces: `create_or_get_active_job(session: Session, batch_id: int, model: str) -> BatchAuditJob`
- Produces: `get_job_progress(session: Session, job_id: int) -> AuditJobProgressRead`
- Produces models `BatchAuditJob`, `ManuscriptAuditJob`, `AgentAuditProgress`.

- [ ] **Step 1: Write failing model and duplicate-job tests**

Cover state defaults, ten manuscript rows, six Agent rows per manuscript, unique active job behavior, and counter serialization.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_audit_jobs.py`

Expected: model/service imports fail.

- [ ] **Step 3: Implement models and schema upgrade**

Add indexed foreign keys and timestamps. Use a deterministic active-job key or transaction-safe uniqueness mechanism so concurrent starts return one active job.

- [ ] **Step 4: Implement creation and read services**

Create all progress rows in one transaction. Calculate counters from persisted manuscript states rather than trusting increment-only fields.

- [ ] **Step 5: Add stale-job interruption**

Implement `interrupt_stale_jobs(session, stale_before)` and call it during application startup after schema initialization.

- [ ] **Step 6: Verify GREEN and commit**

Run focused tests and commit model, migration, service, schema, and tests.

### Task 3: Background Executor and Agent Events

**Files:**
- Create: `server/services/audit_executor_service.py`
- Modify: `server/services/review_service.py`
- Modify: `scripts/text_review/reviewers/tech_media.py`
- Modify: `server/main.py`
- Test: `tests/test_audit_jobs.py`
- Test: `tests/test_review_workflow.py`

**Interfaces:**
- Consumes: persistent job models from Task 2.
- Produces: `submit_audit_job(job_id: int) -> None`
- Produces: `run_audit_job(job_id: int, reviewer_factory: Callable[[], Any]) -> None`
- Produces: optional progress callback events `agent_started`, `agent_retry`, `agent_completed`, `agent_failed`.

- [ ] **Step 1: Write failing execution tests**

Use a blocking fake reviewer to prove submission returns before completion, each Agent transition commits, one manuscript failure does not stop later manuscripts, and the worker opens independent Sessions.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_audit_jobs.py tests/test_review_workflow.py`

- [ ] **Step 3: Add progress callback to six-Agent loop**

Emit events immediately before requests, after validation, on retry, and on terminal failure. Preserve behavior for callers that pass no callback.

- [ ] **Step 4: Implement controlled executor**

Use a bounded `ThreadPoolExecutor`, submit only integer job IDs, create reviewer and database Session inside the worker, commit every state transition, and sanitize errors.

- [ ] **Step 5: Handle existing audits and interruptions**

Skip content already validly audited with the same content/rule version; allow invalid unavailable-only audit runs to be superseded or explicitly rerun without deleting history.

- [ ] **Step 6: Verify GREEN and commit**

Run focused tests and commit.

### Task 4: Job Start and Progress APIs

**Files:**
- Modify: `server/main.py`
- Modify: `server/schemas.py`
- Modify: `web/src/api.ts`
- Test: `tests/test_api.py`
- Test: `tests/test_excel_api.py`

**Interfaces:**
- Produces: `POST /api/batches/{batch_id}/audit-jobs`
- Produces: `GET /api/audit-jobs/{job_id}`
- Produces: `GET /api/batches/{batch_id}/audit-job`
- Frontend types: `AuditJobProgress`, `ManuscriptProgress`, `AgentProgress`.

- [ ] **Step 1: Write failing API tests**

Assert authenticated/CSRF behavior, immediate start response, duplicate start reuse, full progress payload, batch restoration, and absence of raw technical errors.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_api.py tests/test_excel_api.py`

- [ ] **Step 3: Implement endpoints**

Start endpoint creates or returns the active job and submits it after commit. Read endpoints enforce batch/project ownership and return typed progress.

- [ ] **Step 4: Add frontend API contracts**

Add `startAuditJob`, `auditJob`, and `batchAuditJob` methods without removing the old synchronous method until migration is complete.

- [ ] **Step 5: Verify GREEN and commit**

Run focused tests plus `npm run build`, then commit.

### Task 5: Chinese Presentation Adapter

**Files:**
- Create: `web/src/reviewLabels.ts`
- Modify: `web/src/components/AgentResultPanel.tsx`
- Modify: `web/src/pages/Review.tsx`
- Modify: `web/src/pages/Report.tsx`
- Modify: `server/schemas.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces translation functions for Agent IDs, decisions, statuses, severities, categories, fields, publish states, and tasks.
- Produces business-safe Agent detail payload without raw JSON.

- [ ] **Step 1: Write failing API privacy tests**

Assert normal detail responses expose Chinese-safe error summaries and do not return gateway URLs or stack traces in display fields. Keep raw audit data available only in persisted records.

- [ ] **Step 2: Verify RED**

Run relevant API tests.

- [ ] **Step 3: Implement centralized translations**

Map all known enums to Chinese and use a stable fallback `未知状态`, never the raw enum string.

```typescript
export const decisionLabel = (value?: string | null) => ({
  PASS: "通过",
  PASS_WITH_SUGGESTIONS: "通过但有建议",
  NEED_TEXT_FIX: "需要修改",
  HUMAN_REVIEW: "需要人工确认",
  BLOCK: "阻断",
}[value || ""] || "未给出结论");
```

- [ ] **Step 4: Replace raw result UI**

Remove internal Agent IDs, raw JSON, raw rule IDs, English statuses, and synthetic zero scores. Add `查看审核详情` with Chinese summary, evidence, reason, suggestion, confidence, and source description.

- [ ] **Step 5: Translate structured findings and reports**

Apply the same adapter to issue headers, state badges, publish state, task types, and report distributions.

- [ ] **Step 6: Build and commit**

Run `npm run build`, inspect the generated page manually at desktop width, and commit.

### Task 6: Review Desk Progress UI

**Files:**
- Create: `web/src/components/AuditProgressPanel.tsx`
- Modify: `web/src/pages/Review.tsx`
- Modify: `web/src/styles.css` or the existing global stylesheet
- Test: backend API contract tests plus frontend build.

**Interfaces:**
- Consumes: `AuditJobProgress` APIs from Task 4 and translations from Task 5.
- Produces: persistent overall progress, manuscript list, and current six-Agent progress.

- [ ] **Step 1: Implement state restoration and polling logic**

On batch selection, load the current/latest job. Poll every two seconds for non-terminal jobs, stop on terminal state, and refresh table/detail data.

- [ ] **Step 2: Implement progress component**

Render percentage, completed/total, success/failure/waiting, current manuscript, elapsed time, heartbeat age, and six stable Agent rows. Use semantic `<progress>` plus text so progress is accessible.

- [ ] **Step 3: Fix start-button behavior**

Auto-select the only available batch. Show visible disabled reason when no batch is selected. Change the button to `查看审核进度` when a job is active and prevent duplicate starts.

- [ ] **Step 4: Verify responsive layout**

Ensure labels do not overlap at desktop and mobile widths; progress rows retain stable dimensions.

- [ ] **Step 5: Build and commit**

Run `npm run build`, verify local endpoints, and commit.

### Task 7: Rerun Real Batch and Final Verification

**Files:**
- Modify only if verification exposes a tested defect.

- [ ] **Step 1: Run focused and full automated verification**

Run:

```bash
python3 -m pytest -q
npm run build
```

Expected: zero failures; only the known local LibreSSL warning may remain.

- [ ] **Step 2: Start local services and run one real manuscript**

Confirm the job returns immediately, all six Agents reach terminal states, no Schema `400` occurs, and scores/summaries are valid Chinese model output.

- [ ] **Step 3: Rerun the ten-manuscript batch**

Create a new job that supersedes unavailable-only historical audits without deleting them. Observe progress from start to completion.

- [ ] **Step 4: Verify business UI**

Confirm no raw JSON, internal rule IDs, gateway URLs, English decision/status labels, or synthetic `0 分` appear. Confirm progress survives refresh.

- [ ] **Step 5: Run final full suite and review diff**

Run full pytest, frontend build, `git diff --check`, and a focused code review. Fix any Critical or Important findings before completion.

- [ ] **Step 6: Commit final verification fixes if any**

Use a scoped commit message and leave the local service running for user validation.
