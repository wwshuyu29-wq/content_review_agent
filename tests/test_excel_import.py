from __future__ import annotations

import importlib
import json
import stat
import struct
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

import pytest
from openpyxl import Workbook, load_workbook

from server.services import excel_import_service
from server.services.excel_import_service import (
    IMPORT_COLUMNS,
    MAX_IMAGE_BYTES,
    build_import_template,
    load_preview,
    preview_import,
)


REQUIRED_COLUMNS = ("供应商内容编号", "活动主题", "平台", "标题", "正文")


def write_workbook(
    path: Path,
    rows: list[list[object]],
    headers: tuple[str, ...] = IMPORT_COLUMNS,
    *,
    second_sheet_rows: list[list[object]] | None = None,
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(row)
    if second_sheet_rows is not None:
        second = workbook.create_sheet("ignored")
        second.append(list(headers))
        for row in second_sheet_rows:
            second.append(row)
    workbook.save(path)
    return path


def valid_row(
    external_id: object = "content-1",
    *,
    image: object = None,
    publish_time: object = None,
    title: object = " 标题 ",
    body: object = " 正文 ",
) -> list[object]:
    return [external_id, " 活动主题 ", " 小红书 ", title, body, image, publish_time, " 备注 "]


def write_zip(path: Path, entries: list[tuple[object, ...]]) -> Path:
    with ZipFile(path, "w") as archive:
        for entry in entries:
            name, content, *kind = entry
            archive.writestr(name, content, compress_type=kind[0] if kind else ZIP_DEFLATED)
    return path


def assert_has_error(preview, text: str, row_index: int = 0) -> None:
    assert any(text in error for error in preview.rows[row_index].errors), preview.rows[row_index].errors


@pytest.fixture(autouse=True)
def isolate_preview_root_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY",
        str(tmp_path / "preview-roots.json"),
    )


def test_default_preview_root_registry_path_is_not_cwd_dependent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY", raising=False)
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    first_path = excel_import_service._preview_root_registry_path()
    monkeypatch.chdir(second_cwd)

    assert excel_import_service._preview_root_registry_path() == first_path
    assert first_path.is_absolute()


def test_build_import_template_has_exact_chinese_columns() -> None:
    workbook = load_workbook(BytesIO(build_import_template()), read_only=True)

    assert workbook.sheetnames == ["内容清单", "测试场景", "字段说明"]
    assert tuple(cell.value for cell in next(workbook["内容清单"].iter_rows())) == (
        "供应商内容编号", "活动主题", "账号名称", "账号类型", "平台", "标题", "正文", "图片文件名", "计划发布时间", "备注",
    )
    assert tuple(cell.value for cell in next(workbook["测试场景"].iter_rows())) == (
        "供应商内容编号", "测试场景编号", "测试结论", "测试指令", "实际返回结果", "测试城市", "测试时间", "百度地图版本", "设备", "操作系统", "网络环境", "证据文件名",
    )


def test_preview_parses_only_first_worksheet_and_trims_required_values(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "input.xlsx",
        [valid_row()],
        second_sheet_rows=[valid_row("ignored")],
    )

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert preview.total_count == 1
    assert preview.valid_count == 1
    assert preview.error_count == 0
    assert preview.rows[0].row_number == 2
    assert preview.rows[0].valid is True
    assert preview.rows[0].normalized == {
        "external_id": "content-1",
        "campaign_theme": "活动主题",
        "platform": "小红书",
        "title": "标题",
        "body": "正文",
        "image_filename": None,
        "publish_time": None,
        "note": "备注",
    }


@pytest.mark.parametrize("missing_header", REQUIRED_COLUMNS)
def test_preview_rejects_missing_required_headers(tmp_path: Path, missing_header: str) -> None:
    headers = tuple(header for header in IMPORT_COLUMNS if header != missing_header)
    xlsx = write_workbook(tmp_path / "missing.xlsx", [], headers)

    with pytest.raises(ValueError, match=missing_header):
        preview_import(xlsx, None, tmp_path / "imports")


