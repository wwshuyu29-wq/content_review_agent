# Task 6 Report: Review Desk Progress UI

## Status

Implemented and verified.

## Implementation commit

`662ed80f341c75ae0ad341413cd8006bbeec51a4`

## Changes

- Added `web/src/components/AuditProgressPanel.tsx` with semantic `<progress>`, explicit percentage and counters, current manuscript, elapsed/heartbeat information, six stable Agent rows, and the full manuscript status list.
- Updated `web/src/pages/Review.tsx` to restore the current/latest batch audit job, poll active jobs every two seconds, stop at terminal status, and refresh table/detail data.
- Switched batch starts to the audit-job API, prevented duplicate starts, changed the active action to `查看审核进度`, and exposed a visible start/disabled explanation.
- Auto-selects a project's sole batch, including when an invalid `batch_id` query parameter is present.
- Added responsive progress styles with stable row dimensions and a mobile two-row Agent layout.

## Verification

- `npm run build`: passed (TypeScript and Vite production build; 45 modules transformed).
- `python3 -m pytest -q tests/test_audit_jobs.py`: 15 passed.
- `python3 -m pytest -q`: 385 passed, 1 environment warning.
- `python3 -m compileall -q server scripts tests webapp`: passed.
- `git diff --check`: passed before the implementation commit.

## Concerns

- The frontend package has no unit-test runner, so frontend behavior is covered by TypeScript production build verification and existing backend API contract/full-suite tests rather than component-level timer/interaction tests.
- The full suite warning is the existing urllib3 `NotOpenSSLWarning` caused by the local Python build using LibreSSL 2.8.3; it did not fail tests.


## Minor review fix

- Wrapped the progress summary and counters in an `aria-live="polite"` region so periodic updates and terminal state are announced to screen readers.
- Added `test_audit_progress_summary_announces_updates_to_screen_readers` to the existing source-level frontend presentation tests.

## Fix verification

- Regression test first failed because the live region was absent, then passed after the implementation.
- `python3 -m pytest -q tests/test_web_presentation.py`: 5 passed.
- `python3 -m pytest -q`: 386 passed, 1 environment warning.
- `npm run build`: passed.
- `git diff --check`: passed.
