from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.text_review import schema
from scripts.text_review.reviewers.tech_media import (
    AGENT_ORDER, TechMediaReviewer, role_boundary_error, validate_agent_result,
)
from scripts.text_review.standards import Standards
from server.models import (
    AgentResult,
    AuditRun,
    Batch,
    ContentItem,
    ContentVersion,
    FormatStatus,
    HumanDecision,
    Issue,
    PublishStatus,
    ReviewStatus,
    ReviewTask,
    ReviewTaskIssue,
    RuleVersion,
)
from server.services.content_service import validate_content_format
from server.services.deterministic_rule_service import ReviewContext, StructuredIssue, evaluate_rules
from server.services.evidence_service import list_content_test_cases
from server.services.review_arbiter_service import ArbitrationResult, ReviewTaskSpec, arbitrate_review
from server.services.review_profile_service import get_review_profile

MANUAL_SEVERITIES = {"mid", "high", "unknown"}
ISSUE_FIELDS = (
    "rule_id", "category", "severity", "field", "evidence_quote", "evidence_start",
    "evidence_end", "evidence_asset_id", "evidence_timestamp", "reason", "suggestion",
    "source_reference", "auto_fixable", "human_required", "confidence",
)
TECH_MEDIA_INLINE_RULE_MATCHERS = {"exact_phrase", "phrase_list", "replacement_map", "required_term"}
FAST_REVIEW_IGNORED_RULE_IDS = {
    "TEST-COUNT-001",
    "TEST-EVIDENCE-001",
}
FAST_REVIEW_IGNORED_TERMS = ("证据", "测试", "实测", "亲测")
MVP_DIMENSION_KEYS = {
    "CONTENT_QUALITY",
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "CAMPAIGN_EFFECTIVENESS",
}


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _normalize_issue_category(
    category: Any,
    *,
    rule_id: Any = "",
    reason: Any = "",
    suggestion: Any = "",
    field: Any = "",
    evidence_quote: Any = "",
) -> str:
    raw_category = str(category or "")
    if raw_category in {"system", "system_suggestion"}:
        return raw_category
    if raw_category in MVP_DIMENSION_KEYS:
        return raw_category
    rule = str(rule_id or "")
    searchable = " ".join(
        str(value or "")
        for value in (rule, raw_category, reason, suggestion, field, evidence_quote)
    )
    if raw_category in {"内容质量", "基础内容校对", "字符与标签格式", "标点与空格", "标签格式", "标题与正文一致性", "科技测评结构与信息聚焦", "语病"}:
        return "CONTENT_QUALITY"
    if raw_category in {"品牌一致性", "产品定位露出", "产品露出清晰度", "品牌与产品名称露出", "品牌标签规范", "品牌露出清晰度"} or rule.startswith("BRAND") or _contains_any(
        searchable, ("品牌", "官方名称", "产品名", "卖点口径")
    ):
        return "BRAND"
    if raw_category in {"合规表达", "合规审核"} or rule.startswith("CLAIM") or _contains_any(
        searchable, ("合规", "绝对", "保证", "承诺", "夸大", "广告法", "未经确认")
    ):
        return "COMPLIANCE"
    if raw_category in {"产品准确性"} or _contains_any(
        searchable, ("功能", "能力", "路线", "规划", "导航", "产品准确", "事实错误", "讲错")
    ):
        return "PRODUCT_ACCURACY"
    if raw_category in {"传播有效性", "核心价值聚焦", "场景聚焦与测评结构"} or _contains_any(
        searchable, ("传播", "卖点", "转化", "受众", "场景", "标题吸引", "种草")
    ):
        return "CAMPAIGN_EFFECTIVENESS"
    return "CONTENT_QUALITY"


def _is_system_unavailable_issue(issue_data: Mapping[str, Any]) -> bool:
    return (
        str(issue_data.get("rule_id", "")) == "SYSTEM-LLM-UNAVAILABLE"
        or str(issue_data.get("category", "")) in {"system", "system_suggestion"}
    )


def _is_system_unavailable_result(result: Mapping[str, Any]) -> bool:
    issues = list(result.get("issues", []) or [])
    return bool(issues) and all(
        isinstance(issue, Mapping) and _is_system_unavailable_issue(issue)
        for issue in issues
    )


def _agent_review_result_is_unavailable(result: Any) -> bool:
    issues = list(getattr(result, "issues", []) or [])
    return bool(issues) and all(
        getattr(issue, "rule_id", "") == "SYSTEM-LLM-UNAVAILABLE"
        or getattr(issue, "category", "") in {"system", "system_suggestion"}
        for issue in issues
    )


def _agent_review_results_are_unavailable(results: list[Any]) -> bool:
    return bool(results) and all(_agent_review_result_is_unavailable(result) for result in results)


def _is_fast_review_ignored_issue(issue_data: Mapping[str, Any]) -> bool:
    if _is_system_unavailable_issue(issue_data):
        return True
    rule_id = str(issue_data.get("rule_id", ""))
    if rule_id in FAST_REVIEW_IGNORED_RULE_IDS:
        return True
    searchable = " ".join(
        str(issue_data.get(key, ""))
        for key in ("rule_id", "category", "reason", "suggestion")
    )
    return any(term in searchable for term in FAST_REVIEW_IGNORED_TERMS)


