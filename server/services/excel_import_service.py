from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from secrets import token_urlsafe
from typing import Any, Dict, List, Optional, Tuple
from zipfile import BadZipFile, ZipFile, ZipInfo

from openpyxl import Workbook, load_workbook

from server.services.content_service import MAX_BODY_LENGTH, MAX_TITLE_LENGTH


IMPORT_COLUMNS = (
    "供应商内容编号",
    "活动主题",
    "平台",
    "标题",
    "正文",
    "图片文件名",
    "计划发布时间",
    "备注",
)
REQUIRED_COLUMNS = IMPORT_COLUMNS[:5]
MAX_IMPORT_ROWS = 500
EXCEL_CELL_TEXT_LIMIT = 32_767
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_COLUMN_KEYS = {
    "供应商内容编号": "external_id",
    "活动主题": "campaign_theme",
    "平台": "platform",
    "标题": "title",
    "正文": "body",
    "图片文件名": "image_filename",
    "计划发布时间": "publish_time",
    "备注": "note",
}
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ImportRowPreview:
    row_number: int
    normalized: Dict[str, Any]
    errors: List[str]
    warnings: List[str]
    valid: bool


@dataclass(frozen=True)
class ImportPreview:
    token: str
    rows: List[ImportRowPreview]
    warnings: List[str]
    total_count: int
    valid_count: int
    error_count: int


@dataclass(frozen=True)
class _PreviewLocation:
    temp_root: Path
    preview_dir: Path
    manifest: Path


_preview_locations: Dict[str, _PreviewLocation] = {}
_preview_locations_lock = threading.Lock()


def build_import_template() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(IMPORT_COLUMNS))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def preview_import(xlsx_path: Path, zip_path: Optional[Path], temp_root: Path) -> ImportPreview:
    token = token_urlsafe(32)
    root = temp_root.resolve()
    preview_dir = root / token
    manifest = preview_dir / "preview.json"
    registered = False

    try:
        preview_dir.mkdir(parents=True, exist_ok=False)
        rows = _read_rows(xlsx_path)
        warnings: List[str] = []
        zip_entries: Dict[str, ZipInfo] = {}

        if zip_path is not None:
            zip_entries, warnings = _inspect_zip(zip_path)

        rows, image_warnings = _validate_images(rows, zip_path, zip_entries)
        warnings.extend(image_warnings)
        rows = _mark_duplicate_external_ids(rows)

        if zip_path is not None:
            _extract_referenced_images(zip_path, zip_entries, rows, preview_dir / "images")

        valid_count = sum(row.valid for row in rows)
        preview = ImportPreview(
            token=token,
            rows=rows,
            warnings=warnings,
            total_count=len(rows),
            valid_count=valid_count,
            error_count=len(rows) - valid_count,
        )
        _write_preview(manifest, preview)
        location = _PreviewLocation(root, preview_dir.resolve(), manifest.resolve())
        with _preview_locations_lock:
            _preview_locations[token] = location
            registered = True
        return preview
    except Exception:
        if registered:
            with _preview_locations_lock:
                _preview_locations.pop(token, None)
        shutil.rmtree(preview_dir, ignore_errors=True)
        raise


def load_preview(token: str) -> ImportPreview:
    if not isinstance(token, str) or not token:
        raise ValueError("无效的导入 token")

    with _preview_locations_lock:
        location = _preview_locations.get(token)
    if location is None:
        raise ValueError("导入 token 不存在或已失效")

    try:
        preview_dir = location.preview_dir.resolve(strict=True)
        manifest = location.manifest.resolve(strict=True)
        root = location.temp_root.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError("导入预览不存在或已失效") from exc

    if preview_dir.parent != root or manifest.parent != preview_dir or not manifest.is_file():
        raise ValueError("导入预览路径无效")

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        preview = _preview_from_dict(payload)
    except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("导入预览数据无效") from exc
    if preview.token != token:
        raise ValueError("导入预览 token 不匹配")
    return preview


def _read_rows(xlsx_path: Path) -> List[ImportRowPreview]:
    try:
        workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Excel 文件无法读取") from exc

    try:
        worksheet = workbook.worksheets[0]
        iterator = worksheet.iter_rows(values_only=True)
        try:
            raw_headers = next(iterator)
        except StopIteration as exc:
            raise ValueError("Excel 表头不能为空") from exc
        headers = _validate_headers(raw_headers)
        indexes = {header: index for index, header in enumerate(headers)}

        rows: List[ImportRowPreview] = []
        for row_number, values in enumerate(iterator, start=2):
            if _is_blank_row(values):
                continue
            if len(rows) >= MAX_IMPORT_ROWS:
                raise ValueError("Excel 最多允许 500 条内容")
            rows.append(_normalize_row(row_number, values, indexes))
        return rows
    finally:
        workbook.close()


