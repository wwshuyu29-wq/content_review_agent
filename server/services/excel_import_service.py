from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
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
MAX_ZIP_ENTRIES = 1000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
PREVIEW_TTL = timedelta(hours=2)
PREVIEW_MANIFEST_VERSION = 1
PREVIEW_ROOT_REGISTRY_ENV = "CONTENT_REVIEW_PREVIEW_ROOT_REGISTRY"
DEFAULT_PREVIEW_ROOT_REGISTRY = Path(__file__).resolve().parents[2] / "data" / "preview-roots.json"
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
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_PREVIEW_KEYS = frozenset(
    {
        "version",
        "token",
        "expires_at",
        "rows",
        "warnings",
        "total_count",
        "valid_count",
        "error_count",
    }
)
_ROW_KEYS = frozenset({"row_number", "normalized", "errors", "warnings", "valid"})
_NORMALIZED_KEYS = frozenset(_COLUMN_KEYS.values())


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
        expires_at = datetime.now(timezone.utc) + PREVIEW_TTL
        _write_preview(manifest, preview, expires_at)
        location = _PreviewLocation(root, preview_dir.resolve(), manifest.resolve())
        with _preview_locations_lock:
            _register_preview_root(root)
            _preview_locations[token] = location
            registered = True
        cleanup_expired_previews()
        return preview
    except Exception:
        if registered:
            with _preview_locations_lock:
                _preview_locations.pop(token, None)
        shutil.rmtree(preview_dir, ignore_errors=True)
        raise


def load_preview(token: str) -> ImportPreview:
    location = _resolve_preview_location(token)
    try:
        preview, expires_at = _load_preview_manifest(location, token)
    except _ExpiredPreviewError as exc:
        _remove_preview(token, location)
        raise ValueError("导入预览已过期") from exc
    return preview


def consume_preview(token: str) -> ImportPreview:
    location = _resolve_preview_location(token)
    consumed_dir = location.preview_dir.with_name(location.preview_dir.name + ".consuming")
    with _preview_locations_lock:
        try:
            location.preview_dir.rename(consumed_dir)
        except FileNotFoundError as exc:
            _preview_locations.pop(token, None)
            raise ValueError("导入 token 不存在或已失效") from exc
        except OSError as exc:
            raise ValueError("导入预览无法消费") from exc
        _preview_locations.pop(token, None)

    consumed_location = _PreviewLocation(location.temp_root, consumed_dir, consumed_dir / "preview.json")
    try:
        preview, _ = _load_preview_manifest(consumed_location, token)
        return preview
    finally:
        shutil.rmtree(consumed_dir, ignore_errors=True)


class _ExpiredPreviewError(ValueError):
    pass


def _resolve_preview_location(token: str) -> _PreviewLocation:
    if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError("无效的导入 token")

    with _preview_locations_lock:
        location = _preview_locations.get(token)
        if location is None:
            location = _find_persisted_preview(token)
            if location is not None:
                _preview_locations[token] = location
    if location is None:
        raise ValueError("导入 token 不存在或已失效")
    return location


def _find_persisted_preview(token: str) -> Optional[_PreviewLocation]:
    for root in _load_preview_roots():
        preview_dir = root / token
        manifest = preview_dir / "preview.json"
        if preview_dir.is_dir() and manifest.is_file():
            return _PreviewLocation(root, preview_dir, manifest)
    return None


def _load_preview_manifest(
    location: _PreviewLocation, token: str
) -> Tuple[ImportPreview, datetime]:
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
        preview, expires_at = _preview_from_dict(payload)
    except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("导入预览数据无效") from exc
    if preview.token != token:
        raise ValueError("导入预览 token 不匹配")
    if expires_at <= datetime.now(timezone.utc):
        raise _ExpiredPreviewError("导入预览已过期")
    return preview, expires_at


def _remove_preview(token: str, location: _PreviewLocation) -> None:
    with _preview_locations_lock:
        _preview_locations.pop(token, None)
    shutil.rmtree(location.preview_dir, ignore_errors=True)


def _preview_root_registry_path() -> Path:
    configured = os.environ.get(PREVIEW_ROOT_REGISTRY_ENV)
    return Path(configured).expanduser().resolve() if configured else DEFAULT_PREVIEW_ROOT_REGISTRY.resolve()


def _register_preview_root(root: Path) -> None:
    registry = _preview_root_registry_path()
    roots = _load_preview_roots()
    if root in roots:
        return
    roots.append(root)
    registry.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(registry, {"version": 1, "roots": sorted(str(path) for path in roots)})


def _load_preview_roots() -> List[Path]:
    registry = _preview_root_registry_path()
    if not registry.exists():
        return []
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"version", "roots"}:
            raise ValueError
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError
        if not isinstance(payload["roots"], list) or not all(
            isinstance(root, str) and Path(root).is_absolute() for root in payload["roots"]
        ):
            raise ValueError
        roots = [Path(root).resolve() for root in payload["roots"]]
        if len(roots) != len(set(roots)):
            raise ValueError
        return roots
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("导入预览根目录配置无效") from exc


