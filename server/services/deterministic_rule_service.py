from __future__ import annotations

import re
from typing import Any, Mapping

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


def _configured_fields(rule: RuleSpec, default: str = "body") -> list[str]:
    fields = rule.scope.get("fields", rule.scope.get("field", default))
    if fields in (None, "ALL", []):
        return ["title", "body"]
    return _values(fields)


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
    return [(field, context.field_value(field)) for field in _configured_fields(rule) if context.field_value(field)]


def _match_text(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    phrases = _values(rule.model_extra.get("phrases", []))
    replacement_map = rule.model_extra.get("replacement_map", {})
    if rule.matcher == "replacement_map":
        phrases = list(replacement_map)
    issues = []
    for field, text in _text_fields(rule, context):
        if not _scoped(rule, context, field):
            continue
        for phrase in phrases:
            if phrase in text:
                replacement = replacement_map.get(phrase, "")
                issues.append(_issue(rule, field=field, evidence=phrase, reason=f"命中规则短语：{phrase}", suggestion=replacement))
    return issues


def _segments(text: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"[。！？!?；;\n]+", text) if segment.strip()]


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term and term in text for term in terms)


def _is_guarded_context(segment: str, rule: RuleSpec) -> bool:
    return _contains_any(segment, _values(rule.model_extra.get("negation_terms", []))) or _contains_any(
        segment, _values(rule.model_extra.get("criticism_terms", []))
    )


def _is_bound_observation(context: ReviewContext, segment: str) -> bool:
    for record in _valid_test_cases(context):
        observed = str(record.get("observed_result", record.get("result", ""))).strip()
        if observed and (observed in segment or segment in observed):
            return True
    return False