def _simplify_fast_review_agent_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_real_result = any(not _is_system_unavailable_result(result) for result in results)
    simplified = []
    for result in results:
        issues = list(result.get("issues", []))
        if issues and all(isinstance(issue, Mapping) and _is_system_unavailable_issue(issue) for issue in issues):
            simplified.append(result)
            continue
        kept_issues = [
            issue for issue in issues
            if not (
                _is_fast_review_ignored_issue(issue)
                and (has_real_result or not _is_system_unavailable_issue(issue))
            )
        ]
        if len(kept_issues) == len(issues):
            simplified.append(result)
            continue
        updated = {**result, "issues": kept_issues}
        if not kept_issues and updated.get("decision") in {"HUMAN_REVIEW", "BLOCK", "NEED_TEXT_FIX", "PASS_WITH_SUGGESTIONS"}:
            only_removed_system_unavailable = bool(issues) and all(
                isinstance(issue, Mapping) and _is_system_unavailable_issue(issue)
                for issue in issues
            )
            if not only_removed_system_unavailable:
                updated["decision"] = "PASS"
            if updated.get("score") is None and not only_removed_system_unavailable:
                updated["score"] = 95
            if not only_removed_system_unavailable:
                updated["summary"] = "未发现明确影响发布的问题。"
        simplified.append(updated)
    return simplified


def _normalized_agent_result(result: Any) -> dict[str, Any]:
    issues = []
    for issue in result.issues:
        evidence = issue.evidence
        issues.append({
            "rule_id": issue.rule_id,
            "category": _normalize_issue_category(
                issue.category,
                rule_id=issue.rule_id,
                reason=issue.reason,
                suggestion=issue.suggestion,
                field=issue.field,
                evidence_quote=evidence.quote,
            ),
            "severity": issue.severity,
            "field": issue.field,
            "evidence_quote": evidence.quote,
            "evidence_start": evidence.start,
            "evidence_end": evidence.end,
            "evidence_asset_id": evidence.asset_id,
            "evidence_timestamp": evidence.timestamp,
            "reason": issue.reason,
            "suggestion": issue.suggestion,
            "source_reference": issue.source_reference,
            "auto_fixable": issue.auto_fixable,
            "human_required": issue.human_required,
            "confidence": issue.confidence,
        })
    return {
        "agent_name": result.agent_id,
        "agent_id": result.agent_id,
        "agent_version": result.agent_version,
        "decision": result.decision,
        "summary": result.summary,
        "score": result.score,
        "status": result.decision,
        "issues": issues,
        "raw_result": result.model_dump(mode="json"),
    }


class _PrecomputedReviewer:
    def __init__(self, results: list[Any]):
        self._results = results

    def review_structured(self, _row: dict, _standards: Standards) -> list[dict[str, Any]]:
        return [_normalized_agent_result(result) for result in self._results]


def validate_rule_version_identity(project, rule_version: RuleVersion) -> None:
    if not project.code or not project.content_type:
        raise ValueError("project is missing code or content_type")
    expected = {
        "business_domain": "baidu_maps_marketing_review",
        "document_type": "project_standard",
        "project_code": project.code,
        "content_type": project.content_type,
    }
    for field, value in expected.items():
        if getattr(rule_version, field) != value:
            raise ValueError(f"rule version identity mismatch: {field}")
    if not rule_version.package_version:
        raise ValueError("rule version identity mismatch: package_version")
    if not rule_version.package_digest:
        raise ValueError("rule version identity mismatch: package_digest")


def _standards_from_rule_version(rule_version: RuleVersion) -> Standards:
    rules = rule_version.structured_rules
    facts_text = "\n".join(f"{key}: {value}" for key, value in rule_version.project_facts.items())
    dimensions = rule_version.dimension_standards
    dimension_docs = dimensions.get("standards", dimensions) if isinstance(dimensions, dict) else {}
    return Standards(
        global_text="\n\n".join(str(value) for value in dimension_docs.values()),
        project_text=facts_text,
        dimension_docs=dict(dimension_docs),
        deny_words=[],
        recommended={},
        must_human_keywords=[],
        required_tags=[],
    )


def _audit_deterministic_issues(profile: Any, context: ReviewContext) -> list[StructuredIssue]:
    issues = evaluate_rules(profile, context)
    if context.content_type != "TECH_MEDIA_REVIEW":
        return issues
    rules_by_id = {rule.rule_id: rule for rule in profile.rules}
    return [
        issue for issue in issues
        if rules_by_id.get(issue.rule_id)
        and rules_by_id[issue.rule_id].matcher in TECH_MEDIA_INLINE_RULE_MATCHERS
    ]


def _system_issue(*, rule_id: str, reason: str, suggestion: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "category": "system",
        "severity": "HIGH",
        "field": "review",
        "evidence_quote": "",
        "evidence_start": None,
        "evidence_end": None,
        "evidence_asset_id": None,
        "evidence_timestamp": None,
        "reason": reason,
        "suggestion": suggestion,
        "source_reference": [f"SYSTEM:{rule_id}"],
        "auto_fixable": False,
        "human_required": True,
        "confidence": 0.99,
    }


