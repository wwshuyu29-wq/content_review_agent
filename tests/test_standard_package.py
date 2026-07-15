from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from sqlalchemy.orm import Session

from server.db import Base, create_db_engine
from server.models import Project, RuleVersion
from server.services import standard_package_service
from server.services.standard_package_service import (
    compile_standard_package,
    compute_package_digest,
    load_standard_package,
    publish_standard_package,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_GLOBAL_FILES = {
    "合规与广告表达.md",
    "品牌一致性.md",
    "内容准确性.md",
    "实测可信度.md",
    "内容质量.md",
    "传播有效性.md",
    "舆情与素材授权.md",
}
EXPECTED_AGENT_IDS = {
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "TEST_CREDIBILITY",
    "CONTENT_QUALITY",
    "CAMPAIGN_EFFECTIVENESS",
}


@pytest.fixture
def standards_root(tmp_path: Path) -> Path:
    root = tmp_path / "standards"
    shutil.copytree(REPO_ROOT / "data" / "standards", root)
    return root


def test_production_global_directory_contains_only_canonical_chinese_files() -> None:
    global_root = REPO_ROOT / "data" / "standards" / "global"

    assert {path.name for path in global_root.iterdir() if path.is_file()} == CANONICAL_GLOBAL_FILES


def test_agent_standard_config_has_exact_six_unique_global_bindings() -> None:
    bindings = standard_package_service.AGENT_STANDARD_CONFIG

    assert set(bindings) == EXPECTED_AGENT_IDS
    global_files = [binding.global_standard for binding in bindings.values()]
    assert len(global_files) == len(set(global_files)) == 6


def test_authorization_is_supplemental_only_for_compliance_and_brand() -> None:
    bindings = standard_package_service.AGENT_STANDARD_CONFIG
    authorization_agents = {
        agent_id
        for agent_id, binding in bindings.items()
        if "舆情与素材授权.md" in binding.supplemental_standards
    }

    assert authorization_agents == {"COMPLIANCE", "BRAND"}


def test_loads_v1_package_from_canonical_chinese_paths() -> None:
    package = load_standard_package(
        REPO_ROOT / "data" / "standards",
        "bdmap_xdxx_tech_review_2026",
    )

    assert package.metadata.version == "1.0"
    assert package.project.name == "百度地图小度想想科技媒体测评"
    assert set(package.global_standards) == CANONICAL_GLOBAL_FILES
    assert set(package.agent_prompt_versions) == EXPECTED_AGENT_IDS
    assert "config/审核Agent配置.json" in package.file_hashes
    assert all(len(file_hash) == 64 for file_hash in package.file_hashes.values())


def test_compiled_digest_covers_config_and_prompt_hashes() -> None:
    package = load_standard_package(REPO_ROOT / "data" / "standards", "bdmap_xdxx_tech_review_2026")
    compiled = compile_standard_package(package)

    assert compiled["file_hashes"] == package.file_hashes
    assert compiled["agent_prompt_versions"] == package.agent_prompt_versions
    assert any(path.startswith("prompts/") for path in compiled["file_hashes"])

    changed = json.loads(json.dumps(compiled, ensure_ascii=False))
    config_path = "config/审核Agent配置.json"
    changed["file_hashes"][config_path] = "0" * 64
    assert compute_package_digest(changed) != compute_package_digest(compiled)


def test_rejects_rule_section_without_rule_id(standards_root: Path) -> None:
    standard_file = standards_root / "global" / "内容质量.md"
    standard_file.write_text(
        standard_file.read_text(encoding="utf-8").replace(
            "## [QUAL-TITLE-001] 标题与正文一致",
            "## 标题与正文一致",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="rule section.*RULE-ID|RULE-ID.*rule section"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")


def test_allows_introductory_heading_without_rule_id(standards_root: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")

    assert "## 一、基础语言质量" in package.global_standards["内容质量.md"]


def test_v1_official_claims_are_approved_while_pending_claims_stay_pending() -> None:
    package = load_standard_package(REPO_ROOT / "data" / "standards", "bdmap_xdxx_tech_review_2026")

    approved_ids = {claim.claim_id for claim in package.approved_claims}
    pending_ids = {claim.claim_id for claim in package.pending_claims}
    assert {f"PRE-{number:03d}" for number in range(1, 6)} <= approved_ids
    assert {f"WALK-{number:03d}" for number in range(1, 6)} <= approved_ids
    assert {f"PENDING-{number:03d}" for number in range(1, 5)} <= pending_ids
    assert not approved_ids & {f"PENDING-{number:03d}" for number in range(1, 5)}
    assert {"PENDING-005", "PENDING-007", "PENDING-008"} <= pending_ids
    pending_texts = {claim.text for claim in package.pending_claims}
    assert "小度想想可以AI订酒店" in pending_texts
    assert "小度想想可以自动筛选、比较酒店并判断最划算" in pending_texts


def test_loads_only_matching_tech_media_package(standards_root: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")

    assert package.metadata.business_domain == "baidu_maps_marketing_review"
    assert package.metadata.document_type == "project_standard"
    assert package.metadata.content_type == "TECH_MEDIA_REVIEW"
    assert package.metadata.project_code == "bdmap_xdxx_tech_review_2026"
    assert "PENDING-002" in {claim.claim_id for claim in package.pending_claims}
    serialized = json.dumps({
        "project": package.project.model_dump(),
        "approved_claims": [claim.model_dump() for claim in package.approved_claims],
        "pending_claims": [claim.model_dump() for claim in package.pending_claims],
    }, ensure_ascii=False)
    assert "范丞丞" not in serialized
    assert "代言" not in serialized
    full_package = json.dumps(package.model_dump(), ensure_ascii=False)
    assert "deny_words" not in full_package
    assert "must_human_keywords" not in serialized


def test_evidence_requirement_declares_claim(standards_root: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")
    requirement = next(
        item for item in package.evidence_requirements.evidence_requirements
        if item.requirement_id == "EVIDENCE-001"
    )
    assert "claim" in requirement.required_fields


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("test_case_id", ""),
        ("claim", ""),
        ("command", ""),
        ("observed_result", ""),
        ("app_version", ""),
        ("tested_at", ""),
        ("device", ""),
        ("operating_system", ""),
        ("network_environment", ""),
        ("evidence_asset_ids", []),
        ("evidence_asset_ids", [""]),
        ("evidence_asset_ids", ["   "]),
        ("evidence_asset_ids", ["asset-1", "asset-1"]),
    ],
)
def test_test_case_schema_rejects_blank_required_values_and_invalid_evidence_ids(field, value) -> None:
    schema = json.loads((REPO_ROOT / "data" / "standards" / "schemas" / "test_case.schema.json").read_text(encoding="utf-8"))
    record = {
        "test_case_id": "T1", "claim": "路线规划", "command": "规划路线",
        "observed_result": "返回路线", "evidence_asset_ids": ["asset-1"],
        "app_version": "1.0", "tested_at": "2026-07-15", "device": "phone",
        "operating_system": "iOS", "network_environment": "wifi",
    }
    record[field] = value
    with pytest.raises(JsonSchemaValidationError):
        validate_json_schema(record, schema)


def test_test_case_schema_accepts_nonblank_bound_record() -> None:
    schema = json.loads((REPO_ROOT / "data" / "standards" / "schemas" / "test_case.schema.json").read_text(encoding="utf-8"))
    record = {
        "test_case_id": "T1", "claim": "路线规划", "command": "规划路线",
        "observed_result": "返回路线", "evidence_asset_ids": ["asset-1"],
        "app_version": "1.0", "tested_at": "2026-07-15", "device": "phone",
        "operating_system": "iOS", "network_environment": "wifi",
    }
    validate_json_schema(record, schema)


def test_rejects_cross_domain_or_unresolved_rule_reference(standards_root: Path) -> None:
    project_file = standards_root / "projects" / "xiaoduxiangxiang_tech_review" / "project.yaml"
    project_file.write_text(
        project_file.read_text(encoding="utf-8").replace(
            "business_domain: baidu_maps_marketing_review",
            "business_domain: other",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="business_domain"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")


def test_loads_future_semantic_version_from_matching_project_directory(standards_root: Path) -> None:
    source = standards_root / "projects" / "xiaoduxiangxiang_tech_review"
    future = standards_root / "projects" / "future_tech_review"
    shutil.copytree(source, future)
    project_file = future / "project.yaml"
    project_file.write_text(
        project_file.read_text(encoding="utf-8")
        .replace("bdmap_xdxx_tech_review_2026", "future_tech_review_2027")
        .replace('version: "1.0"', 'version: "1.1"'),
        encoding="utf-8",
    )

    package = load_standard_package(standards_root, "future_tech_review_2027", "1.1")

    assert package.metadata.project_code == "future_tech_review_2027"
    assert package.metadata.version == "1.1"


def test_rejects_unknown_top_level_package_fields(standards_root: Path) -> None:
    project_file = standards_root / "projects" / "xiaoduxiangxiang_tech_review" / "project.yaml"
    project_file.write_text(
        project_file.read_text(encoding="utf-8") + "unexpected_field: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected_field"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026", "1.0")


def test_rejects_rule_reference_to_unknown_claim(standards_root: Path) -> None:
    rules_file = standards_root / "rules" / "deterministic_rules.json"
    rules = json.loads(rules_file.read_text(encoding="utf-8"))
    rules["rules"][0]["source_reference"] = ["CLAIM-DOES-NOT-EXIST"]
    rules_file.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="CLAIM-DOES-NOT-EXIST"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")


def test_loads_registered_compositional_matchers(standards_root: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")
    matchers = {rule.rule_id: rule.matcher for rule in package.deterministic_rules}
    assert matchers["CLAIM-UNSUPPORTED-ABSOLUTE-001"] == "guarded_claim"
    assert matchers["CLAIM-PENDING-001"] == "hotel_capability"


def test_rejects_unknown_matcher_in_package(standards_root: Path) -> None:
    rules_file = standards_root / "rules" / "deterministic_rules.json"
    rules = json.loads(rules_file.read_text(encoding="utf-8"))
    rules["rules"][0]["matcher"] = "unknown_composition"
    rules_file.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported matcher"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")


def test_rejects_legacy_rule_arrays(standards_root: Path) -> None:
    rules_file = standards_root / "rules" / "deterministic_rules.json"
    rules = json.loads(rules_file.read_text(encoding="utf-8"))
    rules["deny_words"] = ["legacy"]
    rules_file.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="deny_words"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026", "1.0")


def test_same_version_tampering_is_rejected_by_digest(standards_root: Path, tmp_path: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026", "1.0")
    engine = create_db_engine(f"sqlite:///{tmp_path / 'tamper.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(name="科技测评", code=package.metadata.project_code, content_type=package.metadata.content_type)
        session.add(project)
        session.flush()
        publish_standard_package(session, project.id, package)
        package.project.facts["tampered"] = True

        with pytest.raises(ValueError, match="digest|new package version"):
            publish_standard_package(session, project.id, package)


def test_compiles_and_publishes_immutable_standard_snapshot(standards_root: Path, tmp_path: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")
    compiled = compile_standard_package(package)

    assert compiled["metadata"]["version"] == "1.0"
    assert compiled["structured_rules"]["rules"]
    assert compiled["project_facts"]["project_code"] == "bdmap_xdxx_tech_review_2026"

    engine = create_db_engine(f"sqlite:///{tmp_path / 'standard.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(
            name="科技测评",
            code="bdmap_xdxx_tech_review_2026",
            content_type="TECH_MEDIA_REVIEW",
        )
        session.add(project)
        session.flush()
        version = publish_standard_package(session, project.id, package)
        same_version = publish_standard_package(session, project.id, package)
        session.commit()

        assert version.id == same_version.id
        assert version.version == 1
        assert version.package_version == "1.0"
        assert version.package_digest == compute_package_digest(compiled)
        assert version.project_code == "bdmap_xdxx_tech_review_2026"
        assert version.dimension_standards["metadata"] == compiled["metadata"]
        assert project.current_rule_version_id == version.id
        assert session.get(RuleVersion, version.id).structured_rules == compiled["structured_rules"]