def _guarded_claim_issues(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    phrases = _values(rule.model_extra.get("phrases", []))
    subjects = _values(rule.model_extra.get("subject_terms", []))
    quantifiers = _values(rule.model_extra.get("quantifier_terms", []))
    capabilities = _values(rule.model_extra.get("capability_terms", []))
    percentage_contexts = _values(rule.model_extra.get("percentage_context_terms", []))
    issues = []
    for field, text in _text_fields(rule, context):
        if not _scoped(rule, context, field):
            continue
        for segment in _segments(text):
            if _is_guarded_context(segment, rule) or _is_bound_observation(context, segment):
                continue
            direct = next((phrase for phrase in phrases if phrase in segment), "")
            composition = (
                _contains_any(segment, subjects)
                and _contains_any(segment, quantifiers)
                and _contains_any(segment, capabilities)
            )
            percentage = bool(re.search(r"(?<!\d)100\s*%", segment)) and _contains_any(segment, percentage_contexts)
            if direct or composition or percentage:
                evidence = direct or segment
                issues.append(_issue(
                    rule, field=field, evidence=evidence,
                    reason="命中缺少所提供依据的绝对、保证或能力比较表述",
                    suggestion="改为有边界的观察或补充可追溯依据",
                ))
    return issues


def _hotel_capability_issues(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    hotel_terms = _values(rule.model_extra.get("hotel_terms", []))
    capability_terms = _values(rule.model_extra.get("capability_terms", []))
    issues = []
    for field, text in _text_fields(rule, context):
        if not _scoped(rule, context, field):
            continue
        for segment in _segments(text):
            if _is_guarded_context(segment, rule):
                continue
            if _contains_any(segment, hotel_terms) and _contains_any(segment, capability_terms):
                issues.append(_issue(
                    rule, field=field, evidence=segment,
                    reason="命中待确认的酒店预订、自动筛选、比较或价值排序能力",
                    suggestion="保留为待确认产品能力并转人工核验",
                ))
    return issues


def _record_identifier(record: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _test_case_id(record: Mapping[str, Any]) -> str:
    return _record_identifier(record, ("test_case_id", "external_test_case_id", "id"))


def _evidence_asset_id(record: Mapping[str, Any]) -> str:
    return _record_identifier(record, ("asset_id", "external_id", "id"))


def _evidence_manifest_ids(context: ReviewContext) -> set[str]:
    return {
        asset_id for record in list(context.evidence) + list(context.evidence_assets)
        if (asset_id := _evidence_asset_id(record))
    }


def _evidence_bindings(context: ReviewContext) -> set[tuple[str, str]]:
    return {
        (_test_case_id(record), _evidence_asset_id(record))
        for record in context.evidence
        if _test_case_id(record) and _evidence_asset_id(record)
    }


def _valid_test_cases(context: ReviewContext) -> list[Mapping[str, Any]]:
    manifest_ids = _evidence_manifest_ids(context)
    bindings = _evidence_bindings(context)
    valid = []
    for record in context.test_cases:
        test_case_id = _test_case_id(record)
        claim_value = record.get("claim", "")
        command_value = record.get("command", "")
        observed_value = record.get("observed_result", record.get("result", ""))
        claim = claim_value.strip() if isinstance(claim_value, str) else ""
        command = command_value.strip() if isinstance(command_value, str) else ""
        observed = observed_value.strip() if isinstance(observed_value, str) else ""
        references = {
            str(identifier).strip()
            for identifier in record.get("evidence_asset_ids", []) or []
            if str(identifier).strip()
        }
        if (
            test_case_id and claim and command and observed and references
            and references <= manifest_ids
            and all((test_case_id, asset_id) in bindings for asset_id in references)
        ):
            valid.append(record)
    return valid


def _valid_evidence(context: ReviewContext) -> list[Mapping[str, Any]]:
    records = list(context.evidence) + list(context.evidence_assets)
    return [
        record for record in records
        if any(str(record.get(key, "")).strip() for key in ("asset_id", "id", "filename", "path"))
        or any(str(identifier).strip() for identifier in record.get("evidence_asset_ids", []) or [])
    ]


def _count_issue(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    fields = _configured_fields(rule)
    title_pattern = rule.model_extra.get("title_pattern", r"(\d+)\s*[个项]?测试")
    match = re.search(title_pattern, context.title) if "title" in fields else None
    if not match:
        return []
    declared = int(match.group(1))
    actual = len(_valid_test_cases(context))
    if not actual and "body" in fields:
        actual = len(re.findall(r"(?m)^\s*\d+[\.、)]\s+", context.body))
    if actual == declared:
        return []
    return [_issue(rule, field="title", evidence=match.group(0), reason=f"标题声明 {declared} 个测试，但结构化测试场景/编号正文为 {actual} 个", suggestion=f"将测试数量改为 {actual}")]


def _evidence_issue(rule: RuleSpec, context: ReviewContext) -> list[StructuredIssue]:
    terms = _values(rule.model_extra.get("trigger_terms", []))
    fields = _configured_fields(rule)
    text = "\n".join(context.field_value(field) for field in fields)
    trigger = next((term for term in terms if term in text), None)
    if not trigger:
        return []
    required = _values(rule.model_extra.get("required_fields", []))
    missing = []
    valid_test_cases = _valid_test_cases(context)
    for field in required:
        if field == "test_cases":
            value = valid_test_cases
        elif field in {"evidence", "evidence_assets"}:
            value = _valid_evidence(context)
        else:
            value = getattr(context, field, None)
        if not value:
            missing.append(field)
    required_test_case_fields = _values(rule.model_extra.get("required_test_case_fields", []))
    for field in required_test_case_fields:
        if context.test_cases and any(
            not isinstance(record.get(field), (str, list, tuple, set))
            or not record.get(field)
            or (isinstance(record.get(field), str) and not record.get(field).strip())
            for record in context.test_cases
        ):
            missing.append(f"test_cases.{field}")
    if not missing:
        return []
    return [_issue(
        rule,
        field="body",
        evidence=trigger,
        reason=f"出现实测触发词，但缺少结构化证据字段或测试条件：{', '.join(missing)}",
        suggestion="补充与当前内容版本绑定的测试场景、测试条件、适用边界和证据",
    )]


def _required_term_issue(rule: RuleSpec, context: ReviewContext, profile: ReviewProfile) -> list[StructuredIssue]:
    if not context.platform:
        return []
    if context.platform in profile.platform_requirements:
        config = profile.platform_requirements[context.platform]
        if str(config.get("status", "")).upper() in {"PENDING", "", "INACTIVE"}:
            return []
    terms = _values(rule.model_extra.get("required_terms", []))
    for field, text in _text_fields(rule, context):
        missing = next((term for term in terms if term not in text), None)
        if missing:
            return [_issue(rule, field=field, evidence="", reason=f"缺少平台要求词：{missing}", suggestion=f"补充 {missing}")]
    return []


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalize_platform(profile: ReviewProfile, platform: str) -> str:
    value = platform.strip()
    if not value:
        return ""
    canonical = profile.platform_aliases.get(value)
    if canonical:
        return canonical
    if value in profile.platform_requirements:
        return value
    return ""


def evaluate_rules(profile: ReviewProfile, context: ReviewContext) -> list[StructuredIssue]:
    context = context.model_copy(update={"platform": _normalize_platform(profile, context.platform)})
    issues: list[StructuredIssue] = []
    supported = {
        "exact_phrase", "phrase_list", "replacement_map", "count_consistency",
        "evidence_required", "required_term", "guarded_claim", "hotel_capability",
    }
    for rule in profile.rules:
        if rule.matcher not in supported:
            raise ValueError(f"unsupported matcher: {rule.matcher}")
        if rule.matcher in {"exact_phrase", "phrase_list", "replacement_map"}:
            issues.extend(_match_text(rule, context))
        elif rule.matcher == "guarded_claim":
            issues.extend(_guarded_claim_issues(rule, context))
        elif rule.matcher == "hotel_capability":
            issues.extend(_hotel_capability_issues(rule, context))
        elif not _scoped(rule, context, "title" if "title" in _configured_fields(rule) else "body"):
            continue
        elif rule.matcher == "count_consistency":
            issues.extend(_count_issue(rule, context))
        elif rule.matcher == "evidence_required":
            issues.extend(_evidence_issue(rule, context))
        else:
            issues.extend(_required_term_issue(rule, context, profile))
    unique: list[StructuredIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.rule_id, issue.field, _normalized(issue.evidence))
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique
