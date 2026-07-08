#!/usr/bin/env python3
"""
Agent 主循环 —— 自动拉取待审内容，调用文心（ERNIE）视觉 API 分析，写回 verdict。

设计说明：
  1. 调用 auto_review.py list-pending 拉取待审条目（含 analyze_paths）
  2. 对每张图片/关键帧 base64 编码，调用百度文心 ERNIE Vision API
  3. 解析 API 返回文本，提取 risk_categories / confidence / reason
  4. 调用 auto_review.py record-verdict 写回决策（脚本自行套用规则，不由本模块覆盖）

用法：
  # 单次运行（跑完一批就退出）
  python3 scripts/agent_loop.py --queue queue.jsonl

  # 持续监听（每 N 秒轮询一次）
  python3 scripts/agent_loop.py --queue queue.jsonl --watch --interval 30

环境变量（必须）：
  ERNIE_API_KEY        文心 API Key（千帆平台 / 文心一言开放平台）
  ERNIE_SECRET_KEY     文心 Secret Key

可选环境变量：
  ERNIE_MODEL          默认 ernie-4.5-vl-8b-preview，可换 ernie-4.0-vl-8b 等
  ERNIE_ENDPOINT       若使用自定义部署地址可覆盖此环境变量

依赖：pip install requests
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────
# ERNIE Vision 接口封装
# ──────────────────────────────────────────────

ERNIE_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"

_cached_token: dict[str, object] = {}  # {"token": str, "expires_at": float}


def _get_access_token() -> str:
    """获取/刷新 ERNIE access token（有效期约 30 天，提前 5 分钟刷新）。"""
    global _cached_token
    now = time.time()
    if _cached_token.get("token") and _cached_token.get("expires_at", 0) > now + 300:
        return _cached_token["token"]

    api_key = os.environ.get("ERNIE_API_KEY", "")
    secret_key = os.environ.get("ERNIE_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise EnvironmentError(
            "缺少 ERNIE_API_KEY 或 ERNIE_SECRET_KEY 环境变量。"
            "请在千帆平台 / 文心一言开放平台获取并 export 到环境中。"
        )

    resp = requests.post(
        ERNIE_TOKEN_URL,
        params={"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    _cached_token = {
        "token": data["access_token"],
        "expires_at": now + int(data.get("expires_in", 2592000)),
    }
    return _cached_token["token"]


def _encode_image(path: str) -> str:
    """将本地图片文件 base64 编码（ERNIE API 需要 base64 字符串，不带 data URI 前缀）。"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# 审核 prompt —— 严格对照 references/review_standards.md 风险类别
_REVIEW_PROMPT = """你是一名专业内容安全审核员。请分析这张图片，严格按照下列风险类别判断是否存在风险。

风险类别（英文标识）：
- illegal: 违法违规（毒品/赌博/武器交易等）
- porn_vulgar: 色情低俗（裸露/性暗示/低俗擦边）
- violence_terror: 暴力血腥/恐怖（血腥画面/极端暴力/恐怖主义符号）
- ad_diversion: 广告导流（二维码引流/联系方式导流）
- privacy: 隐私泄露（清晰人脸+身份信息/证件号/手机号/住址）
- copyright: 版权侵权（盗用他人作品/水印被抹除痕迹）
- rumor_fake: 谣言/虚假信息（摆拍冒充实况/明显编造信息）
- brand_compliance: 品牌/合规风险（违反品牌调性/竞品敏感/涉政敏感）

如果图片内容无法清晰判断（图片损坏、过度模糊、内容不可见），请在 risk_categories 中填写 ["unknown"]。

请用如下 JSON 格式回复，不要输出 JSON 以外的内容：
{
  "risk_categories": [],
  "confidence": 0.0,
  "reason": ""
}

说明：
- risk_categories: 命中的风险类别列表，无风险时为空数组 []，无法判断时填 ["unknown"]
- confidence: 整体判断置信度，0~1 之间的浮点数
- reason: 简要说明判断依据（中文，50字以内）
"""


def analyze_image(image_path: str) -> dict:
    """调用 ERNIE Vision API 分析单张图片，返回 {risk_categories, confidence, reason}。"""
    model = os.environ.get("ERNIE_MODEL", "ernie-4.5-vl-8b-preview")
    endpoint = os.environ.get(
        "ERNIE_ENDPOINT",
        f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}",
    )

    token = _get_access_token()
    image_b64 = _encode_image(image_path)

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_base64", "image_base64": image_b64},
                    {"type": "text", "text": _REVIEW_PROMPT},
                ],
            }
        ],
        "temperature": 0.1,
        "top_p": 0.7,
    }

    resp = requests.post(
        endpoint,
        params={"access_token": token},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    if "error_code" in data:
        raise RuntimeError(f"ERNIE API 错误 {data['error_code']}: {data.get('error_msg')}")

    raw_text: str = data.get("result", "")
    return _parse_verdict_text(raw_text)


def _parse_verdict_text(text: str) -> dict:
    """从模型回复文本中提取 JSON。允许模型回复前后有少量说明文字。"""
    text = text.strip()
    # 尝试直接解析
    try:
        result = json.loads(text)
        return _normalize_verdict(result)
    except json.JSONDecodeError:
        pass
    # 找到第一个 { ... } 块
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            result = json.loads(text[start : end + 1])
            return _normalize_verdict(result)
        except json.JSONDecodeError:
            pass
    # 无法解析 → 标记为 unknown，强制转人工
    return {
        "risk_categories": ["unknown"],
        "confidence": 0.0,
        "reason": f"模型回复无法解析为 JSON，原始内容: {text[:100]}",
    }


def _normalize_verdict(d: dict) -> dict:
    """确保字段类型正确，防止后续脚本报错。"""
    cats = d.get("risk_categories", [])
    if not isinstance(cats, list):
        cats = []
    confidence = float(d.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    reason = str(d.get("reason", "")).strip() or "无详细说明"
    return {"risk_categories": cats, "confidence": confidence, "reason": reason}


# ──────────────────────────────────────────────
# 与 auto_review.py 的集成
# ──────────────────────────────────────────────


def list_pending(queue_path: str, frames_dir: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "auto_review.py"),
         "list-pending", "--queue", queue_path, "--frames-dir", frames_dir],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"list-pending 失败: {result.stderr.strip()}")
    return json.loads(result.stdout)


