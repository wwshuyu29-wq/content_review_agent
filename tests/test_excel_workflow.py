from __future__ import annotations

import importlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db import Base, create_db_engine
from server.models import (
    AgentResult,
    AuditRun,
    Batch,
    ContentItem,
    ContentVersion,
    FormatStatus,
    HumanDecision,
    Issue,
    PublishStatus,
    ReviewStatus,
    ReviewTask,
)
from server.seed import seed_default_project
from server.services import excel_import_service
from server.services.excel_import_service import IMPORT_COLUMNS, preview_import


EXPORT_COLUMNS = [
    "系统内容编号",
    "批次编号",
    "格式校验",
    "审核状态",
    "发布状态",
    "问题数量",
    "最高风险等级",
    "问题分类",
    "命中规则",
    "问题原因",
    "原文证据",
    "修改建议",
    "最终标题",
    "最终正文",
    "最终版本来源",
    "是否需要人工",
    "是否已人工确认",
    "审核模型",
    "规则版本",
    "审核完成时间",
]


def confirm_import(*args, **kwargs):
    service = getattr(excel_import_service, "confirm_import", None)
    assert callable(service), "confirm_import service API is missing"
    return service(*args, **kwargs)


def export_batch(*args, **kwargs) -> bytes:
    try:
        module = importlib.import_module("server.services.excel_export_service")
    except ModuleNotFoundError as exc:
        pytest.fail(f"excel_export_service module is missing: {exc}")
    service = getattr(module, "export_batch", None)
    assert callable(service), "export_batch service API is missing"
    return service(*args, **kwargs)


@pytest.fixture(autouse=True)
def isolate_import_and_upload_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY", str(tmp_path / "preview-roots.json"))
    monkeypatch.setenv("CR_DATA_DIR", str(tmp_path / "data"))
    excel_import_service._preview_locations.clear()


def make_session(tmp_path: Path) -> Session:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def write_workbook(path: Path, rows: list[list[object]]) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(IMPORT_COLUMNS))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    return path


def supplier_row(
    external_id: object,
    *,
    campaign_theme: object = "暑期活动",
    platform: object = "小红书",
    title: object = "原始标题",
    body: object = "原始正文",
    image_filename: object = None,
    publish_time: object = "2026-07-20",
    note: object = "供应商备注",
) -> list[object]:
    return [external_id, campaign_theme, platform, title, body, image_filename, publish_time, note]


def write_zip(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content, compress_type=ZIP_DEFLATED)
    return path


def payload_by_supplier_id(batch: Batch) -> dict[str, dict]:
    return {
        item.versions[0].payload["supplier_external_id"]: item.versions[0].payload
        for item in batch.content_items
    }


def test_confirm_import_imports_multiple_preview_rows_into_one_batch(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "multiple.xlsx",
        [supplier_row("content-1", title="标题一"), supplier_row("content-2", title="标题二")],
    )
    preview = preview_import(xlsx, None, tmp_path / "previews")

    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        batch = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="supplier-a",
            batch_name="Excel 批量导入",
        )

        assert batch.import_token == preview.token
        assert batch.project_id == project.id
        assert batch.supplier_id == "supplier-a"
        assert batch.name == "Excel 批量导入"
        assert len(batch.content_items) == 2
        assert {item.external_id for item in batch.content_items} == {"content-1", "content-2"}
        assert {item.format_status for item in batch.content_items} == {FormatStatus.PASSED}
        assert all(item.review_status is ReviewStatus.NOT_STARTED for item in batch.content_items)
        assert all(item.publish_status is PublishStatus.NOT_READY for item in batch.content_items)
        assert all(len(item.versions) == 1 for item in batch.content_items)
        assert all(item.versions[0].source == "SUPPLIER" for item in batch.content_items)
        payloads = payload_by_supplier_id(batch)
        assert payloads["content-1"]["row_number"] == 2
        assert payloads["content-2"]["row_number"] == 3
        assert payloads["content-1"]["campaign_theme"] == "暑期活动"
        assert not (tmp_path / "previews" / preview.token).exists()


