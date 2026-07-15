from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Project, RuleVersion

SUPPORTED_MATCHERS = {
    "exact_phrase",
    "phrase_list",
    "replacement_map",
    "count_consistency",
    "evidence_required",
    "required_term",
    "guarded_claim",
    "hotel_capability",
}
_SEMANTIC_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config" / "审核Agent配置.json"
_CANONICAL_GLOBAL_FILES = (
    "合规与广告表达.md",
    "品牌一致性.md",
    "内容准确性.md",
    "实测可信度.md",
    "内容质量.md",
    "传播有效性.md",
    "舆情与素材授权.md",
)
_EXPECTED_AGENT_IDS = {
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "TEST_CREDIBILITY",
    "CONTENT_QUALITY",
    "CAMPAIGN_EFFECTIVENESS",
}
_NON_RULE_HEADINGS = {
    "评分标准",
    "风险与动作",
    "自动修改边界",
    "处理动作",
    "风险等级与建议动作",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentStandardBinding(StrictModel):
    global_standard: str
    specialist_prompt: str
    supplemental_standards: tuple[str, ...] = ()


def _repo_relative_path(raw_path: str) -> Path:
    path = (_REPO_ROOT / raw_path).resolve()
    try:
        path.relative_to(_REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"configured path escapes repository: {raw_path}") from exc
    if not path.is_file():
        raise ValueError(f"configured file is missing: {raw_path}")
    return path


def _load_agent_config() -> tuple[dict[str, AgentStandardBinding], str]:
    try:
        config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load Agent config: {exc}") from exc
    agents = config.get("审核Agent")
    if not isinstance(agents, dict) or set(agents) != _EXPECTED_AGENT_IDS:
        raise ValueError("Agent config must contain the exact six internal Agent IDs")
    public_prompt = config.get("公共Prompt")
    if not isinstance(public_prompt, str):
        raise ValueError("Agent config must define 公共Prompt")
    _repo_relative_path(public_prompt)
    bindings: dict[str, AgentStandardBinding] = {}
    for agent_id, raw in agents.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Agent config for {agent_id} must be an object")
        binding = AgentStandardBinding(
            global_standard=Path(raw["全局标准"]).name,
            specialist_prompt=raw["Prompt"],
            supplemental_standards=tuple(Path(item).name for item in raw.get("补充标准", [])),
        )
        _repo_relative_path(raw["全局标准"])
        _repo_relative_path(raw["Prompt"])
        for supplemental in raw.get("补充标准", []):
            _repo_relative_path(supplemental)
        bindings[agent_id] = binding
    primary_files = [binding.global_standard for binding in bindings.values()]
    if len(primary_files) != len(set(primary_files)):
        raise ValueError("each Agent must load exactly one unique global standard")
    supplemental_agents = {
        agent_id for agent_id, binding in bindings.items()
        if "舆情与素材授权.md" in binding.supplemental_standards
    }
    if supplemental_agents != {"COMPLIANCE", "BRAND"}:
        raise ValueError("authorization standard is supplemental only for COMPLIANCE and BRAND")
    return bindings, public_prompt


AGENT_STANDARD_CONFIG, PUBLIC_PROMPT_PATH = _load_agent_config()


class StandardMetadata(StrictModel):
    business_domain: str
    document_type: str
    project_code: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @field_validator("business_domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        if value != "baidu_maps_marketing_review":
            raise ValueError("business_domain must be baidu_maps_marketing_review")
        return value

    @field_validator("document_type")
    @classmethod
    def validate_document_type(cls, value: str) -> str:
        if value != "project_standard":
            raise ValueError("document_type must be project_standard")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMANTIC_VERSION.fullmatch(value):
            raise ValueError("version must be semantic major.minor[.patch]")
        return value


class Claim(StrictModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_reference: List[str] = Field(default_factory=list)


class PendingClaim(Claim):
    reason: str = Field(min_length=1)


class ProjectStandard(StrictModel):
    business_domain: str
    document_type: str
    project_code: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    facts: Dict[str, Any] = Field(default_factory=dict)


class EvidenceRequirement(StrictModel):
    requirement_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    required_fields: List[str] = Field(default_factory=list)
    source_reference: List[str] = Field(default_factory=list)


class EvidenceRequirements(StrictModel):
    evidence_requirements: List[EvidenceRequirement] = Field(default_factory=list)


class ClaimsFile(StrictModel):
    approved_claims: List[Claim]
    pending_claims: List[PendingClaim]


class PlatformRequirementsFile(StrictModel):
    platform_requirements: Dict[str, PlatformRequirement]


class PlatformRequirement(StrictModel):
    status: str = Field(min_length=1)
    requirements: List[Any] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)


class DeterministicRule(StrictModel):
    rule_id: str = Field(min_length=1)
    scope: Dict[str, Any] = Field(default_factory=dict)
    matcher: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    action: str = Field(min_length=1)
    auto_fixable: bool = False
    source_reference: List[str] = Field(default_factory=list)
    phrases: List[str] = Field(default_factory=list)
    subject_terms: List[str] = Field(default_factory=list)
    quantifier_terms: List[str] = Field(default_factory=list)
    capability_terms: List[str] = Field(default_factory=list)
    unambiguous_capability_terms: List[str] = Field(default_factory=list)
    product_terms: List[str] = Field(default_factory=list)
    assertion_terms: List[str] = Field(default_factory=list)
    percentage_context_terms: List[str] = Field(default_factory=list)
    hotel_terms: List[str] = Field(default_factory=list)
    negation_terms: List[str] = Field(default_factory=list)
    criticism_terms: List[str] = Field(default_factory=list)
    title_pattern: Optional[str] = None
    trigger_terms: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    required_test_case_fields: List[str] = Field(default_factory=list)
    required_terms: List[str] = Field(default_factory=list)
    replacement_map: Dict[str, str] = Field(default_factory=dict)


class RulesFile(StrictModel):
    rules: List[DeterministicRule]


class TermEntry(StrictModel):
    term_id: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    aliases: List[str] = Field(default_factory=list)


class TermDictionary(StrictModel):
    terms: List[TermEntry] = Field(default_factory=list)


class ReplacementRule(StrictModel):
    replacement_id: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    source_reference: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReplacementRules(StrictModel):
    replacement_rules: List[ReplacementRule] = Field(default_factory=list)


class StandardPackage(StrictModel):
    metadata: StandardMetadata
    project: ProjectStandard
    approved_claims: List[Claim]
    pending_claims: List[PendingClaim]
    evidence_requirements: EvidenceRequirements
    platform_requirements: Dict[str, PlatformRequirement]
    deterministic_rules: List[DeterministicRule]
    global_standards: Dict[str, str]
    public_prompt: str
    agent_prompts: Dict[str, str]
    agent_standard_bindings: Dict[str, AgentStandardBinding]
    agent_prompt_versions: Dict[str, str]
    file_hashes: Dict[str, str]
    term_dictionary: TermDictionary
    replacement_rules: ReplacementRules


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"failed to read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read JSON {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _unique(values: List[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} IDs")


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"failed to hash {path.name}: {exc}") from exc


