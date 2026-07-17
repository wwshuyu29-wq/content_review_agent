from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import main
from server.db import create_db_engine
from scripts.text_review.reviewers.tech_media import AGENT_ORDER, TechMediaReviewer
from server.models import Asset, BatchAuditJob, ContentItem, Project, ReviewStatus, TestCase as EvidenceTestCase, TestEvidence as EvidenceBinding


class FakeReviewer:
    name = "api-fake-reviewer"

    def review_structured(self, row, standards):
        agent_ids = [
            "CONTENT_QUALITY", "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY",
            "CAMPAIGN_EFFECTIVENESS",
        ]
        return [
            {
                "agent_name": agent_id,
                "agent_id": agent_id,
                "agent_version": "tech-media-v1",
                "decision": "PASS_WITH_SUGGESTIONS" if agent_id == "CONTENT_QUALITY" else "PASS",
                "summary": "api test",
                "score": 90,
                "status": "COMPLETED",
                "issues": [],
                "raw_result": {"source": "fake"},
            }
            for agent_id in agent_ids
        ]

    def rewrite(self, row, standards):
        return "建议标题", "建议正文！"


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ONEAPI_KEY", "test-secret-key")
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "test-admin")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-with-at-least-32-bytes")
    monkeypatch.setenv("TRUSTED_PUBLIC_ORIGINS", "http://testserver")
    engine = create_db_engine(database_url)

    def test_session():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_session] = test_session
    main.app.dependency_overrides[main.get_audit_reviewer] = lambda: FakeReviewer()
    with TestClient(main.app) as client:
        authenticated = client.post(
            "/api/auth/login",
            json={"username": "test-admin", "password": "test-admin-password"},
        )
        assert authenticated.status_code == 200
        client.headers.update({
            "Origin": "http://testserver",
            "X-CSRF-Token": authenticated.json()["csrf_token"],
        })
        yield client, engine, tmp_path
    main.app.dependency_overrides.clear()
    engine.dispose()


def test_startup_seeds_project_and_supports_project_rule_workflow(api) -> None:
    client, _, _ = api

    projects = client.get("/api/projects")
    assert projects.status_code == 200
    seeded = projects.json()[0]
    assert seeded["name"] == "百度地图小度想想"
    assert seeded["code"] == "bdmap_xdxx_tech_review_2026"
    assert seeded["content_type"] == "TECH_MEDIA_REVIEW"

    detail = client.get(f"/api/projects/{seeded['id']}")
    assert detail.status_code == 200
    assert detail.json()["current_rule_version"]["version"] == 1

    created = client.post("/api/projects", json={"name": "API 测试项目", "description": "项目说明"})
    assert created.status_code == 201
    project = created.json()

    raw_payload = {
        "dimension_standards": {"quality": "不得使用重复标点"},
        "project_facts": {"product": "测试产品"},
        "structured_rules": {"deny_words": []},
        "prompt_version": "prompt-v1",
    }
    raw_rule = client.post(f"/api/projects/{project['id']}/rule-versions", json=raw_payload)
    assert raw_rule.status_code == 422

    package_rule = client.post(
        f"/api/projects/{seeded['id']}/rule-versions",
        json={"project_code": "bdmap_xdxx_tech_review_2026", "package_version": "1.3"},
    )
    assert package_rule.status_code == 200
    assert package_rule.json()["version"] == 1
    assert package_rule.json()["package_version"] == "1.3"

    versions = client.get(f"/api/projects/{seeded['id']}/rule-versions")
    assert versions.status_code == 200
    assert [entry["version"] for entry in versions.json()] == [1]


