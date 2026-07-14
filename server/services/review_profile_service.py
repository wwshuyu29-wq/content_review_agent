from __future__ import annotations

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
    evidence_requirements: Tuple[Mapping[str, Any], ...] = ()
    platform_requirements: Mapping[str, Mapping[str, Any]] = Field(default_factory=dict)
    replacement_rules: Tuple[Mapping[str, Any], ...] = ()
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
    platform_requirements = structured.get("platform_requirements", {})
    aliases: dict[str, str] = {}
    for canonical, config in platform_requirements.items():
        for alias in config.get("aliases", [canonical]):
            if alias in aliases and aliases[alias] != canonical:
                raise ValueError(f"duplicate platform alias: {alias}")
            aliases[alias] = canonical
    return ReviewProfile(
        business_domain=expected["business_domain"],
        document_type=expected["document_type"],
        project_code=expected["project_code"],
        content_type=expected["content_type"],
        package_version=expected["version"],
        package_digest=rule_version.package_digest,
        rules=rules + replacement_specs,
        evidence_requirements=tuple(evidence),
        platform_requirements=platform_requirements,
        replacement_rules=replacements,
        platform_aliases=aliases,
    )
