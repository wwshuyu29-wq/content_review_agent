from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from server import main
from server.services.excel_import_service import CONTENT_COLUMNS, TEST_CASE_COLUMNS, preview_import

def _workbook(path: Path, *, body: str = "亲测：1个测试\n正文") -> Path:
    workbook = Workbook(); content = workbook.active; content.title = "内容清单"
    content.append(list(CONTENT_COLUMNS)); content.append(["c-1", "活动", "账号", "官方", "平台", "标题", body, None, None, None])
    tests = workbook.create_sheet("测试场景"); tests.append(list(TEST_CASE_COLUMNS))
    tests.append(["c-1", "t-1", "通过", "打开地图", "返回结果", "北京", None, "1.0", "设备", "系统", "WiFi", "shot.png"])
    workbook.create_sheet("字段说明"); workbook.save(path); return path

def test_named_sheets_validate_cross_sheet_ids_and_evidence(tmp_path: Path) -> None:
    xlsx = _workbook(tmp_path / "input.xlsx"); archive = tmp_path / "evidence.zip"
    with ZipFile(archive, "w") as output: output.writestr("shot.png", b"evidence")
    preview = preview_import(xlsx, archive, tmp_path / "previews")
    assert preview.test_count == 1; assert preview.rows[0].tests[0].external_test_case_id == "t-1"; assert preview.rows[0].valid

def test_missing_test_sheet_is_error_for_evidence_trigger(tmp_path: Path) -> None:
    xlsx = _workbook(tmp_path / "input.xlsx"); workbook = load_workbook(xlsx); del workbook["测试场景"]; workbook.save(xlsx)
    preview = preview_import(xlsx, None, tmp_path / "previews")
    assert any("测试场景" in error for error in preview.rows[0].errors)

def test_api_template_handler_is_downloadable() -> None:
    response = main.download_import_template()
    assert response.media_type.startswith("application/vnd.openxmlformats")
    assert load_workbook(BytesIO(response.body)).sheetnames == ["内容清单", "测试场景", "字段说明"]
