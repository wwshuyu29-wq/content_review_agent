# Task 3 Report: Background Executor and Agent Events

## Status

Implemented and verified on branch `feature/audit-progress`.

## Implementation

- Added `server/services/audit_executor_service.py` with a bounded `ThreadPoolExecutor`.
- `submit_audit_job(job_id)` submits only an integer job ID; the worker creates its reviewer and SQLAlchemy `Session` inside the background thread.
- Manuscripts execute sequentially. A manuscript failure is sanitized, committed, and does not stop later manuscripts.
- Batch, manuscript, and Agent transitions commit immediately so independent polling Sessions can observe progress.
- All terminal batch states clear `active_key`, `current_content_item_id`, and `current_agent_id`.
- Added optional `agent_started`, `agent_retry`, `agent_completed`, and `agent_failed` callbacks to the six-Agent reviewer.
- Preserved the existing no-callback call path, including compatibility with `TechMediaReviewer` subclasses that retain the previous `review_structured(context, profile)` signature.
- Valid matching content/rule audits are skipped.
- Complete six-Agent unavailable-only audits can be superseded without deleting history; incomplete unavailable records are not eligible.
- Failed historical audit runs do not block a clean retry and remain in history.
- Persisted executor errors use a stable sanitized Chinese message; callback payloads do not expose transport errors, credentials, URLs, raw responses, or stack traces.
- Application startup configures the worker reviewer factory after database initialization and stale-job interruption.

## TDD Evidence

Initial focused RED run:

```text
7 failed, 40 passed
```

The failures covered the missing executor service, missing callback API, and unavailable-audit supersession behavior.

Additional RED/GREEN regression cycles covered:

- incomplete unavailable audit histories must not be superseded;
- callback persistence failures must not retry a valid model response;
- failed historical audits must permit retry without history deletion;
- legacy no-callback reviewer subclasses must remain compatible.

## Verification

Focused Task 3 plus callback-compatibility tests:

```text
52 passed in 4.66s
```

Full Python suite:

```text
371 passed, 1 warning in 19.93s
```

Static checks:

```text
git diff --check: passed
python3 -m py_compile changed modules: passed
```

## Self-review

Corrected during self-review:

1. Required an exact, completed six-Agent unavailable result set before supersession.
2. Ignored failed historical audits when enforcing duplicate valid-audit protection.
3. Prevented progress callback persistence failures from being mistaken for model failures and retried.
4. Preserved no-callback compatibility for existing reviewer subclasses.
5. Removed a test-only bounded-queue slot leak.

## Concerns

- The full suite retains one pre-existing environment warning: `urllib3` reports LibreSSL 2.8.3 while preferring OpenSSL 1.1.1+. It does not fail tests.
- The executor is intentionally in-process for V1. Process termination is handled by startup stale-job interruption rather than durable external queue delivery.

## P1 Follow-up: Atomic Claim and Historical Valid-Audit Priority

### Fixes

- Workers now claim a job with one conditional SQL `UPDATE ... WHERE id = :job_id AND status = 'QUEUED'` and proceed only when `rowcount == 1`.
- A `RUNNING` job cannot be independently executed by a duplicate submission; reviewer construction and model calls happen only after the successful claim commit.
- Duplicate concurrent submissions now have a regression covering one model workflow, one audit record, and one consistent manuscript/Agent progress tree.
- Matching-audit lookup now scans all completed content-version/rule-version history and skips whenever any valid completed audit exists, regardless of a newer `FAILED`, `SUPERSEDED`, or unavailable-only audit.
- Unavailable-only supersession remains allowed only when no valid completed matching audit exists.
- Historical skip regressions assert that no reviewer/model call occurs and that newer invalid history remains unchanged.

### TDD Evidence

Initial P1 RED run:

```text
5 failed in 11.51s
```

The failures reproduced duplicate execution of a `RUNNING` job, newest-row-only matching, and incorrect supersession of unavailable history despite an older valid audit.

Final covering suite:

```text
55 passed in 5.14s
```

Final full suite:

```text
376 passed, 1 warning in 22.26s
```

The remaining warning is the existing local LibreSSL/urllib3 compatibility warning described above.
