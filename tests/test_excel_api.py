from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy.orm import Session

from server import main
from server.db import create_db_engine
from server.models import AgentResult, AuditRun, Issue, Project
from server.services import excel_import_service
from server.services.excel_import_service import CONTENT_COLUMNS, TEST_CASE_COLUMNS


@pytest.fixture
def excel_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = f"sqlite:///{tmp_path / 'excel-api.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY", str(tmp_path / "preview-roots.json"))
    engine = create_db_engine(database_url)

    def test_session():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_session] = test_session
    with TestClient(main.app) as client:
        yield client, engine, tmp_path
    main.app.dependency_overrides.clear()
    excel_import_service._preview_locations.clear()
    engine.dispose()


def workbook_bytes(*, external_id: str = "content-1", body: str = "普通正文", evidence: str | None = None) -> bytes:
    workbook = Workbook()
    content = workbook.active
    content.title = "内容清单"
    content.append(list(CONTENT_COLUMNS))
    content.append([external_id, "活动", "账号", "媒体", "小红书", "原始标题", body, None, "2026-07-20", "备注"])
    tests = workbook.create_sheet("测试场景")
    tests.append(list(TEST_CASE_COLUMNS))
    if evidence:
        tests.append([external_id, "case-1", "通过", "打开地图", "返回结果", "北京", None, "1.0", "设备", "系统", "WiFi", evidence])
    workbook.create_sheet("字段说明")
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def zip_bytes(filename: str, content: bytes = b"evidence") -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(filename, content)
    return output.getvalue()


def project(client: TestClient) -> dict:
    return client.get("/api/projects").json()[0]