def _validate_agent_protocol(results: list[dict], profile: Any) -> Optional[str]:
    ids = [result.get("agent_id") for result in results]
    if len(results) != len(AGENT_ORDER):
        return f"expected exactly {len(AGENT_ORDER)} Agent results, got {len(results)}"
    if ids != list(AGENT_ORDER):
        return "Agent results do not match the required fixed order"
    if len(set(ids)) != len(ids):
        return "Agent results contain duplicate agent_id values"
    known_references = set(profile.known_source_references)
    for expected_agent_id, result in zip(AGENT_ORDER, results):
        error = validate_agent_result(result, expected_agent_id, known_references)
        if error:
            return error
    return None


def _review_key(content_version_id: int, rule_version_id: int) -> str:
    return f"v1:{content_version_id}:{rule_version_id}"


def _is_unavailable_agent_result(result: AgentResult) -> bool:
    raw_issues = result.raw_result.get("issues") if isinstance(result.raw_result, dict) else None
    if isinstance(raw_issues, list) and raw_issues:
        raw_score = result.raw_result.get("score")
        return (
            result.score in (None, 0)
            and raw_score in (None, 0)
            and all(
                isinstance(issue, dict) and issue.get("rule_id") == "SYSTEM-LLM-UNAVAILABLE"
                for issue in raw_issues
            )
        )
    return (
        result.score is None
        and bool(result.issues)
        and all(issue.rule_id == "SYSTEM-LLM-UNAVAILABLE" for issue in result.issues)
    )


def _is_unavailable_only_audit(audit: AuditRun) -> bool:
    agent_results = list(audit.agent_results)
    return (
        audit.status == "COMPLETED"
        and [result.agent_id for result in agent_results] == list(AGENT_ORDER)
        and all(_is_unavailable_agent_result(result) for result in agent_results)
    )


def has_valid_completed_audit(
    session: Session,
    content_version_id: int,
    rule_version_id: int,
) -> bool:
    completed_audits = session.scalars(
        select(AuditRun).where(
            AuditRun.content_version_id == content_version_id,
            AuditRun.rule_version_id == rule_version_id,
            AuditRun.status == "COMPLETED",
        )
    )
    return any(not _is_unavailable_only_audit(audit) for audit in completed_audits)


def _supersede_unavailable_audit(audit: AuditRun) -> None:
    superseded_at = datetime.utcnow()
    audit.status = "SUPERSEDED"
    audit.review_key = None
    for task in audit.review_tasks:
        if task.status == "OPEN":
            task.status = "SUPERSEDED"
            task.closed_at = superseded_at


def _latest_version(item: ContentItem) -> ContentVersion:
    if not item.versions:
        raise ValueError(f"Content item {item.id} has no versions")
    return item.versions[-1]


def sanitize_version_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    for key in ("test_cases", "evidence", "evidence_assets"):
        sanitized.pop(key, None)
    return sanitized


def _new_version(item: ContentItem, *, source: str, title: str, body: str, payload: Mapping[str, Any]) -> ContentVersion:
    title, body = validate_content_format(title, body)
    version = ContentVersion(
        content_item=item,
        version=_latest_version(item).version + 1,
        source=source,
        title=title,
        body=body,
        payload=sanitize_version_payload(payload),
    )
    item.title = title
    return version


def _open_tasks(item: ContentItem) -> list[ReviewTask]:
    return [task for task in item.review_tasks if task.status == "OPEN"]


def _task_key(audit_run_id: int, target_content_version_id: int, task_type: str, issues: list[Issue]) -> str:
    issue_ids = sorted(issue.id for issue in issues if issue.id is not None)
    raw = json.dumps(
        [audit_run_id, target_content_version_id, task_type, issue_ids],
        separators=(",", ":"),
    )
    return "v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _create_or_reuse_task(
    session: Session,
    *,
    item: ContentItem,
    target: ContentVersion,
    audit: AuditRun,
    task_type: str,
    issues: list[Issue],
) -> ReviewTask:
    session.flush()
    task_key = _task_key(audit.id, target.id, task_type, issues)
    task = session.scalar(select(ReviewTask).where(ReviewTask.task_key == task_key))
    if task is None:
        legacy_issue_id = None
        for issue in issues:
            if issue.id is None:
                continue
            if session.scalar(select(ReviewTask).where(ReviewTask.issue_id == issue.id)) is None:
                legacy_issue_id = issue.id
                break
        task = ReviewTask(
            content_item=item,
            target_content_version=target,
            audit_run=audit,
            issue_id=legacy_issue_id,
            task_key=task_key,
            task_type=task_type,
        )
        session.add(task)
        session.flush()
    existing_issue_ids = {link.issue_id for link in task.issue_links}
    for issue in issues:
        if issue.id is None or issue.id in existing_issue_ids:
            continue
        session.add(ReviewTaskIssue(review_task=task, issue=issue))
    session.flush()
    return task


