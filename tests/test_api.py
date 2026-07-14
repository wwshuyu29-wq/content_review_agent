from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import main
from server.db import create_db_engine
from scripts.text_review.reviewers.tech_media import TechMediaReviewer
from server.models import ContentItem, Project, ReviewStatus


class FakeReviewer:
    name = "api-fake-reviewer"

    def review_structured(self, row, standards):
        agent_ids = [
            "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY",
            "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS",
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
                "issues": [{
                    "rule_id": "QUALITY-API-001", "category": "quality", "severity": "low",
                    "field": "body", "evidence_quote": "！！！", "reason": "重复标点",
                    "suggestion": "！", "source_reference": ["content_quality.md"],
                    "auto_fixable": True, "human_required": False, "confidence": 0.98,
                }] if agent_id == "CONTENT_QUALITY" else [],
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
    engine = create_db_engine(database_url)

    def test_session():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_session] = test_session
    main.app.dependency_overrides[main.get_audit_reviewer] = lambda: FakeReviewer()
    with TestClient(main.app) as client:
        yield client, engine, tmp_path
    main.app.dependency_overrides.clear()
    engine.dispose()


def test_startup_seeds_project_and_supports_project_rule_workflow(api) -> None:
    client, _, _ = api

    projects = client.get("/api/projects")
    assert projects.status_code == 200
    seeded = projects.json()[0]
    assert seeded["name"] == "百度地图小度想想科技媒体测评"
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
        json={"project_code": "bdmap_xdxx_tech_review_2026", "package_version": "0.9"},
    )
    assert package_rule.status_code == 200
    assert package_rule.json()["version"] == 1
    assert package_rule.json()["package_version"] == "0.9"

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
            "body": "原始正文！！！",
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
    assert content["latest_audit"]["agent_results"][0]["raw_result"] == {"source": "fake"}
    assert content["latest_audit"]["issues"][0]["rule_id"] == "QUALITY-API-001"
    task = content["open_tasks"][0]
    assert task["task_type"] == "AUTO_FIX_PROPOSAL"

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
    assert report.json()["totals"] == {"contents": 1, "issues": 1, "tasks": 0}
    assert report.json()["historical_totals"] == {"issues": 1, "tasks": 1}
    assert report.json()["status_counts"] == {"PASSED": 1}


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
    client, _, _ = api
    monkeypatch.setenv("ONEAPI_BASE_URL", "https://trusted.example.test/v1")
    client.put(
        "/api/config",
        json={
            "reviewer": "oneapi",
            "model": "configured-model",
        },
    )
    captured = {}

    class FakeLLM:
        name = "oneapi-test"

    def fake_llm_factory(backend):
        captured.update(
            backend=backend,
            model=os.environ.get("ONEAPI_MODEL"),
            base_url=os.environ.get("ONEAPI_BASE_URL"),
            key=os.environ.get("ONEAPI_KEY"),
        )
        return FakeLLM()

    monkeypatch.setattr(main, "get_llm", fake_llm_factory)

    reviewer = main.get_audit_reviewer()

    assert isinstance(reviewer, TechMediaReviewer)
    assert isinstance(reviewer.llm, FakeLLM)
    assert captured == {
        "backend": "oneapi",
        "model": "configured-model",
        "base_url": "https://trusted.example.test/v1",
        "key": "test-secret-key",
    }


def test_heuristic_dependency_injects_tech_media_reviewer_without_llm(api) -> None:
    reviewer = main.get_audit_reviewer()
    assert isinstance(reviewer, TechMediaReviewer)
    assert reviewer.llm is None


def test_config_never_exposes_or_accepts_oneapi_key(api) -> None:
    client, _, _ = api

    initial = client.get("/api/config")
    assert initial.status_code == 200
    assert initial.json()["key_set"] is True
    assert "ONEAPI_KEY" not in initial.text
    assert "test-secret-key" not in initial.text

    updated = client.put(
        "/api/config",
        json={
            "reviewer": "multi-agent",
            "model": "review-model",
            "base_url": "https://oneapi.example.test",
            "oneapi_key": "must-not-be-stored",
        },
    )
    assert updated.status_code == 422
    assert "must-not-be-stored" not in updated.text
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
    assert "terminal" in response.json()["detail"]
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