def preview_request(client: TestClient, project_id: int, *, xlsx: bytes | None = None, zip_content: bytes | None = None, supplier: str = "supplier", batch: str = "batch"):
    files = {"excel_file": ("input.xlsx", xlsx or workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    if zip_content is not None:
        files["evidence_zip"] = ("evidence.zip", zip_content, "application/zip")
    return client.post("/api/imports/preview", data={"project_id": str(project_id), "supplier_id": supplier, "batch_name": batch}, files=files)


def test_template_route_has_attachment_and_exact_sheets(excel_api) -> None:
    client, _, _ = excel_api
    response = client.get("/api/import-template")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert load_workbook(BytesIO(response.content)).sheetnames == ["内容清单", "测试场景", "字段说明"]


def test_preview_multipart_returns_typed_identity_rows_and_tests(excel_api) -> None:
    client, _, _ = excel_api
    current = project(client)
    response = preview_request(client, current["id"], xlsx=workbook_bytes(body="亲测 1 个测试", evidence="proof.png"), zip_content=zip_bytes("proof.png"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["project_id"] == current["id"]
    assert payload["supplier_id"] == "supplier"
    assert payload["batch_name"] == "batch"
    assert payload["rows"][0]["tests"][0]["external_test_case_id"] == "case-1"
    assert payload["errors"] == []


def test_preview_rejects_non_tech_bad_suffix_malformed_and_oversize(excel_api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, engine, _ = excel_api
    with Session(engine) as session:
        other = Project(name="Other", code="other", content_type="OTHER")
        session.add(other); session.commit(); other_id = other.id
    assert preview_request(client, other_id).status_code == 422
    current = project(client)
    bad_suffix = client.post("/api/imports/preview", data={"project_id": current["id"], "supplier_id": "s", "batch_name": "b"}, files={"excel_file": ("input.xls", b"x", "application/octet-stream")})
    assert bad_suffix.status_code == 422
    assert preview_request(client, current["id"], xlsx=b"not-xlsx").status_code == 422
    monkeypatch.setattr(main, "MAX_EXCEL_UPLOAD_BYTES", 4)
    assert preview_request(client, current["id"]).status_code == 413


def test_preview_temp_upload_directory_is_cleaned(excel_api) -> None:
    client, _, tmp_path = excel_api
    current = project(client)
    assert preview_request(client, current["id"]).status_code == 200
    root = tmp_path / "data" / "import-previews"
    assert not any(path.name.startswith("upload-") for path in root.iterdir())
    assert not any(path.suffix == ".xlsx" for path in root.rglob("*"))


def test_confirm_is_idempotent_and_rejects_cross_identity(excel_api) -> None:
    client, _, _ = excel_api
    current = project(client)
    token = preview_request(client, current["id"]).json()["token"]
    body = {"project_id": current["id"], "supplier_id": "supplier", "batch_name": "batch"}
    first = client.post(f"/api/imports/{token}/confirm", json=body)
    second = client.post(f"/api/imports/{token}/confirm", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert client.post(f"/api/imports/{token}/confirm", json={**body, "supplier_id": "other"}).status_code == 422
    assert client.post("/api/imports/not-a-token/confirm", json=body).status_code == 422


def test_expired_token_returns_422(excel_api) -> None:
    client, _, tmp_path = excel_api
    current = project(client)
    token = preview_request(client, current["id"]).json()["token"]
    manifest = tmp_path / "data" / "import-previews" / token / "preview.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    response = client.post(f"/api/imports/{token}/confirm", json={"project_id": current["id"], "supplier_id": "supplier", "batch_name": "batch"})
    assert response.status_code == 422


def test_export_route_and_missing_batch(excel_api) -> None:
    client, _, _ = excel_api
    current = project(client)
    token = preview_request(client, current["id"]).json()["token"]
    batch = client.post(f"/api/imports/{token}/confirm", json={"project_id": current["id"], "supplier_id": "supplier", "batch_name": "batch"}).json()
    exported = client.get(f"/api/batches/{batch['id']}/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    sheet = load_workbook(BytesIO(exported.content)).active
    assert sheet.cell(2, 1).value == "content-1"
    assert client.get("/api/batches/99999/export").status_code == 404


def test_contents_table_contract_filters_severity_agents_and_missing_media(excel_api) -> None:
    client, engine, _ = excel_api
    current = project(client)
    token = preview_request(client, current["id"]).json()["token"]
    batch = client.post(f"/api/imports/{token}/confirm", json={"project_id": current["id"], "supplier_id": "supplier", "batch_name": "batch"}).json()
    content_id = batch["contents"][0]["id"]
    with Session(engine) as session:
        item = session.get(main.ContentItem, content_id)
        audit = AuditRun(content_item=item, content_version=item.versions[0], rule_version=item.project.current_rule_version, model="m", prompt_version="p", status="COMPLETED")
        for agent_id in reversed(["COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY", "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS"]):
            audit.agent_results.append(AgentResult(agent_name=agent_id, agent_id=agent_id, agent_version="v1", decision="PASS", summary=agent_id, score=90, status="COMPLETED", raw_result={}))
        audit.issues.extend([
            Issue(rule_id="LOW", category="quality", severity="LOW", field="body", evidence_quote="", reason="low", suggestion="s1", auto_fixable=False, human_required=False, confidence=1),
            Issue(rule_id="CRITICAL", category="risk", severity="CRITICAL", field="body", evidence_quote="", reason="critical", suggestion="s2", auto_fixable=False, human_required=True, confidence=1),
        ])
        session.add(audit); session.commit()
    response = client.get("/api/contents/table", params={"project_id": current["id"], "batch_id": batch["id"], "format_status": "PASSED"})
    assert response.status_code == 200
    row = response.json()[0]
    assert row["supplier_external_id"] == "content-1"
    assert row["original_title"] == "原始标题" and row["final_title"] == "原始标题"
    assert row["highest_severity"] == "CRITICAL"
    assert [agent["agent_id"] for agent in row["agents"]] == ["COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY", "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS"]
    assert row["media_url"] is None
    assert client.get("/api/contents/table", params={"review_status": "INVALID"}).status_code == 422


def test_contents_table_always_returns_six_canonical_agent_slots(excel_api) -> None:
    client, _, _ = excel_api
    current = project(client)
    token = preview_request(client, current["id"]).json()["token"]
    client.post(f"/api/imports/{token}/confirm", json={"project_id": current["id"], "supplier_id": "supplier", "batch_name": "batch"})
    row = client.get("/api/contents/table").json()[0]
    assert [agent["agent_id"] for agent in row["agents"]] == ["COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "TEST_CREDIBILITY", "CONTENT_QUALITY", "CAMPAIGN_EFFECTIVENESS"]
    assert all(agent["status"] == "NOT_RUN" for agent in row["agents"])


def test_preview_orphan_test_error_is_returned_and_not_confirmable(excel_api) -> None:
    client, _, _ = excel_api
    current = project(client)
    data = workbook_bytes()
    workbook = load_workbook(BytesIO(data))
    tests = workbook["测试场景"]
    tests.append(["missing", "orphan", "通过", "指令", "结果", None, None, None, None, None, None, "proof.png"])
    output = BytesIO(); workbook.save(output)
    response = preview_request(client, current["id"], xlsx=output.getvalue(), zip_content=zip_bytes("proof.png"))
    assert response.status_code == 200
    payload = response.json()
    assert any("不存在" in error for error in payload["errors"])
    confirm = client.post(f"/api/imports/{payload['token']}/confirm", json={"project_id": current["id"], "supplier_id": "supplier", "batch_name": "batch"})
    assert confirm.status_code == 422
