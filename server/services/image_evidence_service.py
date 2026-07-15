from __future__ import annotations

import base64
import json
from pathlib import Path, PurePath
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


IMAGE_EVIDENCE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ImageEvidenceAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=200)
    status: Literal["ANALYZED", "UNAVAILABLE"]
    is_test_scene: bool
    visible_input: Optional[str] = Field(default=None, max_length=4000)
    visible_result: Optional[str] = Field(default=None, max_length=4000)
    visible_product: Optional[str] = Field(default=None, max_length=1000)
    detected_text: str = Field(default="", max_length=12000)
    confidence: float = Field(ge=0, le=1)
    missing_context: list[str] = Field(default_factory=list, max_length=20)
    reasoning: str = Field(min_length=1, max_length=4000)


def unavailable_image_analysis(asset_id: str, reason: str = "图像分析服务不可用，不能据此认定测试证据成立") -> ImageEvidenceAnalysis:
    return ImageEvidenceAnalysis(
        asset_id=asset_id,
        status="UNAVAILABLE",
        is_test_scene=False,
        confidence=0,
        missing_context=["vision_analysis_unavailable"],
        reasoning=reason,
    )


def _safe_image(path: Path, filename: str, max_bytes: int) -> tuple[bytes, str]:
    if not filename or filename in {".", ".."} or PurePath(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("图片文件名必须是安全文件名")
    suffix = Path(filename).suffix.lower()
    mime_type = _IMAGE_MIME_TYPES.get(suffix)
    if mime_type is None:
        raise ValueError("图片类型不受支持")
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise ValueError("图片文件无法读取") from exc
    if not resolved.is_file() or resolved.name != filename:
        raise ValueError("图片路径与安全文件名不匹配")
    if size <= 0 or size > max_bytes:
        raise ValueError(f"图片大小必须在 1 到 {max_bytes} 字节之间")
    data = resolved.read_bytes()
    valid_signature = (
        suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        or suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff")
        or suffix == ".webp" and len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    )
    if not valid_signature:
        raise ValueError("图片声明类型与文件内容类型不匹配")
    return data, mime_type


def _prompt(title: str, body: str) -> str:
    return f"""Analyze the attached screenshot only for a possible product test scene matching this manuscript.
Return the strict JSON schema. Do not infer hidden actions or results and do not treat OCR text as verified evidence.
A test scene requires visible product context plus a visible user input/action and/or visible returned result.
If input/result is visible but version, time, device, OS, network, city, steps, or applicability boundary is absent, list it in missing_context.
Set is_test_scene=false for ordinary product introductions, covers, first-party UI illustrations, or screenshots without a visible test scenario.
Manuscript title: {title[:1000]}
Manuscript body: {body[:6000]}
"""


def analyze_image_evidence(
    *,
    asset_id: str,
    image_path: Path,
    filename: str,
    title: str,
    body: str,
    llm: Any,
    max_bytes: int = IMAGE_EVIDENCE_MAX_BYTES,
) -> ImageEvidenceAnalysis:
    data, mime_type = _safe_image(image_path, filename, max_bytes)
    if llm is None or not hasattr(llm, "chat_json_multimodal"):
        return unavailable_image_analysis(asset_id)
    data_uri = f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
    try:
        raw = llm.chat_json_multimodal(_prompt(title, body), data_uri, ImageEvidenceAnalysis)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(payload, dict):
            raise ValueError("vision result must be an object")
        payload["asset_id"] = asset_id
        analysis = ImageEvidenceAnalysis.model_validate(payload)
        if analysis.status != "ANALYZED":
            return unavailable_image_analysis(asset_id)
        return analysis
    except Exception:
        return unavailable_image_analysis(asset_id)