def record_verdict(queue_path: str, content_id: str, risk_categories: list[str],
                   confidence: float, reason: str) -> dict:
    cats_str = ",".join(risk_categories) if risk_categories else ""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "auto_review.py"),
         "record-verdict",
         "--queue", queue_path,
         "--content-id", content_id,
         "--risk-categories", cats_str,
         "--confidence", str(confidence),
         "--reason", reason],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"record-verdict 失败: {result.stderr.strip()}")
    return json.loads(result.stdout)


# ──────────────────────────────────────────────
# 主处理逻辑
# ──────────────────────────────────────────────


def process_one(item: dict, queue_path: str) -> None:
    """处理单条待审记录：分析所有图片/帧，合并结论，写回 verdict。"""
    content_id: str = item["content_id"]
    analyze_paths: list[str] = item.get("analyze_paths") or []
    video_note: str = item.get("video_note", "")
    content_type: str = item.get("content_type", "image")

    if not analyze_paths:
        # 没有可分析路径（可能抽帧失败），标记 unknown 转人工
        record_verdict(queue_path, content_id, ["unknown"], 0.0,
                       item.get("error") or "无可分析路径，可能是抽帧失败")
        print(f"  [{content_id}] 无可分析路径，转人工")
        return

    # 逐张分析，合并高风险覆盖
    all_cats: set[str] = set()
    min_confidence = 1.0
    reasons: list[str] = []

    for idx, path in enumerate(analyze_paths):
        if not os.path.exists(path):
            print(f"  [{content_id}] 帧文件不存在: {path}，跳过", file=sys.stderr)
            continue
        try:
            verdict = analyze_image(path)
        except Exception as e:  # noqa: BLE001
            print(f"  [{content_id}] 分析图片失败 ({path}): {e}", file=sys.stderr)
            verdict = {"risk_categories": ["unknown"], "confidence": 0.0,
                       "reason": f"API调用失败: {e}"}

        cats = verdict["risk_categories"]
        conf = verdict["confidence"]
        reason = verdict["reason"]

        all_cats.update(cats)
        min_confidence = min(min_confidence, conf)

        frame_label = f"帧{idx + 1}" if content_type == "video" else "图片"
        if cats and cats != ["unknown"] or cats == ["unknown"]:
            reasons.append(f"{frame_label}: {reason}")

        # 一旦命中高风险，后续帧仍继续分析（完整留痕）但已注定 needs_human
        # 如果帧数很多可以在此 break 提前结束（可选优化，当前全量分析）

    # 如果所有帧都没有问题
    if not reasons:
        reasons = ["所有帧均未发现风险"]

    if video_note:
        reasons.append(video_note)

    final_reason = "；".join(reasons)[:500]  # 截断避免超长
    final_cats = sorted(all_cats)

    verdict_result = record_verdict(
        queue_path, content_id, final_cats,
        round(min_confidence, 4), final_reason,
    )
    print(f"  [{content_id}] {content_type} → "
          f"verdict={verdict_result.get('verdict')} "
          f"risk={verdict_result.get('risk_level')} "
          f"cats={final_cats}")


def run_once(queue_path: str, frames_dir: str) -> int:
    """单次处理所有待审内容，返回处理条数。"""
    pending = list_pending(queue_path, frames_dir)
    if not pending:
        return 0
    print(f"拉取到 {len(pending)} 条待审内容")
    for item in pending:
        try:
            process_one(item, queue_path)
        except Exception as e:  # noqa: BLE001
            print(f"  [{item.get('content_id')}] 处理异常: {e}", file=sys.stderr)
    return len(pending)


def main() -> None:
    parser = argparse.ArgumentParser(description="内容审核 Agent 主循环（文心视觉）")
    parser.add_argument("--queue", required=True, help="queue.jsonl 路径")
    parser.add_argument("--frames-dir", default="/tmp/content-review-frames",
                        help="视频关键帧临时目录")
    parser.add_argument("--watch", action="store_true",
                        help="持续监听模式，每隔 --interval 秒轮询一次")
    parser.add_argument("--interval", type=int, default=30,
                        help="watch 模式下轮询间隔（秒），默认 30")
    args = parser.parse_args()

    os.makedirs(args.frames_dir, exist_ok=True)

    if not args.watch:
        count = run_once(args.queue, args.frames_dir)
        print(f"完成，共处理 {count} 条")
    else:
        print(f"持续监听模式启动，轮询间隔 {args.interval}s，Ctrl+C 退出")
        while True:
            try:
                count = run_once(args.queue, args.frames_dir)
                if count == 0:
                    print(f"队列为空，{args.interval}s 后再次检查...")
            except Exception as e:  # noqa: BLE001
                print(f"本轮出错: {e}", file=sys.stderr)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
