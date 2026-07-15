from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.text_review.reviewers.llm import OpenAICompatLLM
from server.services.image_evidence_service import (
    IMAGE_EVIDENCE_MAX_BYTES,
    ImageEvidenceAnalysis,
    analyze_image_evidence,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image-content"


class VisionLLM:
    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls: list[tuple[str, str, object]] = []

    def chat_json_multimodal(self, prompt: str, image_data_uri: str, schema: object) -> str:
        self.calls.append((prompt, image_data_uri, schema))
        if isinstance(self.payload, Exception):
            raise self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def test_analysis_uses_strict_multimodal_result_and_caller_owned_asset_id(tmp_path: Path) -> None:
    image = tmp_path / "scene.png"
    image.write_bytes(PNG)
    llm = VisionLLM({
        "asset_id": "model-must-not-control-this",
        "is_test_scene": True,
        "visible_input": "输入：北京南站到故宫",
        "visible_result": "显示三条路线",
        "visible_product": "百度地图",
        "detected_text": "北京南站 故宫 三条路线",
        "confidence": 0.93,
        "missing_context": ["app_version", "tested_at"],
        "reasoning": "界面同时显示输入和返回路线",
        "status": "ANALYZED",
    })

    result = analyze_image_evidence(
        asset_id="row-owned-asset", image_path=image, filename="scene.png",
        title="路线规划体验", body="正文", llm=llm,
    )

    assert result.asset_id == "row-owned-asset"
    assert result.is_test_scene is True
    assert result.visible_input == "输入：北京南站到故宫"
    assert result.visible_result == "显示三条路线"
    assert result.missing_context == ["app_version", "tested_at"]
    assert llm.calls[0][1].startswith("data:image/png;base64,")
    assert "safe-image-content" not in llm.calls[0][0]


def test_analysis_unavailable_is_explicit_and_never_a_pass(tmp_path: Path) -> None:
    image = tmp_path / "scene.png"
    image.write_bytes(PNG)

    missing_model = analyze_image_evidence(
        asset_id="asset-1", image_path=image, filename="scene.png",
        title="亲测路线", body="正文", llm=None,
    )
    gateway_failure = analyze_image_evidence(
        asset_id="asset-1", image_path=image, filename="scene.png",
        title="亲测路线", body="正文", llm=VisionLLM(RuntimeError("gateway unavailable")),
    )

    for result in (missing_model, gateway_failure):
        assert result.status == "UNAVAILABLE"
        assert result.is_test_scene is False
        assert result.confidence == 0
        assert result.missing_context
        assert "通过" not in result.reasoning


def test_analysis_rejects_path_traversal_oversize_and_mismatched_type(tmp_path: Path) -> None:
    valid = tmp_path / "valid.png"
    valid.write_bytes(PNG)
    oversize = tmp_path / "large.png"
    oversize.write_bytes(b"\x89PNG\r\n\x1a\n")
    mismatched = tmp_path / "fake.png"
    mismatched.write_bytes(b"not-an-image")

    with pytest.raises(ValueError, match="安全文件名"):
        analyze_image_evidence(asset_id="a", image_path=valid, filename="../valid.png", title="", body="", llm=None)
    with pytest.raises(ValueError, match="大小"):
        analyze_image_evidence(
            asset_id="a", image_path=oversize, filename="large.png", title="", body="", llm=None,
            max_bytes=4,
        )
    with pytest.raises(ValueError, match="类型"):
        analyze_image_evidence(asset_id="a", image_path=mismatched, filename="fake.png", title="", body="", llm=None)


def test_analysis_schema_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ImageEvidenceAnalysis.model_validate({
            "asset_id": "a", "status": "ANALYZED", "is_test_scene": False,
            "visible_input": None, "visible_result": None, "visible_product": None,
            "detected_text": "", "confidence": 0, "missing_context": [],
            "reasoning": "none", "unexpected": "not allowed",
        })


def test_oneapi_multimodal_transport_uses_data_uri_and_strict_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "{}"}}]}

    class Requests:
        @staticmethod
        def post(url: str, **kwargs):
            captured.update(url=url, kwargs=kwargs)
            return Response()

    monkeypatch.setenv("ONEAPI_KEY", "secret-key")
    monkeypatch.setenv("ONEAPI_MODEL", "text-model")
    monkeypatch.setenv("ONEAPI_VISION_MODEL", "vision-model")
    client = OpenAICompatLLM()
    client._requests = Requests()

    client.chat_json_multimodal("inspect", "data:image/png;base64,c2FmZQ==", ImageEvidenceAnalysis)

    body = captured["kwargs"]["json"]
    assert body["model"] == "vision-model"
    content = body["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "inspect"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,c2FmZQ==", "detail": "high"}},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert "secret-key" not in json.dumps(body)
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer secret-key"
