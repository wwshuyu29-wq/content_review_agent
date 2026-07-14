from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..models import Project, RuleVersion


SUPPORTED_MATCHERS = {
    "exact_phrase",
    "phrase_list",
    "replacement_map",
    "count_consistency",
    "evidence_required",
    "required_term",
}


class StandardMetadata(BaseModel):
    business_domain: Literal["baidu_maps_marketing_review"]
    document_type: Literal["project_standard"]
    project_code: Literal["bdmap_xdxx_tech_review_2026"]
    content_type: Literal["TECH_MEDIA_REVIEW"]
    version: Literal["0.9"]


class Claim(BaseModel):
    model_config = ConfigDict(extra="allow")
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_reference: List[str] = Field(default_factory=list)


class PendingClaim(Claim):
    reason: str = Field(min_length=1)


class ProjectStandard(BaseModel):
    model_config = ConfigDict(extra="allow")
    business_domain: Literal["baidu_maps_marketing_review"]
    document_type: Literal["project_standard"]
    project_code: Literal["bdmap_xdxx_tech_review_2026"]
    content_type: Literal["TECH_MEDIA_REVIEW"]
    version: Literal["0.9"]
    name: str
    facts: Dict[str, Any] = Field(default_factory=dict)


class EvidenceRequirement(BaseModel):
    model_config = ConfigDict(extra="allow")
    requirement_id: str
    trigger: str
    required_fields: List[str] = Field(default_factory=list)
    source_reference: List[str] = Field(default_factory=list)


class EvidenceRequirements(BaseModel):
    evidence_requirements: List[EvidenceRequirement] = Field(default_factory=list)


