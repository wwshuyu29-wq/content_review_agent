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
from server.models import ContentItem, Project


class FakeReviewer:
    name = "api-fake-reviewer"

    def review_structured(self, row, standards):
        return [
            {
                "agent_name": "quality",
                "status": "COMPLETED",
                "issues": [
                    {
                        "rule_id": "QUALITY-API-001",
                        "category": "quality",
                        "severity": "low",
                        "field": "body",
                        "evidence_quote": "！！！",
                        "reason": "重复标点",
                        "suggestion": "改为单个感叹号",
                        "auto_fixable": True,
                        "human_required": False,
                        "confidence": 0.98,
                    }
                ],
                "raw_result": {"source": "fake"},
            }
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
    assert seeded["name"] == "百度地图小度想想 × 范丞丞短期合作"

    detail = client.get(f"/api/projects/{seeded['id']}")
    assert detail.status_code == 200
    assert detail.json()["current_rule_version"]["version"] == 1

    created = client.post("/api/projects", json={"name": "API 测试项目", "description": "项目说明"})
    assert created.status_code == 201
    project = created.json()

    rule_payload = {
        "dimension_standards": {"quality": "不得使用重复标点"},
        "project_facts": {"product": "测试产品"},
        "structured_rules": {"deny_words": []},
        "prompt_version": "prompt-v1",
    }
    rule = client.post(f"/api/projects/{project['id']}/rule-versions", json=rule_payload)
    assert rule.status_code == 201
    assert rule.json()["version"] == 1
    assert client.post(
        f"/api/projects/{project['id']}/rule-versions/{rule.json()['id']}/publish"
    ).status_code == 200

    versions = client.get(f"/api/projects/{project['id']}/rule-versions")
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
    assert content["review_status"] == "FIX_PROPOSED"
    assert [version["source"] for version in content["versions"]] == ["SUPPLIER", "AI_PROPOSED"]
    assert content["latest_audit"]["agent_results"][0]["raw_result"] == {"source": "fake"}
    assert content["latest_audit"]["issues"][0]["rule_id"] == "QUALITY-API-001"
    task = content["open_tasks"][0]
    assert task["task_type"] == "REVIEW_FIX_PROPOSAL"

    tasks = client.get("/api/review-tasks", params={"status": "OPEN", "project_id": project["id"]})
    assert tasks.status_code == 200
    assert [entry["id"] for entry in tasks.json()] == [task["id"]]

    resolved = client.post(
        f"/api/review-tasks/{task['id']}/resolve",
        json={"decision": "ACCEPT_SUGGESTION", "reviewer": "owner@example.com", "note": "确认"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["decision"] == "ACCEPT_SUGGESTION"

    final_content = client.get(f"/api/contents/{item['id']}").json()
    assert final_content["review_status"] == "APPROVED"
    assert final_content["publish_status"] == "READY"
    assert final_content["versions"][-1]["source"] == "HUMAN_CONFIRMED"
    assert final_content["open_tasks"] == []

    report = client.get("/api/reports", params={"project_id": project["id"], "batch_id": batch["id"]})
    assert report.status_code == 200
    assert report.json()["totals"] == {"contents": 1, "issues": 1, "tasks": 1}
    assert report.json()["status_counts"] == {"APPROVED": 1}


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
    )
    batch_id = uploaded.json()["id"]

    response = client.post(f"/api/batches/{batch_id}/audit")

    assert response.status_code == 200
    assert response.json()["audited"] == 2
    assert len(client.get("/api/audit-runs", params={"batch_id": batch_id}).json()) == 2


def test_reviewer_dependency_applies_non_secret_config(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = api
    client.put(
        "/api/config",
        json={
            "reviewer": "oneapi",
            "model": "configured-model",
            "base_url": "https://configured.example.test/v1",
        },
    )
    captured = {}

    def fake_factory(backend):
        captured.update(
            backend=backend,
            model=os.environ.get("ONEAPI_MODEL"),
            base_url=os.environ.get("ONEAPI_BASE_URL"),
            key=os.environ.get("ONEAPI_KEY"),
        )
        return FakeReviewer()

    monkeypatch.setattr(main, "get_reviewer", fake_factory)

    reviewer = main.get_audit_reviewer()

    assert isinstance(reviewer, FakeReviewer)
    assert captured == {
        "backend": "oneapi",
        "model": "configured-model",
        "base_url": "https://configured.example.test/v1",
        "key": "test-secret-key",
    }


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
    assert updated.status_code == 200
    assert "must-not-be-stored" not in updated.text
    assert updated.json() == {
        "reviewer": "multi-agent",
        "model": "review-model",
        "base_url": "https://oneapi.example.test",
        "key_set": True,
    }


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
    )
    assert duplicate_batch.status_code == 409

    with Session(engine) as session:
        assert session.scalar(select(ContentItem).where(ContentItem.external_id == "duplicate")) is None
        assert session.scalars(select(Project)).all()

    healthy = client.get("/api/health")
    assert healthy.status_code == 200
    assert healthy.json()["ok"] is True