def test_confirm_import_retains_preview_error_rows_with_errors_in_payload(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "duplicates.xlsx",
        [supplier_row("duplicate", title="第一条"), supplier_row("duplicate", title="第二条")],
    )
    preview = preview_import(xlsx, None, tmp_path / "previews")
    assert preview.error_count == 2

    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        batch = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="supplier-a",
            batch_name="包含错误行",
        )

        assert len(batch.content_items) == 2
        assert all(item.format_status is not FormatStatus.PASSED for item in batch.content_items)
        assert {item.format_status for item in batch.content_items} == {FormatStatus.INVALID}
        assert "duplicate" not in {item.external_id for item in batch.content_items}
        assert len({item.external_id for item in batch.content_items}) == 2
        for item in batch.content_items:
            payload = item.versions[0].payload
            assert payload["supplier_external_id"] == "duplicate"
            assert any("重复" in error for error in payload["preview_errors"])
            assert payload["preview_warnings"] == []


def test_confirm_import_copies_referenced_preview_images_by_exact_filename_per_row(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "images.xlsx",
        [
            supplier_row("row-second", image_filename="second.png"),
            supplier_row("row-first", image_filename="first.png"),
        ],
    )
    archive = write_zip(tmp_path / "images.zip", [("first.png", b"first-image"), ("second.png", b"second-image")])
    preview = preview_import(xlsx, archive, tmp_path / "previews")
    assert preview.valid_count == 2

    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        batch = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="supplier-images",
            batch_name="图片导入",
        )

        payloads = payload_by_supplier_id(batch)
        uploads_dir = tmp_path / "data" / "uploads"
        assert payloads["row-second"]["image_filename"] == "second.png"
        assert payloads["row-first"]["image_filename"] == "first.png"
        assert (uploads_dir / payloads["row-second"]["media"]).read_bytes() == b"second-image"
        assert (uploads_dir / payloads["row-first"]["media"]).read_bytes() == b"first-image"
        assert Path(payloads["row-second"]["media"]).name == payloads["row-second"]["media"]
        assert Path(payloads["row-first"]["media"]).name == payloads["row-first"]["media"]


def test_confirm_import_is_idempotent_for_same_token_without_duplicate_content(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "idempotent.xlsx", [supplier_row("content-1")])
    preview = preview_import(xlsx, None, tmp_path / "previews")

    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        first = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="supplier-a",
            batch_name="首次确认",
        )
        first_id = first.id
        first_content_ids = [item.id for item in first.content_items]

        second = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="ignored",
            batch_name="重复确认不应新建",
        )

        assert second.id == first_id
        assert [item.id for item in second.content_items] == first_content_ids
        assert session.scalar(select(func.count(Batch.id))) == 1
        assert session.scalar(select(func.count(ContentItem.id))) == 1


