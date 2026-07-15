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
from server.services.standard_package_service import (
    compile_standard_package,
    compute_package_digest,
    load_standard_package,
    publish_standard_package,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def standards_root(tmp_path: Path) -> Path:
    root = tmp_path / "standards"
    shutil.copytree(REPO_ROOT / "data" / "standards", root)
    return root


def test_loads_only_matching_tech_media_package(standards_root: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026")

    assert package.metadata.business_domain == "baidu_maps_marketing_review"
    assert package.metadata.document_type == "project_standard"
    assert package.metadata.content_type == "TECH_MEDIA_REVIEW"
    assert package.metadata.project_code == "bdmap_xdxx_tech_review_2026"
    assert "PENDING-002" in {claim.claim_id for claim in package.pending_claims}
    serialized = json.dumps(package.model_dump(), ensure_ascii=False)
    assert "范丞丞" not in serialized
    assert "代言" not in serialized
    assert "deny_words" not in serialized
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
        .replace('version: "0.9"', 'version: "1.0"'),
        encoding="utf-8",
    )

    package = load_standard_package(standards_root, "future_tech_review_2027", "1.0")

    assert package.metadata.project_code == "future_tech_review_2027"
    assert package.metadata.version == "1.0"


def test_rejects_unknown_top_level_package_fields(standards_root: Path) -> None:
    project_file = standards_root / "projects" / "xiaoduxiangxiang_tech_review" / "project.yaml"
    project_file.write_text(
        project_file.read_text(encoding="utf-8") + "unexpected_field: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected_field"):
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026", "0.9")


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
        load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026", "0.9")


def test_same_version_tampering_is_rejected_by_digest(standards_root: Path, tmp_path: Path) -> None:
    package = load_standard_package(standards_root, "bdmap_xdxx_tech_review_2026", "0.9")
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

    assert compiled["metadata"]["version"] == "0.9"
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
        assert version.package_version == "0.9"
        assert version.package_digest == compute_package_digest(compiled)
        assert version.project_code == "bdmap_xdxx_tech_review_2026"
        assert version.dimension_standards["metadata"] == compiled["metadata"]
        assert project.current_rule_version_id == version.id
        assert session.get(RuleVersion, version.id).structured_rules == compiled["structured_rules"]