def _read_rows(xlsx_path: Path) -> List[ImportRowPreview]:
    try:
        workbook = load_workbook(xlsx_path, read_only=True, data_only=False)
    except Exception as exc:
        raise ValueError("Excel 文件无法读取") from exc

    try:
        try:
            worksheet = workbook.worksheets[0]
            iterator = worksheet.iter_rows()
            try:
                header_cells = next(iterator)
            except StopIteration as exc:
                raise ValueError("Excel 表头不能为空") from exc
            for cell in header_cells:
                if cell.data_type == "f":
                    raise ValueError(f"Excel 表头不允许公式：{cell.coordinate}")
            headers = _validate_headers(tuple(cell.value for cell in header_cells))
            indexes = {header: index for index, header in enumerate(headers)}

            rows: List[ImportRowPreview] = []
            for row_number, cells in enumerate(iterator, start=2):
                values = tuple(cell.value for cell in cells)
                if _is_blank_row(values):
                    continue
                if len(rows) >= MAX_IMPORT_ROWS:
                    raise ValueError("Excel 最多允许 500 条内容")
                formula_indexes = {
                    index for index, cell in enumerate(cells) if cell.data_type == "f"
                }
                rows.append(
                    _normalize_row(row_number, values, indexes, headers, formula_indexes)
                )
            return rows
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Excel 工作表解析失败") from exc
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
    row_number: int,
    values: Tuple[Any, ...],
    indexes: Dict[str, int],
    headers: Tuple[str, ...],
    formula_indexes: set[int],
) -> ImportRowPreview:
    normalized: Dict[str, Any] = {}
    errors = [
        f"第 {row_number} 行 {headers[index]} 不允许公式"
        for index in sorted(formula_indexes)
        if index < len(headers)
    ]

    for column in IMPORT_COLUMNS:
        index = indexes.get(column)
        raw_value = (
            values[index]
            if index is not None and index < len(values) and index not in formula_indexes
            else None
        )
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
            if len(archive.filelist) > MAX_ZIP_ENTRIES:
                raise ValueError(f"ZIP 文件条目不能超过 {MAX_ZIP_ENTRIES} 个")
            for info in archive.filelist:
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


def _write_preview(path: Path, preview: ImportPreview, expires_at: datetime) -> None:
    payload = _preview_to_dict(preview)
    payload["version"] = PREVIEW_MANIFEST_VERSION
    payload["expires_at"] = expires_at.isoformat()
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{token_urlsafe(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _preview_to_dict(preview: ImportPreview) -> Dict[str, Any]:
    return asdict(preview)


def _preview_from_dict(payload: Dict[str, Any]) -> Tuple[ImportPreview, datetime]:
    if not isinstance(payload, dict) or set(payload) != _PREVIEW_KEYS:
        raise ValueError("预览字段无效")
    if type(payload["version"]) is not int or payload["version"] != PREVIEW_MANIFEST_VERSION:
        raise ValueError("预览版本无效")
    if not isinstance(payload["token"], str) or not _TOKEN_PATTERN.fullmatch(payload["token"]):
        raise ValueError("预览 token 无效")
    expires_at = _parse_manifest_expiry(payload["expires_at"])
    if not isinstance(payload["rows"], list) or len(payload["rows"]) > MAX_IMPORT_ROWS:
        raise ValueError("预览行数无效")
    if not _is_string_list(payload["warnings"]):
        raise ValueError("预览警告无效")

    rows = [_row_from_dict(row) for row in payload["rows"]]
    row_numbers = [row.row_number for row in rows]
    if row_numbers != sorted(set(row_numbers)):
        raise ValueError("预览行号无效")

    valid_count = sum(row.valid for row in rows)
    expected_counts = (len(rows), valid_count, len(rows) - valid_count)
    stored_counts = (
        payload["total_count"],
        payload["valid_count"],
        payload["error_count"],
    )
    if any(type(value) is not int or value < 0 for value in stored_counts):
        raise ValueError("预览计数类型无效")
    if stored_counts != expected_counts:
        raise ValueError("预览计数不匹配")

    return (
        ImportPreview(
            token=payload["token"],
            rows=rows,
            warnings=list(payload["warnings"]),
            total_count=expected_counts[0],
            valid_count=expected_counts[1],
            error_count=expected_counts[2],
        ),
        expires_at,
    )


def _row_from_dict(payload: Any) -> ImportRowPreview:
    if not isinstance(payload, dict) or set(payload) != _ROW_KEYS:
        raise ValueError("预览行字段无效")
    if type(payload["row_number"]) is not int or payload["row_number"] < 2:
        raise ValueError("预览行号无效")
    normalized = payload["normalized"]
    if not isinstance(normalized, dict) or set(normalized) != _NORMALIZED_KEYS:
        raise ValueError("预览标准化字段无效")
    if any(value is not None and not isinstance(value, str) for value in normalized.values()):
        raise ValueError("预览标准化值无效")
    if not _is_string_list(payload["errors"]) or not _is_string_list(payload["warnings"]):
        raise ValueError("预览行消息无效")
    if type(payload["valid"]) is not bool or payload["valid"] != (not payload["errors"]):
        raise ValueError("预览行状态无效")
    return ImportRowPreview(
        row_number=payload["row_number"],
        normalized=dict(normalized),
        errors=list(payload["errors"]),
        warnings=list(payload["warnings"]),
        valid=payload["valid"],
    )


def _parse_manifest_expiry(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("预览过期时间无效")
    expires_at = datetime.fromisoformat(value)
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("预览过期时间无时区")
    expires_at = expires_at.astimezone(timezone.utc)
    if expires_at > datetime.now(timezone.utc) + PREVIEW_TTL + timedelta(minutes=1):
        raise ValueError("预览过期时间超出限制")
    return expires_at


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def cleanup_expired_previews() -> int:
    removed = 0
    now = datetime.now(timezone.utc)
    with _preview_locations_lock:
        for root in _load_preview_roots():
            if not root.is_dir():
                continue
            for preview_dir in root.iterdir():
                if not preview_dir.is_dir() or not _TOKEN_PATTERN.fullmatch(preview_dir.name):
                    continue
                manifest = preview_dir / "preview.json"
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    preview, expires_at = _preview_from_dict(payload)
                    if preview.token != preview_dir.name or expires_at > now:
                        continue
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                _preview_locations.pop(preview.token, None)
                shutil.rmtree(preview_dir, ignore_errors=True)
                removed += 1
    return removed