class PlatformRequirement(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    requirements: List[Any] = Field(default_factory=list)


class DeterministicRule(BaseModel):
    model_config = ConfigDict(extra="allow")
    rule_id: str
    scope: Dict[str, Any] = Field(default_factory=dict)
    matcher: str
    severity: str
    action: str
    auto_fixable: bool = False
    source_reference: List[str] = Field(default_factory=list)


class StandardPackage(BaseModel):
    metadata: StandardMetadata
    project: ProjectStandard
    approved_claims: List[Claim]
    pending_claims: List[PendingClaim]
    evidence_requirements: EvidenceRequirements
    platform_requirements: Dict[str, PlatformRequirement]
    deterministic_rules: List[DeterministicRule]
    global_standards: Dict[str, str]
    term_dictionary: Dict[str, Any] = Field(default_factory=dict)
    replacement_rules: Dict[str, Any] = Field(default_factory=dict)


def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _unique(values: List[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} IDs")


def load_standard_package(root: Path, project_code: str) -> StandardPackage:
    project_dir = root / "projects" / "xiaoduxiangxiang_tech_review"
    if not project_dir.is_dir():
        raise ValueError(f"standard project directory not found: {project_dir}")
    project_data = _read_yaml(project_dir / "project.yaml")
    if project_data.get("project_code") != project_code:
        raise ValueError("project_code does not match requested package")
    if project_data.get("business_domain") != "baidu_maps_marketing_review":
        raise ValueError("business_domain must be baidu_maps_marketing_review")
    if project_data.get("document_type") != "project_standard":
        raise ValueError("document_type must be project_standard")
    if project_data.get("content_type") != "TECH_MEDIA_REVIEW":
        raise ValueError("content_type must be TECH_MEDIA_REVIEW")

    claims_data = _read_yaml(project_dir / "approved_claims.yaml")
    evidence_data = _read_yaml(project_dir / "evidence_requirements.yaml")
    platforms_data = _read_yaml(project_dir / "platform_requirements.yaml")
    rules_data = json.loads((root / "rules" / "deterministic_rules.json").read_text(encoding="utf-8"))
    package = StandardPackage(
        metadata=StandardMetadata(**{key: project_data[key] for key in StandardMetadata.model_fields}),
        project=ProjectStandard(**project_data),
        approved_claims=claims_data.get("approved_claims", []),
        pending_claims=claims_data.get("pending_claims", []),
        evidence_requirements=EvidenceRequirements(**evidence_data),
        platform_requirements=platforms_data.get("platform_requirements", {}),
        deterministic_rules=rules_data.get("rules", []),
        global_standards={
            name: (root / "global" / f"{name}.md").read_text(encoding="utf-8")
            for name in (
                "compliance",
                "brand_consistency",
                "content_accuracy",
                "test_credibility",
                "content_quality",
                "campaign_effectiveness",
            )
        },
        term_dictionary=json.loads((root / "rules" / "term_dictionary.json").read_text(encoding="utf-8")),
        replacement_rules=json.loads((root / "rules" / "replacement_rules.json").read_text(encoding="utf-8")),
    )
    if package.project.project_code != package.metadata.project_code or package.project.version != package.metadata.version:
        raise ValueError("project metadata does not match package metadata")
    claim_ids = [claim.claim_id for claim in package.approved_claims + package.pending_claims]
    rule_ids = [rule.rule_id for rule in package.deterministic_rules]
    _unique(claim_ids, "claim")
    _unique(rule_ids, "rule")
    _unique(
        [requirement.requirement_id for requirement in package.evidence_requirements.evidence_requirements],
        "evidence requirement",
    )
    known_references = set(claim_ids) | {
        "project.yaml",
        "approved_claims.yaml",
        "evidence_requirements.yaml",
        "platform_requirements.yaml",
        "compliance.md",
        "brand_consistency.md",
        "content_accuracy.md",
        "test_credibility.md",
        "content_quality.md",
        "campaign_effectiveness.md",
    }
    references = [
        reference
        for claim in package.approved_claims + package.pending_claims
        for reference in claim.source_reference
    ]
    references.extend(
        reference
        for requirement in package.evidence_requirements.evidence_requirements
        for reference in requirement.source_reference
    )
    references.extend(
        reference
        for rule in package.deterministic_rules
        for reference in rule.source_reference
    )
    references.extend(
        reference
        for replacement in package.replacement_rules.get("replacement_rules", [])
        for reference in replacement.get("source_reference", [])
    )
    missing = set(references) - known_references
    if missing:
        raise ValueError(f"unresolved source reference: {', '.join(sorted(missing))}")
    for rule in package.deterministic_rules:
        if rule.matcher not in SUPPORTED_MATCHERS:
            raise ValueError(f"unsupported matcher: {rule.matcher}")
    return package


def compile_standard_package(package: StandardPackage) -> dict[str, Any]:
    return {
        "metadata": package.metadata.model_dump(),
        "project_facts": {
            "project_code": package.project.project_code,
            "content_type": package.project.content_type,
            **package.project.facts,
        },
        "dimension_standards": {
            "metadata": package.metadata.model_dump(),
            "standards": package.global_standards,
        },
        "structured_rules": {
            "rules": [rule.model_dump() for rule in package.deterministic_rules],
            "approved_claims": [claim.model_dump() for claim in package.approved_claims],
            "pending_claims": [claim.model_dump() for claim in package.pending_claims],
            "evidence_requirements": package.evidence_requirements.model_dump(),
            "platform_requirements": {
                key: value.model_dump() for key, value in package.platform_requirements.items()
            },
            "term_dictionary": package.term_dictionary,
            "replacement_rules": package.replacement_rules,
        },
    }


def _version_number(version: str) -> int:
    try:
        major, minor = version.split(".", 1)
        return int(major) * 10 + int(minor.split(".", 1)[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"invalid standard version: {version}") from exc


def publish_standard_package(session: Session, project_id: int, package: StandardPackage) -> RuleVersion:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    if project.code != package.metadata.project_code:
        raise ValueError("project code does not match package")
    if project.content_type != package.metadata.content_type:
        raise ValueError("content_type does not match package")
    compiled = compile_standard_package(package)
    version = RuleVersion(
        project=project,
        version=_version_number(package.metadata.version),
        dimension_standards=compiled["dimension_standards"],
        project_facts=compiled["project_facts"],
        structured_rules=compiled["structured_rules"],
        prompt_version=f"{package.metadata.content_type.lower()}-{package.metadata.version}",
    )
    session.add(version)
    session.flush()
    project.current_rule_version = version
    return version