def _validate_headers(raw_headers: Tuple[Any, ...]) -> Tuple[str, ...]:
    headers: List[str] = []
    for value in raw_headers:
        if value is None or not str(value).strip():
            raise ValueError("Excel 表头不得为空")
        headers.append(str(value).strip())

    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if duplicates:
        raise ValueError("Excel 表头重复：" + "、".join(duplicates))

    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise ValueError("Excel 缺少必需表头：" + "、".join(missing))
    return tuple(headers)


def _is_blank_row(values: Tuple[Any, ...]) -> bool:
    return all(value is None or (isinstance(value, str) and not value.strip()) for value in values)


def _normalize_row(
    row_number: int, values: Tuple[Any, ...], indexes: Dict[str, int]
) -> ImportRowPreview:
    normalized: Dict[str, Any] = {}
    errors: List[str] = []

    for column in IMPORT_COLUMNS:
        raw_value = values[indexes[column]] if column in indexes and indexes[column] < len(values) else None
        key = _COLUMN_KEYS[column]
        if column == "计划发布时间":
            normalized[key], date_error = _normalize_date(raw_value)
            if date_error:
                errors.append(date_error)
        else:
            normalized[key] = _normalize_text(raw_value)

    for column in REQUIRED_COLUMNS:
        if not normalized[_COLUMN_KEYS[column]]:
            errors.append(f"{column}不能为空")

    external_id = normalized["external_id"]
    if external_id and len(external_id) > 200:
        errors.append("供应商内容编号不能超过 200 个字符")
    title = normalized["title"]
    if title and len(title) > MAX_TITLE_LENGTH:
        errors.append(f"标题不能超过 {MAX_TITLE_LENGTH} 个字符")
    body = normalized["body"]
    if body and len(body) >= EXCEL_CELL_TEXT_LIMIT:
        errors.append("正文达到 Excel 单元格上限，可能已被截断")
    elif body and len(body) > MAX_BODY_LENGTH:
        errors.append(f"正文不能超过 {MAX_BODY_LENGTH} 个字符")

    image_filename = normalized["image_filename"]
    if image_filename:
        if "," in image_filename or "，" in image_filename:
            errors.append("第一版每条内容最多只能引用一张图片")
        elif not _is_safe_basename(image_filename):
            errors.append("图片文件名必须是不含路径的安全文件名")
        elif Path(image_filename).suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
            errors.append("图片文件名必须使用 JPG、JPEG、PNG 或 WEBP 格式")

    return ImportRowPreview(row_number, normalized, errors, [], not errors)


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_date(value: Any) -> Tuple[Optional[str], Optional[str]]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, datetime):
        return value.date().isoformat(), None
    if isinstance(value, date):
        return value.isoformat(), None
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat(), None
    except ValueError:
        return text, "计划发布时间必须为 YYYY-MM-DD 或 Excel 日期"


def _inspect_zip(zip_path: Path) -> Tuple[Dict[str, ZipInfo], List[str]]:
    try:
        if zip_path.stat().st_size > MAX_ZIP_BYTES:
            raise ValueError("ZIP 文件不能超过 200 MiB")
    except OSError as exc:
        raise ValueError("ZIP 文件无法读取") from exc

    entries: Dict[str, ZipInfo] = {}
    total_size = 0
    try:
        with ZipFile(zip_path) as archive:
            for info in archive.infolist():
                _validate_zip_path(info.filename)
                if info.flag_bits & 0x1:
                    raise ValueError(f"ZIP 包含加密文件：{info.filename}")
                if _is_zip_symlink(info):
                    raise ValueError(f"ZIP 不允许符号链接：{info.filename}")
                if info.is_dir():
                    continue
                suffix = PurePosixPath(info.filename).suffix.lower()
                if suffix not in ALLOWED_IMAGE_SUFFIXES:
                    raise ValueError(f"ZIP 仅允许图片格式：{info.filename}")
                total_size += info.file_size
                if total_size > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("ZIP 解压后内容超过安全限制")
                basename = PurePosixPath(info.filename).name
                if basename in entries:
                    raise ValueError(f"ZIP 内图片文件名重复：{basename}")
                entries[basename] = info
    except BadZipFile as exc:
        raise ValueError("ZIP 文件无法读取") from exc

    return entries, []


