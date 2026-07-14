from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from server.services.review_profile_service import ReviewProfile, RuleSpec


class ReviewContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = ""
    body: str = ""
    platform: str = ""
    content_type: str = "TECH_MEDIA_REVIEW"
    project_id: str = ""
    project_code: str = ""
    test_cases: list[Mapping[str, Any]] = Field(default_factory=list)
    evidence: list[Mapping[str, Any]] = Field(default_factory=list)
    evidence_assets: list[Mapping[str, Any]] = Field(default_factory=list)

    def field_value(self, field: str) -> str:
        value = getattr(self, field, "")
        if isinstance(value, str):
            return value
        return ""


class StructuredIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    category: str = "deterministic"
    severity: str
    field: str
    evidence: str = ""
    reason: str
    suggestion: str
    action: str
    source_reference: list[str] = Field(default_factory=list)
    auto_fixable: bool = False
    human_required: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)

    @property
    def evidence_quote(self) -> str:
        return self.evidence


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, (list, tuple, set)) else [str(value)]


def _scoped(rule: RuleSpec, context: ReviewContext, field: str) -> bool:
    scope = rule.scope
    def matches(key: str, actual: str) -> bool:
        expected = scope.get(key, "ALL")
        if expected in (None, "ALL", [], ""):
            return True
        return actual in _values(expected)
    content_scope = scope.get("content_types", scope.get("content_type", "ALL"))
    if content_scope not in (None, "ALL", [], "") and context.content_type not in _values(content_scope):
        return False
    if not matches("project_ids", context.project_id) or not matches("project_codes", context.project_code):
        return False
    if not matches("platforms", context.platform):
        return False
    fields = scope.get("fields", scope.get("field", "ALL"))
    return fields in (None, "ALL", []) or field in _values(fields)


def _issue(rule: RuleSpec, *, field: str, evidence: str, reason: str, suggestion: str = "") -> StructuredIssue:
    return StructuredIssue(
        rule_id=rule.rule_id,
        severity=rule.severity,
        field=field,
        evidence=evidence,
        reason=reason,
        suggestion=suggestion,
        action=rule.action,
        source_reference=list(rule.source_reference),
        auto_fixable=rule.auto_fixable,
        human_required=rule.action == "HUMAN_REVIEW" or rule.severity.upper() in {"HIGH", "CRITICAL"},
    )


def _text_fields(rule: RuleSpec, context: ReviewContext) -> list[tuple[str, str]]:
    fields = rule.scope.get("fields", rule.scope.get("field", "body"))
    if fields in (None, "ALL"):
        fields = ["title", "body"]
    return [(field, context.field_value(field)) for field in _values(fields) if context.field_value(field)]


def _match_text(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    phrases = _values(rule.model_extra.get("phrases", []))
    replacement_map = rule.model_extra.get("replacement_map", {})
    if rule.matcher == "replacement_map":
        phrases = list(replacement_map)
    issues = []
    for field, text in _text_fields(rule, context):
        for phrase in phrases:
            if phrase in text:
                replacement = replacement_map.get(phrase, "")
                issues.append(_issue(rule, field=field, evidence=phrase, reason=f"命中规则短语：{phrase}", suggestion=replacement))
    return issues


def _count_issue(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    title_pattern = rule.model_extra.get("title_pattern", r"(\d+)\s*[个项]?测试")
    match = re.search(title_pattern, context.title)
    if not match:
        return []
    declared = int(match.group(1))
    actual = len(context.test_cases)
    if not actual:
        actual = len(re.findall(r"(?m)^\s*\d+[\.、)]\s+", context.body))
    if actual == declared:
        return []
    return [_issue(rule, field="title", evidence=match.group(0), reason=f"标题声明 {declared} 个测试，但结构化测试场景/编号正文为 {actual} 个", suggestion=f"将测试数量改为 {actual}")]


def _evidence_issue(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    terms = _values(rule.model_extra.get("trigger_terms", []))
    text = f"{context.title}\n{context.body}"
    trigger = next((term for term in terms if term in text), None)
    if not trigger:
        return []
    required = _values(rule.model_extra.get("required_fields", []))
    missing = []
    for field in required:
        value = getattr(context, field, None)
        if not value:
            missing.append(field)
    if not missing:
        return []
    return [_issue(rule, field="body", evidence=trigger, reason=f"出现实测触发词，但缺少结构化证据字段：{', '.join(missing)}", suggestion="补充可追溯的测试场景和证据")] 


def _required_term_issue(rule: RuleSpec, context: ReviewContext, profile: ReviewProfile) -> list[StructuredIssue]:
    if context.platform and context.platform in profile.platform_requirements:
        config = profile.platform_requirements[context.platform]
        if str(config.get("status", "")).upper() in {"PENDING", "", "INACTIVE"}:
            return []
    terms = _values(rule.model_extra.get("required_terms", []))
    for field, text in _text_fields(rule, context):
        missing = next((term for term in terms if term not in text), None)
        if missing:
            return [_issue(rule, field=field, evidence="", reason=f"缺少平台要求词：{missing}", suggestion=f"补充 {missing}")]
    return []


def evaluate_rules(profile: ReviewProfile, context: ReviewContext) -> list[StructuredIssue]:
    issues: list[StructuredIssue] = []
    for rule in profile.rules:
        if rule.matcher not in {"exact_phrase", "phrase_list", "replacement_map", "count_consistency", "evidence_required", "required_term"}:
            raise ValueError(f"unsupported matcher: {rule.matcher}")
        field = "body" if rule.matcher not in {"count_consistency", "required_term"} else rule.scope.get("field", "body")
        if not _scoped(rule, context, field if isinstance(field, str) else "body"):
            continue
        if rule.matcher in {"exact_phrase", "phrase_list", "replacement_map"}:
            issues.extend(_match_text(rule, context))
        elif rule.matcher == "count_consistency":
            issues.extend(_count_issue(rule, context))
        elif rule.matcher == "evidence_required":
            issues.extend(_evidence_issue(rule, context))
        else:
            issues.extend(_required_term_issue(rule, context, profile))
    return issues