def test_full_http_flow_uploads_audits_resolves_and_reports(api) -> None:
    client, _, _ = api
    project = client.get("/api/projects").json()[0]
    contents = [
        {
            "external_id": "supplier-content-1",
            "title": "原始标题",
            "body": "原始正文最优解",
            "payload": {"platform": "小红书"},
        }
    ]

    uploaded = client.post(
        "/api/batches",
        data={
            "project_id": str(project["id"]),
            "supplier_id": "supplier-api",
            "name": "API 上传批次",
            "contents": json.dumps(contents, ensure_ascii=False),
        },
        files=[("files", ("cover.png", b"png-content", "image/png"))],
    )
    assert uploaded.status_code == 201
    batch = uploaded.json()
    assert batch["content_count"] == 1
    item = batch["contents"][0]
    assert item["format_status"] == "PASSED"
    assert item["versions"][0]["source"] == "SUPPLIER"
    assert item["versions"][0]["payload"]["media"]

    assert client.get(f"/api/media/{item['id']}").content == b"png-content"
    listed = client.get("/api/contents", params={"project_id": project["id"], "batch_id": batch["id"]})
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()] == [item["id"]]

    audited = client.post(f"/api/contents/{item['id']}/audit")
    assert audited.status_code == 200
    assert audited.json()["status"] == "COMPLETED"

    detail = client.get(f"/api/contents/{item['id']}")
    assert detail.status_code == 200
    content = detail.json()
    assert content["review_status"] == "AUTO_FIX_PENDING"
    assert [version["source"] for version in content["versions"]] == ["SUPPLIER", "AI_PROPOSED"]
    assert "raw_result" not in content["latest_audit"]["agent_results"][0]
    assert content["latest_audit"]["issues"][0]["rule_id"] == "REPLACE-001"
    task = content["open_tasks"][0]
    assert task["task_type"] == "AUTO_FIX_PROPOSAL"
    assert task["issue_ids"]

    tasks = client.get("/api/review-tasks", params={"status": "OPEN", "project_id": project["id"]})
    assert tasks.status_code == 200
    assert [entry["id"] for entry in tasks.json()] == [task["id"]]

    resolved = client.post(
        f"/api/review-tasks/{task['id']}/resolve",
        json={"decision": "ACCEPT_AUTO_FIX", "reviewer": "owner@example.com", "note": "确认"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["decision"] == "ACCEPT_AUTO_FIX"

    final_content = client.get(f"/api/contents/{item['id']}").json()
    assert final_content["review_status"] == "PASSED"
    assert final_content["publish_status"] == "READY"
    assert final_content["versions"][-1]["source"] == "HUMAN_CONFIRMED"
    assert final_content["open_tasks"] == []

    report = client.get("/api/reports", params={"project_id": project["id"], "batch_id": batch["id"]})
    assert report.status_code == 200
    assert report.json()["totals"] == {"contents": 1, "issues": 0, "tasks": 0}
    assert report.json()["historical_totals"] == {"issues": 1, "tasks": 1}
    assert report.json()["status_counts"] == {"PASSED": 1}


def test_dashboard_overview_groups_team_workload_quality_and_issue_clusters(api) -> None:
    from server.models import (
        AgentResult,
        AuditRun,
        Batch,
        ContentVersion,
        HumanDecision,
        Issue,
        ReviewTask,
        User,
    )
    from server.services.auth_service import hash_password

    client, engine, _ = api
    project = client.get("/api/projects").json()[0]
    recorded_at = datetime(2026, 7, 8, 10, 0, 0)
    with Session(engine) as session:
        admin = session.scalar(select(User).where(User.username == "test-admin"))
        member = User(
            username="reviewer-a",
            display_name="审核同学 A",
            password_hash=hash_password("reviewer-a password"),
            role="REVIEWER",
        )
        session.add(member)
        session.flush()

        batch = Batch(
            project_id=project["id"],
            supplier_id="internal",
            name="7 月科技稿件",
            uploaded_by_user_id=admin.id,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(batch)
        session.flush()
        first = ContentItem(
            project_id=project["id"],
            batch_id=batch.id,
            external_id="draft-1",
            title="内容准确性问题稿件",
            format_status="PASSED",
            review_status=ReviewStatus.PASSED,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        second = ContentItem(
            project_id=project["id"],
            batch_id=batch.id,
            external_id="draft-2",
            title="品牌表达问题稿件",
            format_status="PASSED",
            review_status=ReviewStatus.HUMAN_REVIEW_REQUIRED,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add_all([first, second])
        session.flush()
        first_version = ContentVersion(content_item_id=first.id, version=1, source="SUPPLIER", title=first.title, body="正文", payload={})
        second_version = ContentVersion(content_item_id=second.id, version=1, source="SUPPLIER", title=second.title, body="正文", payload={})
        session.add_all([first_version, second_version])
        session.flush()
        audit = AuditRun(
            content_item_id=second.id,
            content_version_id=second_version.id,
            rule_version_id=project["current_rule_version_id"],
            model="GPT 5.6 SOL",
            prompt_version="test",
            status="COMPLETED",
            created_by_user_id=member.id,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(audit)
        session.flush()
        agent = AgentResult(audit_run_id=audit.id, agent_name="品牌", agent_id="BRAND", agent_version="v1", decision="REJECT", summary="品牌问题", score=60, status="COMPLETED")
        session.add(agent)
        session.flush()
        issue = Issue(
            audit_run_id=audit.id,
            agent_result_id=agent.id,
            rule_id="BRAND-1",
            category="BRAND",
            severity="HIGH",
            field="body",
            evidence_quote="错误表述",
            source_reference=[],
            reason="品牌表达不一致",
            suggestion="改成标准表述",
            auto_fixable=False,
            human_required=True,
            confidence=0.9,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(issue)
        session.add(
            Issue(
                audit_run_id=audit.id,
                agent_result_id=None,
                rule_id="TEST-EVIDENCE-001",
                category="deterministic",
                severity="HIGH",
                field="body",
                evidence_quote="亲测",
                source_reference=[],
                reason="出现实测触发词，但缺少结构化证据字段或测试条件：test_cases, evidence",
                suggestion="补充证据",
                auto_fixable=False,
                human_required=True,
                confidence=1.0,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        )
        session.add(
            Issue(
                audit_run_id=audit.id,
                agent_result_id=None,
                rule_id="CLAIM-UNSUPPORTED-ABSOLUTE-001",
                category="deterministic",
                severity="MEDIUM",
                field="body",
                evidence_quote="所有场景都能支持",
                source_reference=[],
                reason="命中缺少所提供依据的绝对、保证或能力比较表述",
                suggestion="改为有边界的观察",
                auto_fixable=False,
                human_required=False,
                confidence=1.0,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        )
        session.add(
            Issue(
                audit_run_id=audit.id,
                agent_result_id=None,
                rule_id="CLAIM-PENDING-001",
                category="deterministic",
                severity="MEDIUM",
                field="body",
                evidence_quote="稳定覆盖所有场景",
                source_reference=[],
                reason="命中缺少所提供依据的绝对、保证或能力比较表述",
                suggestion="去掉无边界能力承诺",
                auto_fixable=False,
                human_required=False,
                confidence=1.0,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        )
        session.add(
            Issue(
                audit_run_id=audit.id,
                agent_result_id=None,
                rule_id="SYSTEM-LLM-UNAVAILABLE",
                category="system",
                severity="HIGH",
                field="review",
                evidence_quote="",
                source_reference=[],
                reason="模型审核暂时不可用",
                suggestion="检查模型配置",
                auto_fixable=False,
                human_required=True,
                confidence=1.0,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        )
        session.flush()
        task = ReviewTask(
            content_item_id=second.id,
            target_content_version_id=second_version.id,
            audit_run_id=audit.id,
            issue_id=issue.id,
            task_type="HUMAN_REVIEW",
            status="CLOSED",
        )
        session.add(task)
        session.flush()
        session.add(HumanDecision(review_task_id=task.id, decision="HUMAN_REJECT", reviewer="审核同学 A", reviewer_user_id=member.id, payload={}, created_at=recorded_at, updated_at=recorded_at))
        session.commit()

    response = client.get("/api/dashboard/overview", params={"month": "2026-07"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-07"
    assert payload["quality"]["total_count"] == 2
    assert payload["quality"]["passed_count"] == 1
    assert payload["quality"]["pass_rate"] == 0.5
    assert payload["monthly_reviews"][-1] == {"month": "2026-07", "reviewed_count": 1}
    assert payload["supplier_quality"] == [
        {
            "supplier_name": "7 月科技稿件",
            "project_names": ["7 月科技稿件"],
            "total_count": 2,
            "passed_count": 2,
            "pass_rate": 1.0,
        }
    ]
    assert payload["workload"][0]["display_name"] == "test-admin"
    assert payload["workload"][0]["months"][0]["uploaded_count"] == 2
    reviewer_row = next(row for row in payload["workload"] if row["username"] == "reviewer-a")
    assert reviewer_row["months"][0]["audit_started_count"] == 1
    assert reviewer_row["months"][0]["human_decision_count"] == 1
    assert {cluster["category"] for cluster in payload["issue_clusters"]} == {"BRAND", "COMPLIANCE"}
    assert "deterministic" not in {cluster["category"] for cluster in payload["issue_clusters"]}
    assert "CLAIM-UNSUPPORTED-ABSOLUTE-001" not in {cluster["category"] for cluster in payload["issue_clusters"]}
    assert "TEST-EVIDENCE-001" not in {cluster["category"] for cluster in payload["issue_clusters"]}
    brand_cluster = next(cluster for cluster in payload["issue_clusters"] if cluster["category"] == "BRAND")
    claim_cluster = next(cluster for cluster in payload["issue_clusters"] if cluster["category"] == "COMPLIANCE")
    assert brand_cluster["issue_count"] == 1
    assert brand_cluster["manuscripts"][0]["title"] == "品牌表达问题稿件"
    assert claim_cluster["issue_count"] == 1
    assert claim_cluster["manuscript_count"] == 1


def test_content_test_cases_endpoint_returns_bound_evidence(api) -> None:
    from server.services.evidence_service import attach_evidence, create_asset, create_test_case

    client, engine, _ = api
    project = client.get("/api/projects").json()[0]
    uploaded = client.post(
        "/api/batches",
        data={
            "project_id": str(project["id"]),
            "supplier_id": "evidence-supplier",
            "name": "evidence-batch",
            "contents": json.dumps([{"external_id": "evidence-content", "title": "实测标题", "body": "实测正文"}]),
        },
        files=[("files", ("cover.png", b"png-content", "image/png"))],
    ).json()
    item = uploaded["contents"][0]
    with Session(engine) as session:
        asset = create_asset(
            session, item["id"], asset_id="proof-1", kind="SCREENSHOT", filename="proof.png",
            mime_type="image/png", size_bytes=128,
        )
        case = create_test_case(
            session, item["id"], item["versions"][0]["id"], external_test_case_id="TEST-1",
            claim="路线生成成功", command="输入起终点", observed_result="返回三条路线", city="北京",
        )
        binding = attach_evidence(session, case.id, asset.id)
        session.flush()
        case_id, asset_db_id, binding_id = case.id, asset.id, binding.id
        session.commit()

    response = client.get(f"/api/contents/{item['id']}/test-cases")

    assert response.status_code == 200
    assert response.json() == [{
        "id": case_id,
        "content_item_id": item["id"],
        "content_version_id": item["versions"][0]["id"],
        "external_test_case_id": "TEST-1",
        "claim": "路线生成成功",
        "command": "输入起终点",
        "observed_result": "返回三条路线",
        "city": "北京",
        "tested_at": None,
        "app_version": None,
        "device": None,
        "operating_system": None,
        "network_environment": None,
        "test_metadata": {},
        "evidence": [{
            "id": binding_id,
            "test_case_id": case_id,
            "asset_id": asset_db_id,
            "asset": {
                "id": asset_db_id,
                "content_item_id": item["id"],
                "asset_id": "proof-1",
                "external_id": None,
                "kind": "SCREENSHOT",
                "filename": "proof.png",
                "storage_key": None,
                "mime_type": "image/png",
                "size_bytes": 128,
                "asset_metadata": {},
                "created_at": response.json()[0]["evidence"][0]["asset"]["created_at"],
            },
        }],
    }]


def test_content_test_cases_endpoint_excludes_cross_content_version_and_asset(api) -> None:
    client, engine, _ = api
    project = client.get("/api/projects").json()[0]
    uploaded = []
    for index in (1, 2):
        response = client.post(
            "/api/batches",
            data={
                "project_id": str(project["id"]),
                "supplier_id": f"ownership-supplier-{index}",
                "name": f"ownership-batch-{index}",
                "contents": json.dumps([{"external_id": f"ownership-content-{index}", "title": "标题", "body": "正文"}]),
            },
            files=[("files", ("cover.png", b"png-content", "image/png"))],
        )
        uploaded.append(response.json()["contents"][0])
    first, second = uploaded
    with Session(engine) as session:
        asset = Asset(content_item_id=second["id"], asset_id="foreign-asset", kind="SCREENSHOT", filename="foreign.png")
        corrupt_case = EvidenceTestCase(
            content_item_id=first["id"], content_version_id=second["versions"][0]["id"],
            external_test_case_id="CORRUPT-1", claim="foreign", command="foreign", observed_result="foreign",
        )
        session.add_all([asset, corrupt_case])
        session.flush()
        session.add(EvidenceBinding(test_case=corrupt_case, asset=asset))
        session.commit()

    response = client.get(f"/api/contents/{first['id']}/test-cases")

    assert response.status_code == 200
    assert response.json() == []


def test_default_heuristic_audit_routes_to_human_review(api) -> None:
    client, _, _ = api
    main.app.dependency_overrides[main.get_audit_reviewer] = main.get_audit_reviewer
    project_id = client.get("/api/projects").json()[0]["id"]
    uploaded = client.post(
        "/api/batches",
        data={
            "project_id": str(project_id),
            "supplier_id": "heuristic-safety",
            "name": "heuristic safety",
            "contents": json.dumps([{"external_id": "heuristic", "title": "标题", "body": "正文", "payload": {}}]),
        },
        files=[("files", ("heuristic.png", b"image", "image/png"))],
    ).json()
    content_id = uploaded["contents"][0]["id"]

    response = client.post(f"/api/contents/{content_id}/audit")

    assert response.status_code == 200
    assert response.json()["agent_results"][0]["decision"] == "HUMAN_REVIEW"
    detail = client.get(f"/api/contents/{content_id}").json()
    assert detail["review_status"] == "HUMAN_REVIEW_REQUIRED"
    assert detail["publish_status"] == "NOT_READY"
    assert len(detail["open_tasks"]) == 1


def test_batch_audit_endpoint_audits_all_eligible_contents(api) -> None:
    client, _, _ = api
    project_id = client.get("/api/projects").json()[0]["id"]
    uploaded = client.post(
        "/api/batches",
        data={
            "project_id": str(project_id),
            "supplier_id": "supplier-batch",
            "name": "批量审核",
            "contents": json.dumps(
                [
                    {"external_id": "one", "title": "标题一", "body": "正文一", "payload": {}},
                    {"external_id": "two", "title": "标题二", "body": "正文二", "payload": {}},
                ]
            ),
        },
        files=[
            ("files", ("one.png", b"one", "image/png")),
            ("files", ("two.png", b"two", "image/png")),
        ],
    )
    batch_id = uploaded.json()["id"]

    response = client.post(f"/api/batches/{batch_id}/audit")

    assert response.status_code == 200
    assert response.json()["audited"] == 2
    assert [result["status"] for result in response.json()["results"]] == ["success", "success"]
    assert len(client.get("/api/audit-runs", params={"batch_id": batch_id}).json()) == 2


def test_project_rule_and_config_reject_invalid_string_values(api) -> None:
    client, _, _ = api
    project_id = client.get("/api/projects").json()[0]["id"]
    rule_payload = {
        "dimension_standards": {},
        "project_facts": {},
        "structured_rules": {},
        "prompt_version": "   ",
    }

    assert client.post("/api/projects", json={"name": "   "}).status_code == 422
    assert client.post(
        f"/api/projects/{project_id}/rule-versions", json=rule_payload
    ).status_code == 422

    invalid_configs = (
        {"reviewer": "   "},
        {"reviewer": "unsupported"},
        {"reviewer": "oneapi"},
        {"reviewer": "oneapi", "model": "   "},
    )
    for config in invalid_configs:
        assert client.put("/api/config", json=config).status_code == 422


def test_config_strips_valid_values(api) -> None:
    client, _, _ = api

    response = client.put(
        "/api/config",
        json={"reviewer": " oneapi ", "model": " configured-model "},
    )

    assert response.status_code == 200
    assert response.json()["reviewer"] == "oneapi"
    assert response.json()["model"] == "configured-model"


def test_reviewer_dependency_rejects_invalid_saved_config(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, tmp_path = api
    config_path = tmp_path / "data" / "config.json"
    config_path.write_text(
        json.dumps({"reviewer": "unsupported", "model": "model"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reviewer"):
        main.get_audit_reviewer()

    config_path.write_text(
        json.dumps({"reviewer": "oneapi", "model": "   "}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="model"):
        main.get_audit_reviewer()


def test_reviewer_dependency_applies_non_secret_config(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.models import User

    client, _, _ = api
    monkeypatch.setenv("ONEAPI_BASE_URL", "https://trusted.example.test/v1")
    client.put(
        "/api/config",
        json={
            "reviewer": "oneapi",
            "model": "configured-model",
            "api_key": "configured-key",
        },
    )
    captured = {}

    class FakeLLM:
        name = "oneapi-test"

    def fake_llm_factory(backend, **kwargs):
        captured.update(
            backend=backend,
            model=kwargs.get("model"),
            base_url=os.environ.get("ONEAPI_BASE_URL"),
            key=kwargs.get("api_key"),
        )
        return FakeLLM()

    monkeypatch.setattr(main, "get_llm", fake_llm_factory)

    with Session(api[1]) as session:
        user = session.scalar(select(User).where(User.username == "test-admin"))
        reviewer = main.get_audit_reviewer_for_user(user)

    assert isinstance(reviewer, TechMediaReviewer)
    assert isinstance(reviewer.llm, FakeLLM)
    assert captured == {
        "backend": "oneapi",
        "model": "configured-model",
        "base_url": "https://trusted.example.test/v1",
        "key": "configured-key",
    }


def test_heuristic_dependency_injects_tech_media_reviewer_without_llm(api) -> None:
    reviewer = main.get_audit_reviewer()
    assert isinstance(reviewer, TechMediaReviewer)
    assert reviewer.llm is None


def test_config_accepts_api_key_without_exposing_secret_or_legacy_key_fields(api) -> None:
    client, _, _ = api

    initial = client.get("/api/config")
    assert initial.status_code == 200
    assert initial.json()["key_set"] is True
    assert "ONEAPI_KEY" not in initial.text
    assert "test-secret-key" not in initial.text

    updated = client.put(
        "/api/config",
        json={
            "reviewer": "oneapi",
            "model": "GPT 5.6 SOL",
            "api_key": "must-not-be-exposed",
        },
    )
    assert updated.status_code == 200
    assert updated.json() == {"reviewer": "oneapi", "model": "GPT 5.6 SOL", "key_set": True}
    assert "must-not-be-exposed" not in updated.text

    rejected = client.put(
        "/api/config",
        json={
            "reviewer": "multi-agent",
            "model": "review-model",
            "base_url": "https://oneapi.example.test",
            "oneapi_key": "must-not-be-stored",
        },
    )
    assert rejected.status_code == 422
    assert "must-not-be-stored" not in rejected.text
    assert "base_url" not in initial.json()


def test_rejected_content_audit_returns_conflict_without_reviving_content(api) -> None:
    client, engine, _ = api
    project_id = client.get("/api/projects").json()[0]["id"]
    uploaded = client.post(
        "/api/batches",
        data={
            "project_id": str(project_id),
            "supplier_id": "rejected",
            "name": "rejected",
            "contents": json.dumps([
                {"external_id": "rejected", "title": "title", "body": "body", "payload": {}}
            ]),
        },
        files=[("files", ("rejected.png", b"image", "image/png"))],
    ).json()
    content_id = uploaded["contents"][0]["id"]

    with Session(engine) as session:
        item = session.get(ContentItem, content_id)
        assert item is not None
        item.review_status = ReviewStatus.REJECTED
        session.commit()

    response = client.post(f"/api/contents/{content_id}/audit")

    assert response.status_code == 409
    assert response.json()["detail"] == "当前内容状态不允许重复审核，请刷新后重试。"
    detail = client.get(f"/api/contents/{content_id}").json()
    assert detail["review_status"] == "REJECTED"
    assert detail["latest_audit"] is None


def test_audit_conflict_and_batch_partial_results_are_explicit(api) -> None:
    client, _, _ = api
    project_id = client.get("/api/projects").json()[0]["id"]
    uploaded = client.post(
        "/api/batches",
        data={
            "project_id": str(project_id),
            "supplier_id": "partial",
            "name": "partial",
            "contents": json.dumps([
                {"external_id": "ok", "title": "title", "body": "body", "payload": {}},
                {"external_id": "bad", "title": "", "body": "body", "payload": {}},
            ]),
        },
        files=[
            ("files", ("ok.png", b"ok", "image/png")),
            ("files", ("bad.png", b"bad", "image/png")),
        ],
    ).json()
    first_id = next(item["id"] for item in uploaded["contents"] if item["external_id"] == "ok")
    assert client.post(f"/api/contents/{first_id}/audit").status_code == 200
    assert client.post(f"/api/contents/{first_id}/audit").status_code == 409

    response = client.post(f"/api/batches/{uploaded['id']}/audit")
    assert response.status_code == 200
    assert {entry["status"] for entry in response.json()["results"]} == {"error"}
    assert all(entry.get("error") for entry in response.json()["results"])


def test_upload_requires_one_valid_file_per_row_and_enforces_size(api) -> None:
    client, _, tmp_path = api
    project_id = client.get("/api/projects").json()[0]["id"]
    data = {
        "project_id": str(project_id),
        "supplier_id": "upload",
        "name": "upload",
        "contents": json.dumps([
            {"external_id": "a", "title": "a", "body": "a", "payload": {}},
            {"external_id": "b", "title": "b", "body": "b", "payload": {}},
        ]),
    }
    assert client.post("/api/batches", data=data).status_code == 422
    mismatch = client.post(
        "/api/batches", data=data, files=[("files", ("a.png", b"png", "image/png"))]
    )
    assert mismatch.status_code == 422
    one = {
        **data,
        "contents": json.dumps([{"external_id": "c", "title": "c", "body": "c", "payload": {}}]),
    }
    assert client.post(
        "/api/batches", data=one, files=[("files", ("c.png", b"png", "text/plain"))]
    ).status_code == 422
    response = client.post(
        "/api/batches",
        data=one,
        files=[("files", ("c.png", b"x" * (20 * 1024 * 1024 + 1), "image/png"))],
    )
    assert response.status_code == 413
    assert list((tmp_path / "data" / "uploads").iterdir()) == []


def test_not_found_validation_and_failed_batch_transaction(api) -> None:
    client, engine, _ = api
    project_id = client.get("/api/projects").json()[0]["id"]

    assert client.get("/api/projects/999999").status_code == 404
    assert client.get("/api/contents/999999").status_code == 404
    assert client.post("/api/contents/999999/audit").status_code == 404
    assert client.get("/api/reports", params={"project_id": 999999}).status_code == 404
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    assert client.post(
        "/api/batches",
        data={
            "project_id": str(project_id),
            "supplier_id": "supplier-invalid",
            "name": "错误 JSON",
            "contents": "not-json",
        },
    ).status_code == 422

    duplicate_batch = client.post(
        "/api/batches",
        data={
            "project_id": str(project_id),
            "supplier_id": "supplier-duplicate",
            "name": "重复内容",
            "contents": json.dumps(
                [
                    {"external_id": "duplicate", "title": "标题一", "body": "正文一", "payload": {}},
                    {"external_id": "duplicate", "title": "标题二", "body": "正文二", "payload": {}},
                ]
            ),
        },
        files=[
            ("files", ("duplicate-1.png", b"one", "image/png")),
            ("files", ("duplicate-2.png", b"two", "image/png")),
        ],
    )
    assert duplicate_batch.status_code == 409

    with Session(engine) as session:
        assert session.scalar(select(ContentItem).where(ContentItem.external_id == "duplicate")) is None
        assert session.scalars(select(Project)).all()

    healthy = client.get("/api/health")
    assert healthy.status_code == 200
    assert healthy.json()["ok"] is True


def _create_audit_job_batch(client: TestClient, suffix: str = "one") -> dict:
    project_id = client.get("/api/projects").json()[0]["id"]
    return client.post(
        "/api/batches",
        data={
            "project_id": str(project_id), "supplier_id": f"job-api-{suffix}", "name": f"后台审核 {suffix}",
            "contents": json.dumps([
                {"external_id": f"job-{suffix}-1", "title": "标题一", "body": "正文一", "payload": {}},
                {"external_id": f"job-{suffix}-2", "title": "标题二", "body": "正文二", "payload": {}},
            ]),
        },
        files=[
            ("files", (f"job-{suffix}-1.png", b"one", "image/png")),
            ("files", (f"job-{suffix}-2.png", b"two", "image/png")),
        ],
    ).json()


def test_audit_job_endpoints_start_immediately_reuse_and_restore_progress(api, monkeypatch) -> None:
    client, engine, _ = api
    batch = _create_audit_job_batch(client)
    submitted = []

    def capture_submission(job_id: int) -> None:
        with Session(engine) as independent_session:
            assert independent_session.get(BatchAuditJob, job_id).status == "QUEUED"
        submitted.append(job_id)

    monkeypatch.setattr(main, "submit_audit_job", capture_submission, raising=False)
    first = client.post(f"/api/batches/{batch['id']}/audit-jobs")
    duplicate = client.post(f"/api/batches/{batch['id']}/audit-jobs")
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json() == {
        "job_id": first.json()["job_id"], "batch_id": batch["id"], "status": "QUEUED",
    }
    assert submitted == [first.json()["job_id"]]

    progress = client.get(f"/api/audit-jobs/{first.json()['job_id']}")
    restored = client.get(f"/api/batches/{batch['id']}/audit-job")
    assert progress.status_code == restored.status_code == 200
    assert restored.json() == progress.json()
    payload = progress.json()
    assert (payload["total_count"], payload["pending_count"], payload["running_count"]) == (2, 2, 0)
    assert [row["position"] for row in payload["manuscripts"]] == [1, 2]
    assert len(payload["manuscripts"][0]["agents"]) == len(AGENT_ORDER)
    assert payload["current_agents"] == []


def test_audit_job_endpoints_require_authentication_and_start_requires_csrf(api) -> None:
    client, _, _ = api
    batch = _create_audit_job_batch(client, "security")
    with TestClient(main.app) as unauthenticated:
        assert unauthenticated.post(f"/api/batches/{batch['id']}/audit-jobs").status_code == 401
        assert unauthenticated.get("/api/audit-jobs/999999").status_code == 401
        assert unauthenticated.get(f"/api/batches/{batch['id']}/audit-job").status_code == 401
    csrf_token = client.headers.pop("X-CSRF-Token")
    try:
        assert client.post(f"/api/batches/{batch['id']}/audit-jobs").status_code == 403
    finally:
        client.headers["X-CSRF-Token"] = csrf_token


def test_audit_job_reads_enforce_batch_relationship_and_hide_raw_errors(api, monkeypatch) -> None:
    client, engine, _ = api
    owned_batch = _create_audit_job_batch(client, "owned")
    other_batch = _create_audit_job_batch(client, "other")
    monkeypatch.setattr(main, "submit_audit_job", lambda _job_id: None, raising=False)
    started = client.post(f"/api/batches/{owned_batch['id']}/audit-jobs").json()
    technical_error = "POST https://gateway.internal Authorization: Bearer sk-secret raw response Traceback RuntimeError"
    with Session(engine) as session:
        job = session.get(BatchAuditJob, started["job_id"])
        job.error_summary = technical_error
        job.manuscripts[0].error_summary = technical_error
        job.manuscripts[0].agents[0].error_summary = technical_error
        session.commit()

    assert client.get(f"/api/batches/{other_batch['id']}/audit-job").json() is None
    assert client.get("/api/audit-jobs/999999").status_code == 404
    payload = client.get(f"/api/audit-jobs/{started['job_id']}").json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["batch_id"] == owned_batch["id"]
    assert payload["error_summary"] == "审核过程中出现异常，请稍后重试或联系管理员。"
    for secret in ("https://", "sk-secret", "raw response", "Traceback", "RuntimeError"):
        assert secret not in serialized


def test_service_error_response_hides_raw_value_error_details(api, monkeypatch) -> None:
    client, _, _ = api
    batch = _create_audit_job_batch(client, "service-error")
    technical_error = "invalid content 987654 at https://gateway.internal/v1; raw body=secret; Traceback RuntimeError"

    def raise_technical_error(*_args, **_kwargs):
        raise ValueError(technical_error)

    monkeypatch.setattr(main, "create_or_get_active_job", raise_technical_error)
    response = client.post(f"/api/batches/{batch['id']}/audit-jobs")

    assert response.status_code == 422
    assert response.json()["detail"] == "提交信息有误，请检查后重试。"
    assert technical_error not in response.text
    for secret in ("987654", "https://gateway.internal", "secret", "Traceback", "RuntimeError"):
        assert secret not in response.text


def test_concurrent_audit_job_starts_submit_only_the_created_job(api, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    client, engine, _ = api
    batch = _create_audit_job_batch(client, "concurrent")
    enter_service = Barrier(2)
    submission_lock = Lock()
    submissions = []
    create_job = main.create_or_get_active_job

    def synchronized_create_job(session, batch_id, model):
        enter_service.wait(timeout=5)
        return create_job(session, batch_id, model)

    def capture_submission(job_id: int) -> None:
        with submission_lock:
            if submissions:
                raise RuntimeError("duplicate submission must not reach recovery")
            submissions.append(job_id)

    monkeypatch.setattr(main, "create_or_get_active_job", synchronized_create_job)
    monkeypatch.setattr(main, "submit_audit_job", capture_submission)

    def start_job(_index: int):
        return client.post(f"/api/batches/{batch['id']}/audit-jobs")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(start_job, range(2)))

    assert [response.status_code for response in responses] == [202, 202]
    job_ids = {response.json()["job_id"] for response in responses}
    assert len(job_ids) == 1
    assert submissions == [job_ids.pop()]
    with Session(engine) as session:
        jobs = list(session.scalars(select(BatchAuditJob).where(BatchAuditJob.batch_id == batch["id"])))
        assert len(jobs) == 1
        assert jobs[0].status == "QUEUED"