def test_preview_accepts_workbook_with_only_required_headers(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "required-only.xlsx",
        [valid_row()[: len(REQUIRED_COLUMNS)]],
        REQUIRED_COLUMNS,
    )

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert preview.total_count == 1
    assert preview.valid_count == 1
    assert preview.rows[0].normalized["image_filename"] is None
    assert preview.rows[0].normalized["publish_time"] is None
    assert preview.rows[0].normalized["note"] is None


def test_preview_rejects_blank_and_duplicate_headers(tmp_path: Path) -> None:
    blank = list(IMPORT_COLUMNS)
    blank[-1] = ""
    duplicate = list(IMPORT_COLUMNS)
    duplicate[-1] = "标题"

    with pytest.raises(ValueError, match="表头.*空"):
        preview_import(write_workbook(tmp_path / "blank.xlsx", [], tuple(blank)), None, tmp_path / "a")
    with pytest.raises(ValueError, match="重复.*标题"):
        preview_import(write_workbook(tmp_path / "duplicate.xlsx", [], tuple(duplicate)), None, tmp_path / "b")


def test_preview_marks_duplicate_ids_on_every_affected_row(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "duplicate-id.xlsx", [valid_row("same"), valid_row(" same ")])

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert preview.error_count == 2
    assert_has_error(preview, "重复")
    assert_has_error(preview, "重复", 1)


def test_preview_ignores_blank_rows_and_rejects_more_than_500_nonblank_rows(tmp_path: Path) -> None:
    rows = [valid_row(f"id-{index}") for index in range(500)]
    rows.insert(20, [None] * len(IMPORT_COLUMNS))
    allowed = preview_import(write_workbook(tmp_path / "500.xlsx", rows), None, tmp_path / "allowed")
    assert allowed.total_count == 500

    rows.append(valid_row("overflow"))
    with pytest.raises(ValueError, match="500"):
        preview_import(write_workbook(tmp_path / "501.xlsx", rows), None, tmp_path / "rejected")


def test_preview_validates_required_values_and_content_service_lengths(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "invalid-values.xlsx",
        [
            valid_row(" "),
            valid_row("long-title", title="x" * 501),
            valid_row("long-body", body="x" * 100_001),
        ],
    )

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert preview.error_count == 3
    assert_has_error(preview, "供应商内容编号")
    assert_has_error(preview, "标题", 1)
    assert_has_error(preview, "正文", 2)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (date(2026, 7, 14), "2026-07-14"),
        (datetime(2026, 7, 15, 13, 30), "2026-07-15"),
        (" 2026-07-16 ", "2026-07-16"),
    ],
)
def test_preview_normalizes_excel_and_text_dates(tmp_path: Path, raw_value: object, expected: str) -> None:
    xlsx = write_workbook(tmp_path / "date.xlsx", [valid_row(publish_time=raw_value)])

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert preview.rows[0].normalized["publish_time"] == expected
    assert preview.rows[0].valid is True


def test_preview_marks_invalid_date_as_row_error(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "bad-date.xlsx", [valid_row(publish_time="2026/07/14")])

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert_has_error(preview, "计划发布时间")


def test_preview_allows_only_one_safe_image_filename_per_row(tmp_path: Path) -> None:
    xlsx = write_workbook(
        tmp_path / "image-names.xlsx",
        [valid_row("many", image="a.jpg,b.jpg"), valid_row("path", image="folder/a.jpg")],
    )

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert_has_error(preview, "一张", 0)
    assert_has_error(preview, "文件名", 1)


def test_zip_is_optional_unless_an_image_is_referenced(tmp_path: Path) -> None:
    no_image = preview_import(
        write_workbook(tmp_path / "no-image.xlsx", [valid_row()]), None, tmp_path / "no-image"
    )
    assert no_image.rows[0].valid is True

    referenced = preview_import(
        write_workbook(tmp_path / "referenced.xlsx", [valid_row(image="cover.jpg")]),
        None,
        tmp_path / "referenced",
    )
    assert_has_error(referenced, "ZIP")


