from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from server.db import Base, create_db_engine
from server.models import Asset, ContentVersion, ReviewStatus, TestCase as EvidenceTestCase
from server.seed import seed_default_project
from server.services.content_service import submit_batch
from server.services.evidence_service import attach_evidence, create_asset, create_test_case, list_content_test_cases
from server.services.review_arbiter_service import arbitrate_review


def make_session(tmp_path: Path) -> Session:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'evidence.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_content(session: Session, external_id: str = "content-1"):
    project = seed_default_project(session)
    batch = submit_batch(
        session, project_id=project.id, supplier_id="supplier", name=external_id,
        contents=[{"external_id": external_id, "title": "标题", "body": "正文"}],
    )
    return batch.content_items[0], batch.content_items[0].versions[0]


def test_evidence_models_round_trip_with_relationships_and_no_blob(tmp_path: Path):
    with make_session(tmp_path) as session:
        item, version = make_content(session)
        asset = create_asset(
            session, item.id, asset_id="asset-1", external_id="proof-1", kind="IMAGE",
            filename="proof.png", storage_key="uploads/proof.png", mime_type="image/png", size_bytes=12,
            metadata={"sha256": "abc"},
        )
        case = create_test_case(
            session, item.id, version.id, external_test_case_id="TEST-1", claim="生成路线",
            command="输入两个目的地", observed_result="返回路线", city="北京",
            tested_at="2026-07-14", app_version="1.0", device="phone",
            operating_system="iOS", network_environment="wifi",
        )
        evidence = attach_evidence(session, case.id, asset.id)
        session.commit()
        session.expire_all()

        saved = session.get(EvidenceTestCase, case.id)
        assert saved.content_item_id == item.id
        assert saved.content_version_id == version.id
        assert saved.evidence[0].asset.filename == "proof.png"
        assert evidence.asset_id == asset.id
        assert "blob" not in {column["name"] for column in inspect(session.bind).get_columns("assets")}


def test_evidence_service_validates_ownership_and_is_idempotent(tmp_path: Path):
    with make_session(tmp_path) as session:
        item, version = make_content(session)
        other_item, other_version = make_content(session, "content-2")
        asset = create_asset(session, item.id, asset_id="asset-1", kind="IMAGE", filename="a.png")
        case = create_test_case(
            session, item.id, version.id, external_test_case_id="TEST-1", claim="claim",
            command="command", observed_result="result",
        )
        assert create_asset(session, item.id, asset_id="asset-1", kind="IMAGE", filename="a.png").id == asset.id
        assert create_test_case(
            session, item.id, version.id, external_test_case_id="TEST-1", claim="claim",
            command="command", observed_result="result",
        ).id == case.id
        assert attach_evidence(session, case.id, asset.id).id == attach_evidence(session, case.id, asset.id).id
        with pytest.raises(ValueError, match="ownership"):
            create_test_case(
                session, item.id, other_version.id, external_test_case_id="TEST-2", claim="claim",
                command="command", observed_result="result",
            )
        with pytest.raises(ValueError, match="same content"):
            other_asset = create_asset(session, other_item.id, asset_id="other", kind="IMAGE", filename="b.png")
            attach_evidence(session, case.id, other_asset.id)


def test_list_manifest_is_structured(tmp_path: Path):
    with make_session(tmp_path) as session:
        item, version = make_content(session)
        asset = create_asset(session, item.id, asset_id="asset-1", kind="SCREENSHOT", filename="a.png")
        case = create_test_case(
            session, item.id, version.id, external_test_case_id="TEST-1", claim="claim",
            command="command", observed_result="result",
        )
        attach_evidence(session, case.id, asset.id)
        manifest = list_content_test_cases(session, item.id)
        assert manifest[0]["test_case_id"] == "TEST-1"
        assert manifest[0]["evidence_assets"][0]["asset_id"] == "asset-1"


def issue(rule_id="X", severity="LOW", **kwargs):
    return {"rule_id": rule_id, "severity": severity, "category": "text", **kwargs}


def test_arbiter_routes_required_outcomes():
    assert arbitrate_review([], [issue("EVIDENCE", "HIGH", human_required=True)]).review_status == ReviewStatus.HUMAN_REVIEW_REQUIRED
    assert arbitrate_review([], [issue("CLAIM", "CRITICAL")]).review_status == ReviewStatus.BLOCKED
    assert arbitrate_review([], [issue("TEXT", "MEDIUM")]).review_status == ReviewStatus.SUPPLIER_REVISION_REQUIRED
    low = issue("BRAND-REPLACE-001", "LOW", auto_fixable=True, category="brand")
    result = arbitrate_review([], [low])
    assert result.review_status == ReviewStatus.AUTO_FIX_PENDING
    assert result.ai_proposal_allowed is True
    assert arbitrate_review([], [], campaign_score=45, suggestions=["优化开头"]).review_status == ReviewStatus.PASSED_WITH_SUGGESTIONS
    assert arbitrate_review([{"decision": "PASS"}], []).review_status == ReviewStatus.HUMAN_REVIEW_REQUIRED
    assert arbitrate_review([{"agent_id": agent_id, "decision": "PASS"} for agent_id in (
        "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
        "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
    )], []).review_status == ReviewStatus.PASSED


def test_run_audit_prefers_exact_version_database_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from server.services import review_service

    with make_session(tmp_path) as session:
        item, first_version = make_content(session)
        second_version = ContentVersion(
            content_item=item, version=2, source="SUPPLIER_REVISION", title="标题", body="正文",
            payload={"test_cases": [{"test_case_id": "STALE", "command": "stale", "observed_result": "stale"}]},
        )
        session.add(second_version)
        session.flush()
        asset = create_asset(session, item.id, asset_id="asset-current", kind="SCREENSHOT", filename="current.png")
        current = create_test_case(
            session, item.id, second_version.id, external_test_case_id="CURRENT", claim="current claim",
            command="current command", observed_result="current result",
        )
        attach_evidence(session, current.id, asset.id)
        captured = {}
        monkeypatch.setattr(review_service, "evaluate_rules", lambda profile, context: captured.setdefault("context", context) and [])

        review_service.run_audit(session, item.id)

        assert [case["test_case_id"] for case in captured["context"].test_cases] == ["CURRENT"]
        assert [asset["asset_id"] for asset in captured["context"].evidence_assets] == ["asset-current"]
        assert first_version.id != second_version.id


def test_numeric_or_claim_low_issue_never_allows_ai_proposal():
    numeric = issue("QUALITY-001", "LOW", auto_fixable=True, evidence_quote="提升 30%", suggestion="提升 50%")
    claim = issue("CLAIM-001", "LOW", auto_fixable=True, evidence_quote="支持新功能", suggestion="支持全部功能")
    for candidate in (numeric, claim):
        result = arbitrate_review([], [candidate])
        assert result.review_status is not ReviewStatus.AUTO_FIX_PENDING
        assert result.ai_proposal_allowed is False