def _validate_markdown_rule_ids(filename: str, content: str) -> None:
    for line in content.splitlines():
        if not line.startswith("## "):
            continue
        heading = line[3:].strip()
        if re.match(r"^\[[A-Z]+(?:-[A-Z]+)*-\d{3}\] ", heading):
            continue
        if re.match(r"^[一二三四五六七八九十]+、", heading) or heading in _NON_RULE_HEADINGS:
            continue
        raise ValueError(f"rule section without [RULE-ID] in {filename}: {heading}")


def _find_project_dir(root: Path, project_code: str, package_version: str) -> Path:
    if not _SEMANTIC_VERSION.fullmatch(package_version):
        raise ValueError("package_version must be semantic major.minor[.patch]")
    projects_root = root / "projects"
    matches: list[Path] = []
    for candidate in sorted(projects_root.iterdir() if projects_root.is_dir() else []):
        if not candidate.is_dir() or not (candidate / "project.yaml").is_file():
            continue
        data = _read_yaml(candidate / "project.yaml")
        if data.get("project_code") == project_code:
            matches.append(candidate)
    if not matches:
        raise ValueError(f"no standard package found for project_code {project_code}")
    if len(matches) != 1:
        raise ValueError(f"multiple standard packages found for project_code {project_code}")
    project_data = _read_yaml(matches[0] / "project.yaml")
    if project_data.get("version") != package_version:
        raise ValueError(
            f"package_version {package_version} does not match project package version {project_data.get('version')}"
        )
    return matches[0]


