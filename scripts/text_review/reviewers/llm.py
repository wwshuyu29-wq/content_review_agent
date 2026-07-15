"""LLM 客户端（可插拔）。

get_llm(backend):
  - "oneapi" -> OpenAICompatLLM（公司内部 OneAPI 网关，OpenAI 兼容，推荐）
  - "ernie"  -> ErnieLLM（文心开放平台，需 access_token）
  - 其它/"heuristic" -> None（离线，语义维度弃权转人工）

dodo-happywork-v1-internal 等内网模型：若也走 OneAPI，直接用 "oneapi" 后端 + 对应 model 名即可；
若是独立协议，仿照 ErnieLLM 新增一个类，实现 chat() 即可。
"""
from __future__ import annotations

import copy
import os
import re
from typing import Any


def oneapi_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a JSON Schema to OneAPI strict mode without mutating its source."""
    adapted = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(adapted)
    return adapted


class OpenAICompatLLM:
    """OpenAI 兼容网关客户端（适配公司内部 OneAPI）。

    环境变量：
      ONEAPI_BASE_URL  API 基址，默认 https://oneapi-comate.baidu-int.com/v1
      ONEAPI_KEY       令牌（OneAPI 控制台 /mine 领取），必填
      ONEAPI_MODEL     模型名（如 ernie-4.0-8k / gpt-4o / claude-3-5-sonnet 等），必填
    """
    name = "oneapi"

    def __init__(
        self,
        *,
        model: str | None = None,
        vision_model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        import requests
        self._requests = requests
        self.base_url = (base_url or os.environ.get(
            "ONEAPI_BASE_URL", "https://oneapi-comate.baidu-int.com/v1"
        )).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("ONEAPI_KEY", "")
        self.model = model if model is not None else os.environ.get("ONEAPI_MODEL", "")
        configured_vision_model = vision_model if vision_model is not None else os.environ.get("ONEAPI_VISION_MODEL", "")
        self.vision_model = configured_vision_model or self.model

    def _request_content(
        self,
        content: Any,
        response_format: dict[str, Any] | None = None,
        *,
        model: str | None = None,
    ) -> str:
        if not self.api_key:
            raise EnvironmentError("缺少 ONEAPI_KEY 环境变量（在 OneAPI 控制台 /mine 领取）")
        if not self.model:
            raise EnvironmentError("缺少 ONEAPI_MODEL 环境变量（如 ernie-4.0-8k / gpt-4o）")
        selected_model = model or self.model
        if not selected_model:
            raise EnvironmentError("缺少 OneAPI 模型配置")
        body = {
            "model": selected_model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        }
        if response_format is not None:
            body["response_format"] = response_format
        r = self._requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        status_code = getattr(r, "status_code", 200)
        if status_code >= 400:
            try:
                error = r.json().get("error", {})
            except (AttributeError, TypeError, ValueError):
                error = {}
            if isinstance(error, dict):
                details = "; ".join(
                    f"{key}={error[key]}" for key in ("type", "code", "message") if error.get(key)
                )
            else:
                details = str(error)
            sanitized = re.sub(r"https?://\S+", "[已隐藏URL]", details)
            if self.api_key:
                sanitized = sanitized.replace(self.api_key, "[已隐藏凭据]")
            suffix = f": {sanitized}" if sanitized else ""
            raise RuntimeError(f"OneAPI 请求失败 (HTTP {status_code}){suffix}")
        r.raise_for_status()
        data = r.json()
        if "error" in data and data["error"]:
            raise RuntimeError(f"OneAPI 错误: {data['error']}")
        return data["choices"][0]["message"]["content"]

    def _request(self, prompt: str, response_format: dict[str, Any] | None = None) -> str:
        return self._request_content(prompt, response_format)

    def chat(self, prompt: str) -> str:
        return self._request(prompt)

    def chat_json(self, prompt: str, schema: Any) -> str:
        json_schema = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema
        return self._request(
            prompt,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_review_result",
                    "strict": True,
                    "schema": oneapi_strict_schema(json_schema),
                },
            },
        )

    def chat_json_multimodal(self, prompt: str, image_data_uri: str, schema: Any) -> str:
        json_schema = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema
        return self._request_content(
            [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_uri, "detail": "high"}},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "image_evidence_analysis",
                    "strict": True,
                    "schema": oneapi_strict_schema(json_schema),
                },
            },
            model=self.vision_model,
        )


class ErnieLLM:
    name = "ernie"
    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"

    def __init__(self):
        import requests
        self._requests = requests
        self._token = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        ak = os.environ.get("ERNIE_API_KEY", "")
        sk = os.environ.get("ERNIE_SECRET_KEY", "")
        if not ak or not sk:
            raise EnvironmentError("缺少 ERNIE_API_KEY / ERNIE_SECRET_KEY 环境变量")
        r = self._requests.post(
            self.TOKEN_URL,
            params={"grant_type": "client_credentials", "client_id": ak, "client_secret": sk},
            timeout=15,
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def chat(self, prompt: str) -> str:
        model = os.environ.get("ERNIE_MODEL", "ernie-4.0-8k")
        endpoint = os.environ.get(
            "ERNIE_TEXT_ENDPOINT",
            f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}",
        )
        r = self._requests.post(
            endpoint, params={"access_token": self._get_token()},
            json={"messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if "error_code" in data:
            raise RuntimeError(f"ERNIE 错误 {data['error_code']}: {data.get('error_msg')}")
        return data.get("result", "")


def get_llm(backend: str, *, model: str | None = None, vision_model: str | None = None):
    if backend == "oneapi":
        return OpenAICompatLLM(model=model, vision_model=vision_model)
    if backend == "ernie":
        return ErnieLLM()
    return None