def _validate_zip_path(name: str) -> None:
    if not name or "\\" in name or name.startswith("/") or _WINDOWS_DRIVE.match(name):
        raise ValueError(f"ZIP 包含不安全路径：{name}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"ZIP 包含不安全路径：{name}")


def _is_zip_symlink(info: ZipInfo) -> bool:
    return info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16)


def _is_safe_basename(name: str) -> bool:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return False
    if name.startswith("/") or _WINDOWS_DRIVE.match(name):
        return False
    return PurePosixPath(name).name == name


def _validate_images(
    rows: List[ImportRowPreview], zip_path: Optional[Path], entries: Dict[str, ZipInfo]
) -> Tuple[List[ImportRowPreview], List[str]]:
    referenced = {
        row.normalized["image_filename"]
        for row in rows
        if row.normalized["image_filename"] and _is_safe_basename(row.normalized["image_filename"])
    }
    updated: List[ImportRowPreview] = []
    for row in rows:
        errors = list(row.errors)
        image_filename = row.normalized["image_filename"]
        if image_filename and _is_safe_basename(image_filename):
            if zip_path is None:
                errors.append("填写图片文件名时必须上传图片 ZIP")
            elif image_filename not in entries:
                errors.append(f"ZIP 中未找到图片：{image_filename}")
            elif entries[image_filename].file_size > MAX_IMAGE_BYTES:
                errors.append(f"图片不能超过 20 MiB：{image_filename}")
        updated.append(_replace_row_errors(row, errors))

    warnings = []
    if zip_path is not None:
        warnings = [
            f"ZIP 中图片未被 Excel 引用：{name}"
            for name in sorted(set(entries) - referenced)
        ]
    return updated, warnings


def _mark_duplicate_external_ids(rows: List[ImportRowPreview]) -> List[ImportRowPreview]:
    counts: Dict[str, int] = {}
    for row in rows:
        external_id = row.normalized["external_id"]
        if external_id:
            counts[external_id] = counts.get(external_id, 0) + 1

    updated = []
    for row in rows:
        errors = list(row.errors)
        external_id = row.normalized["external_id"]
        if external_id and counts[external_id] > 1:
            errors.append(f"供应商内容编号在批次内重复：{external_id}")
        updated.append(_replace_row_errors(row, errors))
    return updated


def _replace_row_errors(row: ImportRowPreview, errors: List[str]) -> ImportRowPreview:
    return ImportRowPreview(row.row_number, row.normalized, errors, row.warnings, not errors)


def _extract_referenced_images(
    zip_path: Path, entries: Dict[str, ZipInfo], rows: List[ImportRowPreview], image_dir: Path
) -> None:
    referenced = {
        row.normalized["image_filename"]
        for row in rows
        if row.normalized["image_filename"] in entries
        and entries[row.normalized["image_filename"]].file_size <= MAX_IMAGE_BYTES
    }
    if not referenced:
        return

    image_dir.mkdir()
    try:
        with ZipFile(zip_path) as archive:
            for basename in sorted(referenced):
                destination = image_dir / basename
                written = 0
                with archive.open(entries[basename]) as source, destination.open("xb") as output:
                    while True:
                        chunk = source.read(min(1024 * 1024, MAX_IMAGE_BYTES + 1 - written))
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_IMAGE_BYTES:
                            raise ValueError(f"图片不能超过 20 MiB：{basename}")
                        output.write(chunk)
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("ZIP 图片解压失败") from exc


def _write_preview(path: Path, preview: ImportPreview) -> None:
    payload = _preview_to_dict(preview)
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _preview_to_dict(preview: ImportPreview) -> Dict[str, Any]:
    return asdict(preview)


def _preview_from_dict(payload: Dict[str, Any]) -> ImportPreview:
    rows = [
        ImportRowPreview(
            row_number=int(row["row_number"]),
            normalized=dict(row["normalized"]),
            errors=list(row["errors"]),
            warnings=list(row["warnings"]),
            valid=bool(row["valid"]),
        )
        for row in payload["rows"]
    ]
    return ImportPreview(
        token=str(payload["token"]),
        rows=rows,
        warnings=list(payload["warnings"]),
        total_count=int(payload["total_count"]),
        valid_count=int(payload["valid_count"]),
        error_count=int(payload["error_count"]),
    )
