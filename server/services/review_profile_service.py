from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field

from server.services.standard_package_service import SUPPORTED_MATCHERS, compute_package_digest


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    rule_id: str = Field(min_length=1)
    scope: Mapping[str, Any] = Field(default_factory=dict)
    matcher: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    action: str = Field(min_length=1)
    auto_fixable: bool = False
    source_reference: Tuple[str, ...] = ()


class ReviewProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    business_domain: str
    document_type: str
    project_code: str
    content_type: str
    package_version: str
    package_digest: str
    rules: Tuple[RuleSpec, ...]
    project_facts: Mapping[str, Any] = Field(default_factory=dict)
    global_standards: Mapping[str, str] = Field(default_factory=dict)
    approved_claims: Tuple[Mapping[str, Any], ...] = ()
    pending_claims: Tuple[Mapping[str, Any], ...] = ()
    evidence_requirements: Tuple[Mapping[str, Any], ...] = ()
    known_source_references: Tuple[str, ...] = ()
    platform_requirements: Mapping[str, Mapping[str, Any]] = Field(default_factory=dict)
    replacement_rules: Tuple[Mapping[str, Any], ...] = ()
    safe_replacement_map: Mapping[str, Mapping[str, str]] = Field(default_factory=dict)
    platform_aliases: Mapping[str, str] = Field(default_factory=dict)


def _snapshot_compiled(rule_version: Any) -> dict[str, Any]:
    metadata = rule_version.dimension_standards.get("metadata", {})
    return {
        "metadata": metadata,
        "project_facts": rule_version.project_facts,
        "dimension_standards": rule_version.dimension_standards,
        "structured_rules": rule_version.structured_rules,
    }


def get_review_profile(rule_version: Any) -> ReviewProfile:
    metadata = rule_version.dimension_standards.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("rule version identity mismatch: metadata")
    expected = {
        "business_domain": "baidu_maps_marketing_review",
        "document_type": "project_standard",
        "project_code": rule_version.project_code,
        "content_type": "TECH_MEDIA_REVIEW",
        "version": rule_version.package_version,
    }
    for key, value in expected.items():
        if not value or metadata.get(key) != value:
            raise ValueError(f"rule version identity mismatch: {key}")
    for key in ("business_domain", "document_type", "project_code", "content_type"):
        if getattr(rule_version, key) != expected[key]:
            raise ValueError(f"rule version identity mismatch: {key}")
    if not rule_version.package_digest:
        raise ValueError("rule version identity mismatch: package_digest")
    if compute_package_digest(_snapshot_compiled(rule_version)) != rule_version.package_digest:
        raise ValueError("rule version identity mismatch: package_digest")

    structured = rule_version.structured_rules
    legacy = {"deny_words", "must_human_keywords", "required_tags", "recommended"} & set(structured)
    if legacy:
        raise ValueError(f"legacy rule arrays are not allowed: {', '.join(sorted(legacy))}")
    rules = tuple(RuleSpec(**rule) for rule in structured.get("rules", []))
    for rule in rules:
        if rule.matcher not in SUPPORTED_MATCHERS:
            raise ValueError(f"unsupported matcher: {rule.matcher}")
    evidence = structured.get("evidence_requirements", {}).get("evidence_requirements", [])
    replacements = tuple(structured.get("replacement_rules", {}).get("replacement_rules", []))
    rule_ids = [rule.rule_id for rule in rules]
    replacement_ids = [replacement.get("replacement_id") for replacement in replacements]
    if (
        any(not replacement_id for replacement_id in replacement_ids)
        or len(replacement_ids) != len(set(replacement_ids))
        or set(rule_ids) & set(replacement_ids)
    ):
        raise ValueError("duplicate rule or replacement IDs")
    replacement_specs = tuple(
        RuleSpec(
            rule_id=replacement["replacement_id"],
            scope={"content_type": "TECH_MEDIA_REVIEW", "fields": ["title", "body"]},
            matcher="replacement_map",
            severity="LOW",
            action="SUGGEST_REPLACEMENT",
            auto_fixable=True,
            source_reference=tuple(replacement.get("source_reference", [])),
            replacement_map={replacement["from"]: replacement["to"]},
        )
        for replacement in replacements
    )
    safe_replacements: dict[str, dict[str, str]] = {}
    for rule in rules:
        if rule.matcher == "replacement_map" and rule.severity.upper() == "LOW" and rule.auto_fixable:
            replacement_map = rule.model_extra.get("replacement_map", {})
            if isinstance(replacement_map, dict) and all(
                isinstance(source, str) and isinstance(target, str) for source, target in replacement_map.items()
            ):
                safe_replacements[rule.rule_id] = dict(replacement_map)
    for replacement in replacements:
        source, target = replacement.get("from"), replacement.get("to")
        if isinstance(source, str) and isinstance(target, str):
            safe_replacements[replacement["replacement_id"]] = {source: target}
    platform_requirements = structured.get("platform_requirements", {})
    approved_claims = tuple(structured.get("approved_claims", []))
    pending_claims = tuple(structured.get("pending_claims", []))
    global_standards = dict(rule_version.dimension_standards.get("standards", {}))
    project_facts = dict(rule_version.project_facts or {})
    known_references = {
        "project.yaml", "approved_claims.yaml", "evidence_requirements.yaml",
        "platform_requirements.yaml", "project_context.md",
        "compliance.md", "brand_consistency.md", "content_accuracy.md",
        "test_credibility.md", "content_quality.md", "campaign_effectiveness.md",
    }
    for claim in approved_claims + pending_claims:
        known_references.update(claim.get("source_reference", []))
    for requirement in evidence:
        known_references.update(requirement.get("source_reference", []))
    for rule in rules + replacement_specs:
        known_references.update(rule.source_reference)
    aliases: dict[str, str] = {}
    for canonical, config in platform_requirements.items():
        for alias in config.get("aliases", [canonical]):
            if alias in aliases and aliases[alias] != canonical:
                raise ValueError(f"duplicate platform alias: {alias}")
            aliases[alias] = canonical
    profile = ReviewProfile(
        business_domain=expected["business_domain"],
        document_type=expected["document_type"],
        project_code=expected["project_code"],
        content_type=expected["content_type"],
        package_version=expected["version"],
        package_digest=rule_version.package_digest,
        rules=rules + replacement_specs,
        project_facts=project_facts,
        global_standards=global_standards,
        approved_claims=approved_claims,
        pending_claims=pending_claims,
        evidence_requirements=tuple(evidence),
        known_source_references=tuple(sorted(known_references)),
        platform_requirements=platform_requirements,
        replacement_rules=replacements,
        safe_replacement_map=safe_replacements,
        platform_aliases=aliases,
    )
    object.__setattr__(
        profile,
        "safe_replacement_map",
        MappingProxyType({
            rule_id: MappingProxyType(dict(replacements_for_rule))
            for rule_id, replacements_for_rule in safe_replacements.items()
        }),
    )
    return profile