def test_preview_matches_exact_basename_extracts_only_referenced_and_warns_unreferenced(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "images.xlsx", [valid_row(image="Cover.JPG")])
    archive = write_zip(
        tmp_path / "images.zip",
        [("nested/Cover.JPG", b"referenced"), ("unused.png", b"unreferenced")],
    )

    preview = preview_import(xlsx, archive, tmp_path / "imports")

    assert preview.rows[0].valid is True
    assert preview.rows[0].normalized["image_filename"] == "Cover.JPG"
    assert any("unused.png" in warning for warning in preview.warnings)
    preview_dir = next((tmp_path / "imports").iterdir())
    extracted_files = [path for path in preview_dir.rglob("*") if path.is_file()]
    assert any(path.name == "Cover.JPG" and path.read_bytes() == b"referenced" for path in extracted_files)
    assert not any(path.name == "unused.png" for path in extracted_files)


def test_preview_uses_case_sensitive_exact_image_matching(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "case.xlsx", [valid_row(image="cover.jpg")])
    archive = write_zip(tmp_path / "case.zip", [("Cover.JPG", b"image")])

    preview = preview_import(xlsx, archive, tmp_path / "imports")

    assert_has_error(preview, "cover.jpg")


def test_preview_marks_missing_referenced_image_as_row_error(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "missing-image.xlsx", [valid_row(image="missing.png")])
    archive = write_zip(tmp_path / "images.zip", [("other.png", b"image")])

    preview = preview_import(xlsx, archive, tmp_path / "imports")

    assert_has_error(preview, "missing.png")


@pytest.mark.parametrize("unsafe_name", ["../cover.jpg", "/cover.jpg", "C:\\cover.jpg"])
def test_preview_rejects_zip_traversal_and_absolute_paths(tmp_path: Path, unsafe_name: str) -> None:
    xlsx = write_workbook(tmp_path / "unsafe.xlsx", [valid_row()])
    archive = write_zip(tmp_path / "unsafe.zip", [(unsafe_name, b"image")])

    with pytest.raises(ValueError, match="ZIP.*路径"):
        preview_import(xlsx, archive, tmp_path / "imports")


def test_preview_rejects_zip_symlinks(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "symlink.xlsx", [valid_row()])
    archive = tmp_path / "symlink.zip"
    info = ZipInfo("cover.jpg")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive, "w") as output:
        output.writestr(info, "target.jpg")

    with pytest.raises(ValueError, match="符号链接"):
        preview_import(xlsx, archive, tmp_path / "imports")


def mark_zip_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", data, local + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", data, central + 8)[0] | 0x1
    struct.pack_into("<H", data, local + 6, local_flags)
    struct.pack_into("<H", data, central + 8, central_flags)
    path.write_bytes(data)


def test_preview_rejects_encrypted_zip_entries(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "encrypted.xlsx", [valid_row()])
    archive = write_zip(tmp_path / "encrypted.zip", [("cover.jpg", b"image")])
    mark_zip_encrypted(archive)

    with pytest.raises(ValueError, match="加密"):
        preview_import(xlsx, archive, tmp_path / "imports")


def test_preview_rejects_non_image_entries_and_duplicate_basenames(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "invalid-entries.xlsx", [valid_row()])
    non_image = write_zip(tmp_path / "non-image.zip", [("notes.exe", b"text")])
    duplicate = write_zip(
        tmp_path / "duplicate.zip",
        [("a/cover.jpg", b"one"), ("b/cover.jpg", b"two")],
    )

    with pytest.raises(ValueError, match="图片格式"):
        preview_import(xlsx, non_image, tmp_path / "non-image")
    with pytest.raises(ValueError, match="重复.*cover.jpg"):
        preview_import(xlsx, duplicate, tmp_path / "duplicate")


