from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from scripts.text_review.reviewers.tech_media import AGENT_ORDER
from server.models import PublishStatus, ReviewStatus


@dataclass(frozen=True)
class ReviewTaskSpec:
    task_type: str
    issue_keys: tuple[str, ...] = ()
    blocking: bool = True


@dataclass(frozen=True)
class ArbitrationResult:
    review_status: ReviewStatus
    publish_status: PublishStatus
    task_specs: tuple[ReviewTaskSpec, ...] = ()
    ai_proposal_allowed: bool = False
    reason: str = ""


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _issue_key(issue: Any) -> str:
    return str(_value(issue, "rule_id", "UNKNOWN"))


def _task(task_type: str, issues: Iterable[Any]) -> ReviewTaskSpec:
    return ReviewTaskSpec(task_type=task_type, issue_keys=tuple(dict.fromkeys(_issue_key(issue) for issue in issues)))


def _protocol_valid(agent_results: Sequence[Any]) -> bool:
    if not agent_results:
        return True
    if len(agent_results) != len(AGENT_ORDER):
        return False
    ids = [_value(result, "agent_id", _value(result, "agent_name")) for result in agent_results]
    if ids != list(AGENT_ORDER):
        return False
    for result in agent_results:
        decision = str(_value(result, "decision", "")).upper()
        if decision not in {"PASS", "PASS_WITH_SUGGESTIONS", "NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"}:
            return False
    return True


def _is_safe_auto_fix(issue: Any) -> bool:
    if str(_value(issue, "severity", "")).upper() != "LOW" or not bool(_value(issue, "auto_fixable", False)):
        return False
    text = " ".join(str(_value(issue, key, "")) for key in ("rule_id", "category", "field", "reason")).upper()
    forbidden = {"EVIDENCE", "TEST", "RESULT", "CLAIM", "FACT", "FUNCTION", "FEATURE", "NUMBER", "DIGIT", "COUNT"}
    if any(token in text for token in forbidden):
        return False
    if any(character.isdigit() for character in str(_value(issue, "evidence_quote", "")) + str(_value(issue, "suggestion", ""))):
        return False
    allowed = {"BRAND", "REPLACE", "QUALITY", "TEXT", "TYPO", "PUNCT", "SPELL", "WORDING", "ABSOLUTE"}
    return any(token in text for token in allowed)


def _needs_human(issue: Any) -> bool:
    severity = str(_value(issue, "severity", "")).upper()
    text = " ".join(str(_value(issue, key, "")) for key in ("rule_id", "category", "reason")).upper()
    return (
        severity in {"HIGH", "UNKNOWN"}
        or bool(_value(issue, "human_required", False))
        or "SYSTEM" in text
        or "UNAVAILABLE" in text
        or "PENDING" in text
        or (any(token in text for token in ("EVIDENCE", "CLAIM", "FACT", "FUNCTION", "FEATURE", "NUMBER", "TEST_RESULT"))
            and not _is_safe_auto_fix(issue))
    )


def arbitrate_review(
    agent_results: Sequence[Any],
    deterministic_issues: Sequence[Any],
    *,
    campaign_score: Optional[int] = None,
    suggestions: Optional[Sequence[str]] = None,
) -> ArbitrationResult:
    agent_results = list(agent_results or [])
    issues = list(deterministic_issues or [])
    for result in agent_results:
        issues.extend(list(_value(result, "issues", []) or []))

    if not _protocol_valid(agent_results):
        return ArbitrationResult(
            ReviewStatus.HUMAN_REVIEW_REQUIRED, PublishStatus.NOT_READY,
            (_task("HUMAN_REVIEW", issues),), reason="missing or invalid agent protocol",
        )

    decisions = [str(_value(result, "decision", "")).upper() for result in agent_results]
    critical = [issue for issue in issues if str(_value(issue, "severity", "")).upper() == "CRITICAL"]
    if critical or "BLOCK" in decisions:
        return ArbitrationResult(
            ReviewStatus.BLOCKED, PublishStatus.NOT_READY,
            (_task("BLOCK_REVIEW", critical or issues),), reason="critical issue or explicit BLOCK",
        )

    human = [issue for issue in issues if _needs_human(issue)]
    if human or any(decision == "HUMAN_REVIEW" for decision in decisions):
        return ArbitrationResult(
            ReviewStatus.HUMAN_REVIEW_REQUIRED, PublishStatus.NOT_READY,
            (_task("HUMAN_REVIEW", human or issues),), reason="human verification required",
        )

    medium = [issue for issue in issues if str(_value(issue, "severity", "")).upper() in {"MEDIUM", "MID"}]
    if medium or "NEED_TEXT_FIX" in decisions:
        return ArbitrationResult(
            ReviewStatus.SUPPLIER_REVISION_REQUIRED, PublishStatus.NOT_READY,
            (_task("SUPPLIER_REVISION", medium or issues),), reason="supplier revision required",
        )

    low = [issue for issue in issues if str(_value(issue, "severity", "")).upper() == "LOW"]
    if low and all(_is_safe_auto_fix(issue) for issue in low):
        return ArbitrationResult(
            ReviewStatus.AUTO_FIX_PENDING, PublishStatus.NOT_READY,
            (_task("AUTO_FIX_PROPOSAL", low),), ai_proposal_allowed=True,
            reason="allowlisted low-risk text replacements",
        )

    has_suggestions = bool(low or suggestions or any(decision == "PASS_WITH_SUGGESTIONS" for decision in decisions))
    if has_suggestions or (campaign_score is not None and campaign_score < 60):
        return ArbitrationResult(
            ReviewStatus.PASSED_WITH_SUGGESTIONS, PublishStatus.READY,
            reason="nonblocking suggestions only",
        )

    return ArbitrationResult(ReviewStatus.PASSED, PublishStatus.READY, reason="all required checks passed")
