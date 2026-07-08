#!/usr/bin/env python3
"""
供应商内容摆渡入库脚本。

输入：供应商提交清单 (manifest)，JSON 数组，每条记录格式：
  {
    "supplier_id": "supplier_001",
    "content_type": "image" | "video",
    "source_url": "https://...",   # 供应商侧免费入口产生的直链
    "submit_time": "2026-07-06T10:00:00"
  }

流程：
  1. 逐条下载 source_url 到本地临时目录
  2. 校验格式（image: jpg/png/webp；video: mp4/mov）与大小上限
  3. 调用 dodo_cli bos upload 写入内网私有存储（不加 --no-ath，默认私有）
  4. 生成待审队列记录，追加写入 queue.jsonl

用法：
  python3 sync_intake.py --manifest manifest.json --queue queue.jsonl [--tmp-dir /tmp/intake] [--max-size-mb 200]

设计说明：
  - 本脚本只负责"外部 -> 内网 BOS"这一次性摆渡动作，供应商侧永远不会拿到内网凭证，
    内网侧也不会直接访问供应商存储，满足"供应商与内网存储隔离"的要求。
  - source_url 允许来自任意外部免费入口（表单导出直链 / 供应商自备对象存储直链），
    脚本不假设具体供应商平台。
"""
import argparse
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.request
import uuid

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def guess_ext(url: str, content_type: str) -> str:
    path = url.split("?")[0]
    _, ext = os.path.splitext(path)
    if ext:
        return ext.lower()
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed or ""


def download(url: str, dest_dir: str, max_size_mb: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "content-review-agent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type", "")
        ext = guess_ext(url, content_type)
        local_path = os.path.join(dest_dir, f"{uuid.uuid4().hex}{ext}")
        max_bytes = max_size_mb * 1024 * 1024
        total = 0
        with open(local_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"文件超过大小上限 {max_size_mb}MB，已中止下载")
                f.write(chunk)
    return local_path


def classify_ext(content_type: str, local_path: str) -> bool:
    ext = os.path.splitext(local_path)[1].lower()
    if content_type == "image":
        return ext in IMAGE_EXTS
    if content_type == "video":
        return ext in VIDEO_EXTS
    return False


def bos_upload(local_path: str) -> dict:
    """调用 dodo_cli bos upload，解析 remote_key 与 download url。"""
    result = subprocess.run(
        ["dodo_cli", "bos", "upload", local_path],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"dodo_cli bos upload 失败: {result.stderr.strip()}")
    remote_key = None
    url = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("uploaded:"):
            # "Uploaded: <local_path> -> <remote_key>"
            if "->" in line:
                remote_key = line.split("->", 1)[1].strip()
        if line.lower().startswith("url:"):
            url = line.split(":", 1)[1].strip()
    if not remote_key:
        raise RuntimeError(f"未能从 dodo_cli 输出解析 remote_key，原始输出:\n{result.stdout}")
    return {"remote_key": remote_key, "bos_url": url}


def main():
    parser = argparse.ArgumentParser(description="供应商内容摆渡入库")
    parser.add_argument("--manifest", required=True, help="供应商提交清单 JSON 文件路径")
    parser.add_argument("--queue", required=True, help="输出队列 JSONL 文件路径（追加写入）")
    parser.add_argument("--tmp-dir", default="/tmp/content-review-intake", help="下载临时目录")
    parser.add_argument("--max-size-mb", type=int, default=200, help="单文件大小上限（MB）")
    args = parser.parse_args()

    os.makedirs(args.tmp_dir, exist_ok=True)

    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    results = []
    for item in manifest:
        content_id = uuid.uuid4().hex[:12]
        record = {
            "content_id": content_id,
            "supplier_id": item.get("supplier_id"),
            "content_type": item.get("content_type"),
            "source_url": item.get("source_url"),
            "submit_time": item.get("submit_time"),
            "intake_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "intake_failed",
        }
        try:
            local_path = download(item["source_url"], args.tmp_dir, args.max_size_mb)
            if not classify_ext(item.get("content_type"), local_path):
                raise ValueError(f"文件扩展名与声明的 content_type={item.get('content_type')} 不匹配")
            bos_info = bos_upload(local_path)
            record["remote_key"] = bos_info["remote_key"]
            record["bos_url"] = bos_info["bos_url"]
            record["local_path"] = local_path
            record["status"] = "queued_for_review"
        except Exception as e:  # noqa: BLE001
            record["error"] = str(e)
            print(f"[FAIL] {item.get('source_url')}: {e}", file=sys.stderr)
        results.append(record)

    with open(args.queue, "a", encoding="utf-8") as f:
        for record in results:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r["status"] == "queued_for_review")
    print(f"摆渡完成：成功 {ok}/{len(results)}，队列文件: {args.queue}")


if __name__ == "__main__":
    main()