def _validate_project_schema(root: Path, project_data: Dict[str, Any]) -> None:
    schema_path = root / "schemas" / "project_standard.schema.json"
    try:
        schema = _read_json(schema_path)
        validate_json_schema(project_data, schema)
    except JsonSchemaValidationError as exc:
        field = ".".join(str(part) for part in exc.absolute_path) or "project_standard"
        raise ValueError(f"project standard schema validation failed for {field}: {exc.message}") from exc


def load_standard_package(root: Path, project_code: str, package_version: str = "1.0") -> StandardPackage:
    project_dir = _find_project_dir(root, project_code, package_version)
    project_data = _read_yaml(project_dir / "project.yaml")
    _validate_project_schema(root, project_data)
    metadata = StandardMetadata(**{key: project_data[key] for key in StandardMetadata.model_fields})
    if metadata.project_code != project_code or metadata.version != package_version:
        raise ValueError("requested project package identity does not match project.yaml")

    try:
        global_standards: dict[str, str] = {}
        for filename in _CANONICAL_GLOBAL_FILES:
            content = (root / "global" / filename).read_text(encoding="utf-8")
            _validate_markdown_rule_ids(filename, content)
            global_standards[filename] = content
        public_prompt = _repo_relative_path(PUBLIC_PROMPT_PATH).read_text(encoding="utf-8")
        agent_prompts = {
            agent_id: _repo_relative_path(binding.specialist_prompt).read_text(encoding="utf-8")
            for agent_id, binding in AGENT_STANDARD_CONFIG.items()
        }
        hash_paths: dict[str, Path] = {
            "config/审核Agent配置.json": _CONFIG_PATH,
            PUBLIC_PROMPT_PATH: _repo_relative_path(PUBLIC_PROMPT_PATH),
        }
        hash_paths.update({
            binding.specialist_prompt: _repo_relative_path(binding.specialist_prompt)
            for binding in AGENT_STANDARD_CONFIG.values()
        })
        hash_paths.update({
            f"data/standards/global/{filename}": root / "global" / filename
            for filename in _CANONICAL_GLOBAL_FILES
        })
        for filename in (
            "project.yaml", "approved_claims.yaml", "evidence_requirements.yaml",
            "platform_requirements.yaml", "project_context.md",
        ):
            hash_paths[f"data/standards/projects/{project_dir.name}/{filename}"] = project_dir / filename
        for filename in ("deterministic_rules.json", "term_dictionary.json", "replacement_rules.json"):
            hash_paths[f"data/standards/rules/{filename}"] = root / "rules" / filename
        for filename in ("review_result.schema.json", "project_standard.schema.json", "test_case.schema.json"):
            hash_paths[f"data/standards/schemas/{filename}"] = root / "schemas" / filename
        file_hashes = {logical_path: _sha256(path) for logical_path, path in hash_paths.items()}
        prompt_versions = {
            agent_id: file_hashes[binding.specialist_prompt]
            for agent_id, binding in AGENT_STANDARD_CONFIG.items()
        }

        claims_data = ClaimsFile(**_read_yaml(project_dir / "approved_claims.yaml"))
        evidence_data = EvidenceRequirements(**_read_yaml(project_dir / "evidence_requirements.yaml"))
        platforms_data = PlatformRequirementsFile(**_read_yaml(project_dir / "platform_requirements.yaml"))
        rules_data = _read_json(root / "rules" / "deterministic_rules.json")
        if any(key in rules_data for key in ("deny_words", "must_human_keywords", "required_tags", "recommended")):
            legacy_keys = sorted(key for key in rules_data if key in {"deny_words", "must_human_keywords", "required_tags", "recommended"})
            raise ValueError(f"legacy rule arrays are not allowed in a standard package: {', '.join(legacy_keys)}")
        parsed_rules = RulesFile(**rules_data)
        package = StandardPackage(
            metadata=metadata,
            project=ProjectStandard(**project_data),
            approved_claims=claims_data.approved_claims,
            pending_claims=claims_data.pending_claims,
            evidence_requirements=evidence_data,
            platform_requirements=platforms_data.platform_requirements,
            deterministic_rules=parsed_rules.rules,
            global_standards=global_standards,
            public_prompt=public_prompt,
            agent_prompts=agent_prompts,
            agent_standard_bindings=AGENT_STANDARD_CONFIG,
            agent_prompt_versions=prompt_versions,
            file_hashes=file_hashes,
            term_dictionary=TermDictionary(**_read_json(root / "rules" / "term_dictionary.json")),
            replacement_rules=ReplacementRules(**_read_json(root / "rules" / "replacement_rules.json")),
        )
    except (KeyError, OSError) as exc:
        raise ValueError(f"standard package is incomplete: {exc}") from exc

    if package.project.model_dump() and (
        package.project.project_code != package.metadata.project_code
        or package.project.version != package.metadata.version
        or package.project.content_type != package.metadata.content_type
    ):
        raise ValueError("project metadata does not match package metadata")
    claim_ids = [claim.claim_id for claim in package.approved_claims + package.pending_claims]
    _unique(claim_ids, "claim")
    rule_ids = [rule.rule_id for rule in package.deterministic_rules]
    replacement_ids = [entry.replacement_id for entry in package.replacement_rules.replacement_rules]
    _unique(rule_ids, "rule")
    _unique([entry.term_id for entry in package.term_dictionary.terms], "term")
    _unique(replacement_ids, "replacement")
    if set(rule_ids) & set(replacement_ids):
        raise ValueError("duplicate rule or replacement IDs")
    _unique(
        [requirement.requirement_id for requirement in package.evidence_requirements.evidence_requirements],
        "evidence requirement",
    )
    known_references = set(claim_ids) | {
        "project.yaml", "approved_claims.yaml", "evidence_requirements.yaml", "platform_requirements.yaml",
        "project_context.md", *_CANONICAL_GLOBAL_FILES,
    }
    references = [reference for claim in package.approved_claims + package.pending_claims for reference in claim.source_reference]
    references += [reference for requirement in package.evidence_requirements.evidence_requirements for reference in requirement.source_reference]
    references += [reference for rule in package.deterministic_rules for reference in rule.source_reference]
    references += [reference for replacement in package.replacement_rules.replacement_rules for reference in replacement.source_reference]
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
            "public_prompt": package.public_prompt,
            "agent_prompts": package.agent_prompts,
            "agent_standard_bindings": {
                agent_id: binding.model_dump(mode="json")
                for agent_id, binding in package.agent_standard_bindings.items()
            },
            "file_hashes": package.file_hashes,
            "agent_prompt_versions": package.agent_prompt_versions,
        },
        "file_hashes": package.file_hashes,
        "agent_prompt_versions": package.agent_prompt_versions,
        "structured_rules": {
            "rules": [rule.model_dump() for rule in package.deterministic_rules],
            "approved_claims": [claim.model_dump() for claim in package.approved_claims],
            "pending_claims": [claim.model_dump() for claim in package.pending_claims],
            "evidence_requirements": package.evidence_requirements.model_dump(),
            "platform_requirements": {key: value.model_dump() for key, value in package.platform_requirements.items()},
            "term_dictionary": package.term_dictionary.model_dump(),
            "replacement_rules": package.replacement_rules.model_dump(by_alias=True),
        },
    }