def _derive_state(item: ContentItem, *, clear_status: Optional[ReviewStatus] = None) -> None:
    if item.review_status is ReviewStatus.REJECTED:
        item.publish_status = PublishStatus.NOT_READY
        return
    open_tasks = _open_tasks(item)
    if open_tasks:
        item.publish_status = PublishStatus.NOT_READY
        task_statuses = {
            "BLOCK_REVIEW": ReviewStatus.BLOCKED,
            "HUMAN_REVIEW": ReviewStatus.HUMAN_REVIEW_REQUIRED,
            "SUPPLIER_REVISION": ReviewStatus.SUPPLIER_REVISION_REQUIRED,
            "AUTO_FIX_PROPOSAL": ReviewStatus.AUTO_FIX_PENDING,
        }
        item.review_status = next(
            (task_statuses[task_type] for task_type in task_statuses if any(task.task_type == task_type for task in open_tasks)),
            ReviewStatus.HUMAN_REVIEW_REQUIRED,
        )
        return
    if clear_status in {ReviewStatus.PASSED, ReviewStatus.PASSED_WITH_SUGGESTIONS}:
        item.review_status = clear_status
        item.publish_status = PublishStatus.READY
    elif clear_status is not None:
        item.review_status = clear_status
        item.publish_status = PublishStatus.NOT_READY
    else:
        item.review_status = ReviewStatus.HUMAN_REVIEW_REQUIRED
        item.publish_status = PublishStatus.NOT_READY


def _safe_proposal(
    content_version: ContentVersion,
    issues: list[Issue],
    safe_replacement_map: Mapping[str, Mapping[str, str]],
) -> Optional[tuple[str, str]]:
    title, body = content_version.title, content_version.body
    changed = False
    for issue in issues:
        if issue.agent_result_id is not None or issue.field not in {"title", "body"}:
            continue
        replacements = safe_replacement_map.get(issue.rule_id, {})
        replacement = replacements.get(issue.evidence_quote)
        if replacement is None:
            continue
        source = title if issue.field == "title" else body
        if issue.evidence_quote not in source:
            continue
        updated = source.replace(issue.evidence_quote, replacement)
        if updated != source:
            changed = True
            if issue.field == "title":
                title = updated
            else:
                body = updated
    return (title, body) if changed else None