def test_preview_rejects_oversized_zip_and_uncompressed_expansion(tmp_path: Path, monkeypatch) -> None:
    xlsx = write_workbook(tmp_path / "limits.xlsx", [valid_row()])
    archive = write_zip(tmp_path / "limits.zip", [("cover.jpg", b"x" * 128, ZIP_STORED)])

    monkeypatch.setattr(excel_import_service, "MAX_ZIP_BYTES", 32)
    with pytest.raises(ValueError, match="200 MiB"):
        preview_import(xlsx, archive, tmp_path / "zip-too-large")

    monkeypatch.setattr(excel_import_service, "MAX_ZIP_BYTES", 1024)
    monkeypatch.setattr(excel_import_service, "MAX_UNCOMPRESSED_BYTES", 64)
    with pytest.raises(ValueError, match="解压"):
        preview_import(xlsx, archive, tmp_path / "expansion")


def test_preview_rejects_images_larger_than_20_mib(tmp_path: Path, monkeypatch) -> None:
    xlsx = write_workbook(tmp_path / "large-image.xlsx", [valid_row(image="cover.jpg")])
    archive = write_zip(tmp_path / "large-image.zip", [("cover.jpg", b"12345", ZIP_STORED)])
    monkeypatch.setattr(excel_import_service, "MAX_IMAGE_BYTES", 4)

    preview = preview_import(xlsx, archive, tmp_path / "imports")

    assert MAX_IMAGE_BYTES == 20 * 1024 * 1024
    assert_has_error(preview, "20 MiB")


def test_preview_token_is_opaque_and_metadata_is_persisted_without_raw_file_bytes(tmp_path: Path) -> None:
    raw_marker = b"unique raw workbook marker"
    xlsx = write_workbook(tmp_path / "token.xlsx", [valid_row(body=raw_marker.decode())])
    archive = write_zip(tmp_path / "token.zip", [("unused.png", b"raw zip marker")])
    temp_root = tmp_path / "imports"

    preview = preview_import(xlsx, archive, temp_root)
    loaded = load_preview(preview.token)

    assert loaded == preview
    assert len(preview.token) >= 32
    assert str(temp_root) not in preview.token
    manifests = list(temp_root.rglob("preview.json"))
    assert len(manifests) == 1
    metadata_bytes = manifests[0].read_bytes()
    json.loads(metadata_bytes)
    assert xlsx.read_bytes() not in metadata_bytes
    assert archive.read_bytes() not in metadata_bytes


def _preview_manifest(temp_root: Path, token: str) -> Path:
    return temp_root / token / "preview.json"


def test_preview_survives_module_registry_reset_and_reload(tmp_path: Path, monkeypatch) -> None:
    registry = tmp_path / "preview-roots.json"
    monkeypatch.setenv("CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY", str(registry))
    xlsx = write_workbook(tmp_path / "restart.xlsx", [valid_row()])
    temp_root = tmp_path / "imports"
    preview = preview_import(xlsx, None, temp_root)

    excel_import_service._preview_locations.clear()
    reloaded = importlib.reload(excel_import_service)

    loaded = reloaded.load_preview(preview.token)
    assert reloaded._preview_to_dict(loaded) == reloaded._preview_to_dict(preview)
    registry_payload = json.loads(registry.read_text(encoding="utf-8"))
    assert registry_payload == {"roots": [str(temp_root.resolve())], "version": 1}
    assert str(temp_root) not in preview.token


def test_consume_preview_survives_module_registry_reset_and_reload(tmp_path: Path, monkeypatch) -> None:
    registry = tmp_path / "preview-roots.json"
    monkeypatch.setenv("CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY", str(registry))
    xlsx = write_workbook(tmp_path / "restart-consume.xlsx", [valid_row()])
    temp_root = tmp_path / "imports"
    preview = preview_import(xlsx, None, temp_root)
    preview_dir = temp_root / preview.token

    excel_import_service._preview_locations.clear()
    reloaded = importlib.reload(excel_import_service)
    consumed = reloaded.consume_preview(preview.token)

    assert reloaded._preview_to_dict(consumed) == reloaded._preview_to_dict(preview)
    assert not preview_dir.exists()
    with pytest.raises(ValueError, match="不存在|失效"):
        reloaded.consume_preview(preview.token)