def compute_package_digest(compiled: dict[str, Any]) -> str:
    canonical = json.dumps(
        compiled,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def publish_standard_package(session: Session, project_id: int, package: StandardPackage) -> RuleVersion:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    if project.code != package.metadata.project_code:
        raise ValueError("project code does not match package")
    if project.content_type != package.metadata.content_type:
        raise ValueError("content_type does not match package")
    compiled = compile_standard_package(package)
    package_digest = compute_package_digest(compiled)
    existing = session.scalar(
        select(RuleVersion).where(
            RuleVersion.project_id == project_id,
            RuleVersion.package_version == package.metadata.version,
        )
    )
    if existing is not None:
        if existing.package_digest != package_digest:
            raise ValueError(
                "standard package digest mismatch for existing package_version; publish a new package version"
            )
        project.current_rule_version = existing
        return existing
    latest = session.scalar(select(func.max(RuleVersion.version)).where(RuleVersion.project_id == project_id)) or 0
    version = RuleVersion(
        project=project,
        version=latest + 1,
        business_domain=package.metadata.business_domain,
        document_type=package.metadata.document_type,
        project_code=package.metadata.project_code,
        content_type=package.metadata.content_type,
        package_version=package.metadata.version,
        package_digest=package_digest,
        dimension_standards=compiled["dimension_standards"],
        project_facts=compiled["project_facts"],
        structured_rules=compiled["structured_rules"],
        prompt_version=f"{package.metadata.content_type.lower()}-{package.metadata.version}",
    )
    session.add(version)
    session.flush()
    project.current_rule_version = version
    return version
