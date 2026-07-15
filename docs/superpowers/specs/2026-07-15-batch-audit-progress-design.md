# Batch Audit Progress Design

## Goal

Make long-running batch reviews observable and restart-aware. Starting a review must return immediately, while the review desk shows batch, manuscript, and six-Agent progress that survives page refreshes.

## Current Problem

`POST /api/batches/{batch_id}/audit` currently reviews every manuscript synchronously. Manuscripts run sequentially, and each manuscript calls all six Agents sequentially. A ten-manuscript OneAPI batch takes about four minutes, while the browser waits for the full response and receives no progress updates.

## Chosen Approach

Use database-backed jobs, a controlled in-process executor for local V1, and frontend polling. Keep the executor behind a service boundary so CNAP deployment can later replace it with a Redis-backed worker without changing the API or UI contract.

## State Model

### Batch job

- `QUEUED`
- `RUNNING`
- `COMPLETED`
- `COMPLETED_WITH_ERRORS`
- `FAILED`
- `INTERRUPTED`

Persist total, completed, failed, skipped, current manuscript, current Agent, timestamps, heartbeat, model, and a sanitized error summary.

### Manuscript job

- `PENDING`
- `RUNNING`
- `COMPLETED`
- `FAILED`
- `SKIPPED`

Persist manuscript order, content ID, timestamps, and sanitized error summary.

### Agent progress

- `PENDING`
- `RUNNING`
- `COMPLETED`
- `FAILED`

Persist Agent ID, attempt number, timestamps, duration, decision, score, and sanitized error summary. Completed review output remains in the existing `AgentResult` model; progress records describe in-flight execution.

## API

### Start or resume a batch job

`POST /api/batches/{batch_id}/audit-jobs`

Return within one second:

```json
{
  "job_id": 123,
  "batch_id": 1,
  "status": "QUEUED"
}
```

If the batch already has a non-terminal job, return that job rather than starting a duplicate.

### Read job progress

`GET /api/audit-jobs/{job_id}`

Return batch counters, current manuscript, current Agent, heartbeat, elapsed time, manuscript rows, and six Agent rows for the current manuscript.

### Read the current or latest batch job

`GET /api/batches/{batch_id}/audit-job`

Used to restore progress after refresh or login.

The existing synchronous endpoints remain temporarily compatible but the frontend uses the job endpoints.

## Execution Flow

1. Validate the batch and create the job plus manuscript and Agent progress rows in one transaction.
2. Submit only the job ID to the controlled executor.
3. The worker creates its own database Session; request Sessions are never shared across threads.
4. Mark the job and first manuscript `RUNNING`, commit, then update the heartbeat.
5. Before each Agent call, mark that Agent `RUNNING` and commit.
6. After each Agent call or retry, persist the latest attempt and state immediately.
7. Complete and arbitrate the manuscript using the existing review service, then commit its terminal state.
8. Continue after a manuscript failure; finish the batch as `COMPLETED_WITH_ERRORS` when appropriate.
9. Refresh the review table after the job reaches a terminal state.

V1 processes one manuscript at a time to avoid sudden OneAPI rate-limit pressure. The design permits a later configurable concurrency limit.

## Review Service Integration

Refactor the six-Agent loop to accept an optional progress callback. The callback receives Agent start, retry, completion, and failure events. Existing callers without a callback retain current behavior.

A final Agent failure produces a controlled system issue and routes the manuscript to human review. It must never make the content publishable by default.

## Review Desk UI

Add a persistent batch progress band below the filters:

- Overall percentage and `completed / total` manuscripts.
- Current manuscript sequence and title.
- Completed, failed, and waiting counters.
- Elapsed time and last heartbeat age.
- Six Agent rows with status, decision, score, attempts, and duration.
- A collapsible manuscript list with each manuscript state.

Poll every two seconds while a job is non-terminal. Stop polling on terminal state and refresh the content table. On page load or batch change, fetch the current or latest job.

When a non-terminal job exists, the start button becomes `查看审核进度`. When no concrete batch is selected, the disabled button must visibly explain that a batch is required.

## Chinese Review Presentation

The review desk is for Chinese-speaking business reviewers. Internal enum values, rule IDs, HTTP errors, URLs, stack traces, and raw JSON must not be the primary presentation.

- Translate Agent decisions to `通过`, `通过但有建议`, `需要修改`, `需要人工确认`, and `阻断`.
- Translate severity, field, category, review status, publish status, and task type values to Chinese.
- Show a score only when the model successfully returns a valid score. Unavailable model output displays `未评分`, never a synthetic `0 分`.
- Replace `查看原始结果` with `查看审核详情` containing Chinese summary, quoted evidence, reason, suggestion, confidence, and source description.
- Hide internal rule IDs and raw JSON from the normal review desk. Preserve them in the database for audit and engineering diagnosis.
- Convert technical failures into a stable Chinese business message. Never expose gateway URLs, API keys, raw response bodies, or stack traces.
- Apply the same translations to structured findings, six-Agent cards, task actions, and report distributions.

## OneAPI Structured Output Compatibility

The configured `gpt-5.6-sol` model supports semantic analysis and strict JSON Schema, but strict mode requires every property to appear in `required`; optional values must be required and nullable. The runtime schema adapter must transform Pydantic output accordingly without weakening Pydantic validation after the response returns.

- Recursively require every object property.
- Represent optional fields as nullable types while preserving constraints.
- Preserve `additionalProperties: false` for strict objects.
- Keep final validation through `AgentReviewResult.model_validate`.
- Capture a sanitized OneAPI error message for engineering logs and progress state.
- Existing failed audits with unavailable fallback results are not valid semantic reviews and must be rerun after the compatibility fix.

## Failure and Recovery

- OneAPI timeout or invalid JSON follows the existing retry policy and updates the attempt count.
- A failed Agent or manuscript stores a sanitized error summary and does not stop later manuscripts.
- Never expose API keys, complete model responses, or stack traces through progress APIs.
- On application startup, mark stale `QUEUED` or `RUNNING` jobs as `INTERRUPTED` when their heartbeat exceeds the configured threshold.
- A new job may resume only unfinished manuscripts after interruption; already completed content versions are not audited twice with the same rule version.

## Testing

- Model constraints and duplicate active-job protection.
- Start endpoint returns before execution completes.
- Worker uses independent Sessions and persists transitions.
- Agent callback records start, retry, completion, and failure.
- One manuscript failure does not stop the batch.
- Progress API counters match persisted manuscript states.
- Refresh restoration returns the active job.
- Stale jobs become `INTERRUPTED`.
- Frontend builds and renders overall plus six-Agent progress states.
- Existing full backend suite remains green.

## Acceptance Criteria

- Starting a batch returns a job ID within one second.
- The page displays manuscript and Agent progress within two seconds.
- Progress survives refresh and re-login.
- Database counters and UI counters agree.
- A failed manuscript does not stop the remaining batch.
- Duplicate active jobs for one batch are impossible.
- Completion refreshes six-Agent findings and human tasks automatically.
- `gpt-5.6-sol` completes the strict structured semantic review without a Schema `400` response.
- Failed model calls display `未评分` rather than `0 分`.
- Normal business-facing pages contain no untranslated enum labels, internal rule codes, raw JSON, gateway URLs, or English fallback instructions.