def test_export_batch_returns_xlsx_with_supplier_and_review_columns(tmp_path: Path) -> None:
    completed_at = datetime(2026, 7, 15, 8, 30, 0)
    xlsx = write_workbook(
        tmp_path / "export.xlsx",
        [
            supplier_row(
                "supplier-1",
                campaign_theme="新品活动",
                platform="抖音",
                title="供应商标题",
                body="供应商正文",
                image_filename=None,
                publish_time="2026-08-01",
                note="导出备注",
            )
        ],
    )
    preview = preview_import(xlsx, None, tmp_path / "previews")

    with make_session(tmp_path) as session:
        project = seed_default_project(session)
        batch = confirm_import(
            session,
            preview.token,
            project_id=project.id,
            supplier_id="supplier-a",
            batch_name="导出批次",
        )
        item = batch.content_items[0]
        supplier_version = item.versions[0]
        audit = AuditRun(
            content_item=item,
            content_version=supplier_version,
            rule_version=project.current_rule_version,
            model="model-x",
            prompt_version="prompt-x",
            status="COMPLETED",
            completed_at=completed_at,
        )
        result = AgentResult(
            audit_run=audit,
            agent_name="quality",
            status="COMPLETED",
            raw_result={"source": "test"},
        )
        low_issue = Issue(
            audit_run=audit,
            agent_result=result,
            rule_id="QUALITY-1",
            category="quality",
            severity="low",
            field="body",
            evidence_quote="证据一",
            reason="原因一",
            suggestion="建议一",
            auto_fixable=True,
            human_required=False,
            confidence=0.9,
        )
        high_issue = Issue(
            audit_run=audit,
            agent_result=result,
            rule_id="RISK-1",
            category="external",
            severity="high",
            field="title",
            evidence_quote="证据二",
            reason="原因二",
            suggestion="建议二",
            auto_fixable=False,
            human_required=True,
            confidence=0.99,
        )
        task = ReviewTask(
            content_item=item,
            issue=high_issue,
            target_content_version=supplier_version,
            audit_run=audit,
            task_type="RISK_REVIEW",
            status="CLOSED",
            closed_at=completed_at,
        )
        HumanDecision(
            review_task=task,
            decision="APPROVE_RISK",
            reviewer="legal@example.com",
            note="已确认",
            payload={},
        )
        final_version = ContentVersion(
            content_item=item,
            version=2,
            source="HUMAN_APPROVED",
            title="最终标题",
            body="最终正文",
            payload=supplier_version.payload,
        )
        item.title = "最终标题"
        item.review_status = ReviewStatus.APPROVED
        item.publish_status = PublishStatus.READY
        session.add_all([audit, low_issue, final_version])
        session.commit()

        workbook = load_workbook(BytesIO(export_batch(session, batch.id)))
        worksheet = workbook.active
        headers = [cell.value for cell in worksheet[1]]
        values = {headers[index]: worksheet.cell(row=2, column=index + 1).value for index in range(len(headers))}

        assert headers == list(IMPORT_COLUMNS) + EXPORT_COLUMNS
        assert values["供应商内容编号"] == "supplier-1"
        assert values["活动主题"] == "新品活动"
        assert values["平台"] == "抖音"
        assert values["标题"] == "供应商标题"
        assert values["正文"] == "供应商正文"
        assert values["图片文件名"] is None
        assert values["计划发布时间"] == "2026-08-01"
        assert values["备注"] == "导出备注"
        assert values["系统内容编号"] == item.id
        assert values["批次编号"] == batch.id
        assert values["格式校验"] == "PASSED"
        assert values["审核状态"] == "APPROVED"
        assert values["发布状态"] == "READY"
        assert values["问题数量"] == 2
        assert values["最高风险等级"] == "high"
        assert values["问题分类"] == "quality\nexternal"
        assert values["命中规则"] == "QUALITY-1\nRISK-1"
        assert values["问题原因"] == "原因一\n原因二"
        assert values["原文证据"] == "证据一\n证据二"
        assert values["修改建议"] == "建议一\n建议二"
        assert values["最终标题"] == "最终标题"
        assert values["最终正文"] == "最终正文"
        assert values["最终版本来源"] == "HUMAN_APPROVED"
        assert values["是否需要人工"] == "是"
        assert values["是否已人工确认"] == "是"
        assert values["审核模型"] == "model-x"
        assert values["规则版本"] == project.current_rule_version.version
        assert values["审核完成时间"] == completed_at
        assert worksheet.freeze_panes == "A2"
        assert worksheet.auto_filter.ref == f"A1:{get_column_letter(len(headers))}2"
        assert worksheet["E2"].alignment.wrap_text is True
        assert worksheet["AA2"].alignment.wrap_text is True


def test_export_batch_raises_for_missing_batch(tmp_path: Path) -> None:
    with make_session(tmp_path) as session:
        with pytest.raises(ValueError, match="Batch 999 does not exist"):
            export_batch(session, 999)