def _normalize_agent_results(
    reviewer: Any,
    row: dict,
    standards: Standards,
    *,
    context: Optional[ReviewContext] = None,
    profile: Any = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> list[dict]:
    if isinstance(reviewer, TechMediaReviewer):
        if progress_callback is None:
            structured = reviewer.review_structured(context, profile)
        else:
            structured = reviewer.review_structured(
                context,
                profile,
                progress_callback=progress_callback,
            )
        return [_normalized_agent_result(result) for result in structured]
    if hasattr(reviewer, "review_structured"):
        return list(reviewer.review_structured(row, standards))
    verdict = reviewer.review(row, standards)
    issues = []
    for index, reason in enumerate(verdict.issues):
        issues.append({
            "rule_id": f"LEGACY-{index + 1}",
            "category": verdict.categories[index] if index < len(verdict.categories) else "unknown",
            "severity": verdict.risk_level,
            "field": "body",
            "evidence_quote": "",
            "reason": reason,
            "suggestion": verdict.suggestion,
            "auto_fixable": verdict.risk_level == schema.RISK_LOW,
            "human_required": verdict.risk_level in MANUAL_SEVERITIES,
            "confidence": verdict.confidence,
        })
    return [{"agent_name": "legacy", "status": "COMPLETED", "issues": issues, "raw_result": {}}]


def _profile_for_item(item: ContentItem, rule_version: RuleVersion) -> Any:
    profile = get_review_profile(rule_version)
    review_brief = item.batch.review_brief or item.project.description
    if review_brief:
        profile = profile.model_copy(
            update={
                "project_facts": {
                    **dict(profile.project_facts),
                    "review_brief": review_brief,
                    "batch_review_brief": item.batch.review_brief or "",
                }
            }
        )
    return profile


def _review_context_for_item(
    session: Session,
    item: ContentItem,
    content_version: ContentVersion,
) -> ReviewContext:
    database_test_cases = list_content_test_cases(
        session, item.id, content_version_id=content_version.id
    )
    if item.project.content_type == "TECH_MEDIA_REVIEW" or database_test_cases:
        context_test_cases = database_test_cases
        context_evidence_assets = [
            asset for test_case in database_test_cases for asset in test_case["evidence_assets"]
        ]
        context_evidence = [
            {"test_case_id": test_case["test_case_id"], "asset_id": asset["asset_id"]}
            for test_case in database_test_cases for asset in test_case["evidence_assets"]
        ]
    else:
        context_test_cases = list(content_version.payload.get("test_cases", []))
        context_evidence = list(content_version.payload.get("evidence", []))
        context_evidence_assets = list(content_version.payload.get("evidence_assets", []))
    return ReviewContext(
        title=content_version.title,
        body=content_version.body,
        platform=str(content_version.payload.get("platform", "")),
        content_type=item.project.content_type or "",
        project_id=str(item.project_id),
        project_code=item.project.code or "",
        test_cases=context_test_cases,
        evidence=context_evidence,
        evidence_assets=context_evidence_assets,
    )


def _batch_audit_candidate(
    session: Session,
    item: ContentItem,
) -> tuple[ContentVersion, RuleVersion, Any, ReviewContext]:
    if item.review_status is ReviewStatus.REJECTED:
        raise ValueError("Rejected content is terminal")
    if item.format_status is not FormatStatus.PASSED:
        raise ValueError("Only content with PASSED format status can be audited")
    rule_version = item.project.current_rule_version
    if rule_version is None:
        raise ValueError("Project has no current rule version")
    validate_rule_version_identity(item.project, rule_version)
    content_version = _latest_version(item)
    matching_audits = list(session.scalars(
        select(AuditRun)
        .where(
            AuditRun.content_version_id == content_version.id,
            AuditRun.rule_version_id == rule_version.id,
            AuditRun.status.in_(("RUNNING", "COMPLETED")),
        )
        .order_by(AuditRun.id.desc())
    ))
    if any(
        audit.status == "COMPLETED" and not _is_unavailable_only_audit(audit)
        for audit in matching_audits
    ):
        raise ValueError("Content version has already been audited with this rule version")
    if any(audit.status == "RUNNING" for audit in matching_audits):
        raise ValueError("Content version has already been audited with this rule version")
    historical_audits = list(session.scalars(
        select(AuditRun)
        .where(
            AuditRun.content_version_id == content_version.id,
            AuditRun.status == "COMPLETED",
        )
        .order_by(AuditRun.id.desc())
    ))
    unavailable_audits = [audit for audit in historical_audits if _is_unavailable_only_audit(audit)]
    open_tasks = _open_tasks(item)
    if open_tasks and not unavailable_audits:
        raise ValueError("Content has open review tasks; resolve them before re-audit")
    profile = _profile_for_item(item, rule_version)
    context = _review_context_for_item(session, item, content_version)
    return content_version, rule_version, profile, context


def run_batch_audit_once(
    session: Session,
    batch: Batch,
    *,
    reviewer: Any,
    model: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> Optional[tuple[list[AuditRun], list[tuple[int, str]]]]:
    if (
        not isinstance(reviewer, TechMediaReviewer)
        or reviewer.llm is None
        or not callable(getattr(reviewer, "review_manuscript_batch_structured", None))
    ):
        return None
    if any(item.project.content_type != "TECH_MEDIA_REVIEW" for item in batch.content_items):
        return None
    candidates = []
    errors: list[tuple[int, str]] = []
    shared_profile = None
    for item in batch.content_items:
        try:
            content_version, _rule_version, profile, context = _batch_audit_candidate(session, item)
        except ValueError as error:
            errors.append((item.id, str(error)))
            continue
        if shared_profile is None:
            shared_profile = profile
        candidates.append({
            "content_item_id": item.id,
            "external_id": item.external_id,
            "context": context,
            "content_version_id": content_version.id,
        })
    if not candidates:
        return [], errors

    def review_candidate_group(group: list[dict[str, Any]]) -> dict[int, list[Any]]:
        results = reviewer.review_manuscript_batch_structured(group, shared_profile)
        if len(group) > 2 and all(
            _agent_review_results_are_unavailable(results.get(int(candidate["content_item_id"]), []))
            for candidate in group
        ):
            split_results: dict[int, list[Any]] = {}
            for start in range(0, len(group), 2):
                split_results.update(review_candidate_group(group[start:start + 2]))
            return split_results
        return results

    batch_results = review_candidate_group(candidates)
    if candidates and all(
        _agent_review_results_are_unavailable(batch_results.get(int(candidate["content_item_id"]), []))
        for candidate in candidates
    ):
        return None
    audits: list[AuditRun] = []
    for candidate in candidates:
        content_id = int(candidate["content_item_id"])
        results = batch_results.get(content_id)
        if not results:
            errors.append((content_id, "Batch model response did not include this content"))
            continue
        try:
            audit = run_audit(
                session,
                content_id,
                reviewer=_PrecomputedReviewer(results),
                model=model,
                created_by_user_id=created_by_user_id,
            )
        except ValueError as error:
            session.rollback()
            errors.append((content_id, str(error)))
        else:
            audits.append(audit)
    return audits, errors


def run_audit(
    session: Session,
    content_item_id: int,
    *,
    reviewer: Any = None,
    model: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> AuditRun:
    item = session.get(ContentItem, content_item_id)
    if item is None:
        raise ValueError(f"Content item {content_item_id} does not exist")
    if item.review_status is ReviewStatus.REJECTED:
        raise ValueError("Rejected content is terminal")
    if item.format_status is not FormatStatus.PASSED:
        raise ValueError("Only content with PASSED format status can be audited")

    rule_version = item.project.current_rule_version
    if rule_version is None:
        raise ValueError("Project has no current rule version")
    validate_rule_version_identity(item.project, rule_version)
    content_version = _latest_version(item)
    review_key = None
    if item.project.content_type == "TECH_MEDIA_REVIEW":
        review_key = _review_key(content_version.id, rule_version.id)
    matching_audits = []
    if review_key is not None:
        matching_audits = list(session.scalars(
            select(AuditRun)
            .where(
                AuditRun.content_version_id == content_version.id,
                AuditRun.rule_version_id == rule_version.id,
                AuditRun.status.in_(("RUNNING", "COMPLETED")),
            )
            .order_by(AuditRun.id.desc())
        ))
    if any(
        audit.status == "COMPLETED" and not _is_unavailable_only_audit(audit)
        for audit in matching_audits
    ):
        raise ValueError("Content version has already been audited with this rule version")
    running_audit = next((audit for audit in matching_audits if audit.status == "RUNNING"), None)
    if running_audit is not None:
        raise ValueError("Content version has already been audited with this rule version")
    historical_audits = list(session.scalars(
        select(AuditRun)
        .where(
            AuditRun.content_version_id == content_version.id,
            AuditRun.status == "COMPLETED",
        )
        .order_by(AuditRun.id.desc())
    ))
    unavailable_audits = [audit for audit in historical_audits if _is_unavailable_only_audit(audit)]
    if unavailable_audits:
        for unavailable_audit in unavailable_audits:
            _supersede_unavailable_audit(unavailable_audit)
        session.flush()
    if _open_tasks(item):
        raise ValueError("Content has open review tasks; resolve them before re-audit")
    standards = _standards_from_rule_version(rule_version)
    profile = get_review_profile(rule_version)
    review_brief = item.batch.review_brief or item.project.description
    if review_brief:
        profile = profile.model_copy(
            update={
                "project_facts": {
                    **dict(profile.project_facts),
                    "review_brief": review_brief,
                    "batch_review_brief": item.batch.review_brief or "",
                }
            }
        )
    reviewer = reviewer or TechMediaReviewer()
    row = {
        schema.COL_ID: item.external_id,
        schema.COL_TITLE: content_version.title,
        schema.COL_BODY: content_version.body,
        **content_version.payload,
    }
    audit = AuditRun(
        content_item=item,
        content_version=content_version,
        rule_version=rule_version,
        review_key=review_key,
        model=model or getattr(reviewer, "name", reviewer.__class__.__name__),
        prompt_version=rule_version.prompt_version,
        status="RUNNING",
        created_by_user_id=created_by_user_id,
    )
    item.review_status = ReviewStatus.AI_REVIEWING
    item.publish_status = PublishStatus.NOT_READY
    session.add(audit)
    session.flush()

    database_test_cases = list_content_test_cases(
        session, item.id, content_version_id=content_version.id
    )
    if item.project.content_type == "TECH_MEDIA_REVIEW":
        context_test_cases = database_test_cases
        context_evidence_assets = [
            asset for test_case in database_test_cases for asset in test_case["evidence_assets"]
        ]
        context_evidence = [
            {"test_case_id": test_case["test_case_id"], "asset_id": asset["asset_id"]}
            for test_case in database_test_cases for asset in test_case["evidence_assets"]
        ]
    elif database_test_cases:
        context_test_cases = database_test_cases
        context_evidence_assets = [
            asset for test_case in database_test_cases for asset in test_case["evidence_assets"]
        ]
        context_evidence = [
            {"test_case_id": test_case["test_case_id"], "asset_id": asset["asset_id"]}
            for test_case in database_test_cases for asset in test_case["evidence_assets"]
        ]
    else:
        context_test_cases = list(content_version.payload.get("test_cases", []))
        context_evidence = list(content_version.payload.get("evidence", []))
        context_evidence_assets = list(content_version.payload.get("evidence_assets", []))
    context = ReviewContext(
        title=content_version.title,
        body=content_version.body,
        platform=str(content_version.payload.get("platform", "")),
        content_type=item.project.content_type or "",
        project_id=str(item.project_id),
        project_code=item.project.code or "",
        test_cases=context_test_cases,
        evidence=context_evidence,
        evidence_assets=context_evidence_assets,
    )
    persisted_issues: list[Issue] = []
    for deterministic in _audit_deterministic_issues(profile, context):
        persisted = Issue(
            audit_run=audit,
            rule_id=deterministic.rule_id,
            category=_normalize_issue_category(
                deterministic.category,
                rule_id=deterministic.rule_id,
                reason=deterministic.reason,
                suggestion=deterministic.suggestion,
                field=deterministic.field,
                evidence_quote=deterministic.evidence,
            ),
            severity=deterministic.severity,
            field=deterministic.field,
            evidence_quote=deterministic.evidence,
            source_reference=deterministic.source_reference,
            reason=deterministic.reason,
            suggestion=deterministic.suggestion,
            auto_fixable=deterministic.auto_fixable,
            human_required=deterministic.human_required,
            confidence=deterministic.confidence,
        )
        session.add(persisted)
        persisted_issues.append(persisted)
    agent_result_data = _normalize_agent_results(
        reviewer,
        row,
        standards,
        context=context,
        profile=profile,
        progress_callback=progress_callback,
    )
    agent_result_data = _simplify_fast_review_agent_results(agent_result_data)
    strict_protocol = item.project.content_type == "TECH_MEDIA_REVIEW"
    protocol_error = _validate_agent_protocol(agent_result_data, profile) if strict_protocol else None
    role_boundary_agents = {
        result.get("agent_id") for result in agent_result_data if role_boundary_error(result)
    } if strict_protocol else set()
    agent_results_have_real = any(not _is_system_unavailable_result(result) for result in agent_result_data)
    persisted_agent_keys: set[tuple[Any, Any, Any]] = set()
    for result_data in agent_result_data:
        persistence_key = (result_data.get("agent_name"),)
        if persistence_key in persisted_agent_keys:
            continue
        persisted_agent_keys.add(persistence_key)
        result = AgentResult(
            audit_run=audit,
            agent_name=result_data["agent_name"],
            agent_id=result_data.get("agent_id"),
            agent_version=result_data.get("agent_version"),
            decision=result_data.get("decision"),
            summary=result_data.get("summary"),
            score=result_data.get("score"),
            status=result_data.get("status", "COMPLETED"),
            raw_result=dict(result_data.get("raw_result", result_data)),
        )
        session.add(result)
        session.flush()
        if result_data.get("agent_id") in role_boundary_agents:
            continue
        for issue_data in result_data.get("issues", []):
            issue_data = {
                "source_reference": [],
                "evidence_start": None,
                "evidence_end": None,
                "evidence_asset_id": None,
                "evidence_timestamp": None,
                **issue_data,
            }
            missing = [field for field in ISSUE_FIELDS if field not in issue_data]
            if missing:
                raise ValueError(f"Structured issue missing fields: {', '.join(missing)}")
            if _is_fast_review_ignored_issue(issue_data) and (
                agent_results_have_real or not _is_system_unavailable_issue(issue_data)
            ):
                continue
            issue_data["category"] = _normalize_issue_category(
                issue_data.get("category"),
                rule_id=issue_data.get("rule_id"),
                reason=issue_data.get("reason"),
                suggestion=issue_data.get("suggestion"),
                field=issue_data.get("field"),
                evidence_quote=issue_data.get("evidence_quote"),
            )
            persisted = Issue(
                audit_run=audit,
                agent_result=result,
                **{field: issue_data[field] for field in ISSUE_FIELDS},
            )
            session.add(persisted)
            persisted_issues.append(persisted)
    if protocol_error:
        issue_data = _system_issue(
            rule_id=(
                "SYSTEM-AGENT-DECISION"
                if "blocking decision" in protocol_error or "PASS with issues" in protocol_error
                else "SYSTEM-AGENT-PROTOCOL"
            ),
            reason=protocol_error,
            suggestion="Route this audit to human review and regenerate the complete six-agent result set.",
        )
        persisted = Issue(audit_run=audit, **issue_data)
        session.add(persisted)
        persisted_issues.append(persisted)
    elif strict_protocol:
        for result_data in agent_result_data:
            decision = result_data.get("decision")
            if decision in {"HUMAN_REVIEW", "BLOCK", "NEED_TEXT_FIX"} and not result_data.get("issues"):
                issue_data = _system_issue(
                    rule_id="SYSTEM-AGENT-DECISION",
                    reason=f"Agent {result_data.get('agent_id')} returned {decision} without a blocking issue",
                    suggestion="Route this audit to human review and correct the Agent result.",
                )
                persisted = Issue(audit_run=audit, **issue_data)
                session.add(persisted)
                persisted_issues.append(persisted)
    session.flush()

    campaign_result = next(
        (result for result in agent_result_data if result.get("agent_id") == "CAMPAIGN_EFFECTIVENESS"), None
    )
    arbitration = arbitrate_review(
        [
            {
                "agent_id": result.get("agent_id"),
                "agent_name": result.get("agent_name"),
                "decision": (
                    "PASS_WITH_SUGGESTIONS"
                    if result.get("agent_id") in role_boundary_agents
                    else result.get("decision")
                ),
            }
            for result in agent_result_data
        ],
        persisted_issues,
        campaign_score=campaign_result.get("score") if campaign_result else None,
        suggestions=[
            issue.suggestion for issue in persisted_issues
            if issue.severity.upper() == "LOW" and issue.suggestion
        ],
        safe_auto_fix_rule_ids=set(profile.safe_replacement_map),
    )

    if arbitration.ai_proposal_allowed:
        proposed_text = _safe_proposal(content_version, persisted_issues, profile.safe_replacement_map)
        if proposed_text is None:
            arbitration = ArbitrationResult(
                ReviewStatus.HUMAN_REVIEW_REQUIRED,
                PublishStatus.NOT_READY,
                (ReviewTaskSpec("HUMAN_REVIEW"),),
                reason="allowlisted issue could not be applied safely",
            )
        else:
            proposed = _new_version(
                item,
                source="AI_PROPOSED",
                title=proposed_text[0],
                body=proposed_text[1],
                payload=content_version.payload,
            )
            session.add(proposed)
            session.flush()
            _create_or_reuse_task(
                session, item=item, target=proposed, audit=audit,
                task_type="AUTO_FIX_PROPOSAL", issues=persisted_issues,
            )

    if not arbitration.ai_proposal_allowed:
        task_issues: dict[str, list[Issue]] = {}
        for issue in persisted_issues:
            task_issues.setdefault(issue.rule_id, []).append(issue)
        for task_spec in arbitration.task_specs:
            applicable = [
                issue for key in task_spec.issue_keys for issue in task_issues.get(key, [])
            ]
            if not applicable and persisted_issues:
                applicable = list(persisted_issues)
            _create_or_reuse_task(
                session, item=item, target=content_version, audit=audit,
                task_type=task_spec.task_type, issues=applicable,
            )
    session.flush()
    item.review_status = arbitration.review_status
    item.publish_status = (
        arbitration.publish_status
        if arbitration.review_status in {ReviewStatus.PASSED, ReviewStatus.PASSED_WITH_SUGGESTIONS}
        and not _open_tasks(item)
        else PublishStatus.NOT_READY
    )

    audit.status = "COMPLETED"
    audit.completed_at = datetime.utcnow()
    session.commit()
    session.refresh(audit)
    return audit


def resolve_task(
    session: Session,
    review_task_id: int,
    *,
    decision: str,
    reviewer: str,
    reviewer_user_id: Optional[int] = None,
    note: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> HumanDecision:
    task = session.get(ReviewTask, review_task_id)
    if task is None:
        raise ValueError(f"Review task {review_task_id} does not exist")
    if task.status != "OPEN":
        raise ValueError("Review task is already closed")
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    item = task.content_item
    if item.review_status is ReviewStatus.REJECTED:
        raise ValueError("Rejected content is terminal")

    payload = dict(payload or {})
    target = task.target_content_version
    allowed = {
        "AUTO_FIX_PROPOSAL": {
            "ACCEPT_AUTO_FIX", "EDIT_AUTO_FIX", "REJECT_AUTO_FIX",
            "ACCEPT_SUGGESTION", "ACCEPT_EDITED", "REJECT_SUGGESTION",
        },
        "HUMAN_REVIEW": {"HUMAN_APPROVE", "HUMAN_REJECT", "APPROVE_RISK", "REJECT_RISK"},
        "BLOCK_REVIEW": {"HUMAN_APPROVE", "HUMAN_REJECT", "APPROVE_RISK", "REJECT_RISK"},
        "SUPPLIER_REVISION": {"SUPPLIER_REVISION_SUBMITTED"},
    }
    if decision not in allowed.get(task.task_type, set()):
        raise ValueError(f"Decision {decision} is invalid for {task.task_type}")

    clear_status: Optional[ReviewStatus] = None
    if decision in {"ACCEPT_AUTO_FIX", "ACCEPT_SUGGESTION"}:
        if target.source != "AI_PROPOSED":
            raise ValueError("Task target is not an AI proposed version")
        session.add(_new_version(
            item, source="HUMAN_CONFIRMED", title=target.title, body=target.body, payload=target.payload
        ))
        clear_status = ReviewStatus.PASSED
    elif decision in {"EDIT_AUTO_FIX", "ACCEPT_EDITED"}:
        title, body = validate_content_format(payload.get("title"), payload.get("body"))
        session.add(_new_version(
            item, source="HUMAN_EDITED", title=title, body=body, payload=target.payload
        ))
        clear_status = ReviewStatus.PASSED
    elif decision in {"REJECT_AUTO_FIX", "REJECT_SUGGESTION"}:
        _create_or_reuse_task(
            session, item=item, target=task.audit_run.content_version,
            audit=task.audit_run, task_type="SUPPLIER_REVISION", issues=list(task.issues),
        )
    elif decision in {"HUMAN_APPROVE", "APPROVE_RISK"}:
        if "title" in payload or "body" in payload:
            title = payload.get("title", target.title)
            body = payload.get("body", target.body)
            title, body = validate_content_format(title, body)
            session.add(_new_version(
                item, source="HUMAN_APPROVED", title=title, body=body, payload=target.payload
            ))
        clear_status = ReviewStatus.PASSED
    elif decision in {"HUMAN_REJECT", "REJECT_RISK"}:
        item.review_status = ReviewStatus.REJECTED
        item.publish_status = PublishStatus.NOT_READY
        for candidate in _open_tasks(item):
            if candidate.id != task.id:
                candidate.status = "SUPERSEDED"
                candidate.closed_at = datetime.utcnow()
    elif decision == "SUPPLIER_REVISION_SUBMITTED":
        title, body = validate_content_format(payload.get("title"), payload.get("body"))
        session.add(_new_version(
            item, source="SUPPLIER_REVISION", title=title, body=body, payload=target.payload
        ))
        clear_status = ReviewStatus.NOT_STARTED

    task.status = "CLOSED"
    task.closed_at = datetime.utcnow()
    human_decision = HumanDecision(
        review_task=task,
        decision=decision,
        reviewer=reviewer.strip(),
        reviewer_user_id=reviewer_user_id,
        note=note,
        payload=payload,
    )
    session.add(human_decision)
    session.flush()
    if item.review_status is not ReviewStatus.REJECTED:
        _derive_state(item, clear_status=clear_status)
    session.commit()
    session.refresh(human_decision)
    return human_decision