def test_expired_preview_is_rejected_and_cleaned_up(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "expired.xlsx", [valid_row()])
    temp_root = tmp_path / "imports"
    preview = preview_import(xlsx, None, temp_root)
    preview_dir = temp_root / preview.token
    manifest = _preview_manifest(temp_root, preview.token)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="过期|失效"):
        load_preview(preview.token)

    assert not preview_dir.exists()


def test_consume_preview_returns_once_and_removes_persisted_preview(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "consume.xlsx", [valid_row()])
    temp_root = tmp_path / "imports"
    preview = preview_import(xlsx, None, temp_root)
    preview_dir = temp_root / preview.token

    consumed = excel_import_service.consume_preview(preview.token)

    assert consumed == preview
    assert not preview_dir.exists()
    with pytest.raises(ValueError, match="不存在|失效"):
        excel_import_service.consume_preview(preview.token)


def test_formula_cells_are_reported_with_readable_row_and_field_errors(tmp_path: Path) -> None:
    xlsx = write_workbook(tmp_path / "formula.xlsx", [valid_row(title="=1+1")])

    preview = preview_import(xlsx, None, tmp_path / "imports")

    assert_has_error(preview, "第 2 行")
    assert_has_error(preview, "标题")
    assert_has_error(preview, "公式")


def test_formula_only_rows_still_trigger_500_row_limit(tmp_path: Path) -> None:
    formula_rows = [[None] * 7 + ["=ROW()"] for _ in range(501)]
    xlsx = write_workbook(tmp_path / "formula-overflow.xlsx", formula_rows)
    temp_root = tmp_path / "imports"

    with pytest.raises(ValueError, match="500"):
        preview_import(xlsx, None, temp_root)

    assert list(temp_root.iterdir()) == []


def test_preview_rejects_more_than_1000_zip_entries_and_cleans_up(tmp_path: Path) -> None:
    assert excel_import_service.MAX_ZIP_ENTRIES == 1000
    xlsx = write_workbook(tmp_path / "entries.xlsx", [valid_row()])
    archive = write_zip(
        tmp_path / "entries.zip",
        [(f"image-{index}.jpg", b"") for index in range(1001)],
    )
    temp_root = tmp_path / "imports"

    with pytest.raises(ValueError, match="1000"):
        preview_import(xlsx, archive, temp_root)

    assert list(temp_root.iterdir()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(total_count=99),
        lambda payload: payload["rows"][0].update(valid=False),
        lambda payload: payload["rows"][0]["normalized"].update(unexpected="value"),
        lambda payload: payload["rows"][0].update(row_number="2"),
        lambda payload: payload.update(unexpected="value"),
        lambda payload: payload.update(expires_at="not-a-timestamp"),
    ],
)
def test_load_preview_rejects_tampered_manifest_schema_and_counts(tmp_path: Path, mutate) -> None:
    xlsx = write_workbook(tmp_path / "tampered.xlsx", [valid_row()])
    temp_root = tmp_path / "imports"
    preview = preview_import(xlsx, None, temp_root)
    manifest = _preview_manifest(temp_root, preview.token)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    mutate(payload)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="预览数据无效"):
        load_preview(preview.token)


class _LazyWorksheet:
    def iter_rows(self, *, values_only: bool = False):
        if values_only:
            yield IMPORT_COLUMNS
        else:
            yield tuple(_FormulaAwareCell(value, "s", f"A{index}") for index, value in enumerate(IMPORT_COLUMNS, 1))
        raise BadZipFile("broken worksheet XML")


class _FormulaAwareCell:
    def __init__(self, value: object, data_type: str, coordinate: str) -> None:
        self.value = value
        self.data_type = data_type
        self.coordinate = coordinate


class _LazyWorkbook:
    worksheets = [_LazyWorksheet()]

    def close(self) -> None:
        pass


def test_lazy_workbook_iteration_error_is_wrapped_and_preview_is_cleaned_up(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(excel_import_service, "load_workbook", lambda *args, **kwargs: _LazyWorkbook())
    temp_root = tmp_path / "imports"

    with pytest.raises(ValueError, match="Excel.*解析"):
        preview_import(tmp_path / "lazy.xlsx", None, temp_root)

    assert list(temp_root.iterdir()) == []
