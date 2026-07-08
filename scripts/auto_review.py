#!/usr/bin/env python3
"""
自动审核辅助脚本。

本脚本本身不做"看图判断"（那部分由调用方 agent 用多模态能力完成），
只负责：
  1. list-pending：从队列中找出待审记录，图片直接给出本地路径；
     视频先调用 extract_frames.py 抽关键帧，再给出帧路径列表。
  2. record-verdict：接收 agent 对某条记录的图片/关键帧分析结果
     （risk_categories + confidence），套用 references/review_standards.md
     中定义的决策规则算出最终 verdict，写回队列文件。

决策规则（与 references/review_standards.md 保持一致，修改规则请同步改两处）：
  - 命中 illegal/porn_vulgar/violence_terror 中任意一个 -> needs_human（高风险强制人工）
  - 命中 privacy/copyright/rumor_fake/brand_compliance 中任意一个：
      confidence >= 0.85 -> reject
      否则 -> needs_human
  - 未命中任何类别 -> pass
  - risk_categories 包含 "unknown"（模型明确表示无法判断） -> needs_human

用法：
  python3 auto_review.py list-pending --queue queue.jsonl --frames-dir /tmp/content-review-frames
  python3 auto_review.py record-verdict --queue queue.jsonl --content-id abc123 \
      --risk-categories porn_vulgar --confidence 0.92 --reason "画面存在裸露内容"
"""
import argparse
import json
import os
import subprocess
import sys

HIGH_RISK = {"illegal", "porn_vulgar", "violence_terror"}
MID_RISK = {"privacy", "copyright", "rumor_fake", "brand_compliance"}
MID_RISK_REJECT_THRESHOLD = 0.85

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_queue(path):
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def save_queue(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cmd_list_pending(args):
    records = load_queue(args.queue)
    pending = [r for r in records if r.get("status") == "queued_for_review"]
    output = []
    for r in pending:
        item = {"content_id": r["content_id"], "content_type": r.get("content_type")}
        if r.get("content_type") == "image":
            item["analyze_paths"] = [r.get("local_path")]
        elif r.get("content_type") == "video":
            frames_out = os.path.join(args.frames_dir, r["content_id"])
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPT_DIR, "extract_frames.py"),
                 "--video", r.get("local_path"), "--out-dir", frames_out,
                 "--interval", str(args.interval)],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                item["error"] = result.stderr.strip()
            else:
                frame_info = json.loads(result.stdout)
                item["analyze_paths"] = frame_info["frames"]
                item["video_note"] = "关键帧抽样审核，无法覆盖音频/字幕，建议人工快速抽查完整视频"
        output.append(item)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def decide_verdict(risk_categories, confidence):
    cats = set(risk_categories)
    if "unknown" in cats:
        return "needs_human", "high"
    if cats & HIGH_RISK:
        return "needs_human", "high"
    if cats & MID_RISK:
        if confidence >= MID_RISK_REJECT_THRESHOLD:
            return "reject", "mid"
        return "needs_human", "mid"
    return "pass", "low"


def cmd_record_verdict(args):
    records = load_queue(args.queue)
    risk_categories = [c.strip() for c in args.risk_categories.split(",") if c.strip()] if args.risk_categories else []
    verdict, risk_level = decide_verdict(risk_categories, args.confidence)

    found = False
    for r in records:
        if r["content_id"] == args.content_id:
            r["auto_verdict"] = verdict
            r["risk_categories"] = risk_categories
            r["risk_level"] = risk_level
            r["confidence"] = args.confidence
            r["auto_reason"] = args.reason
            r["status"] = "auto_passed" if verdict == "pass" else (
                "auto_rejected" if verdict == "reject" else "needs_human"
            )
            found = True
            break
    if not found:
        print(f"未找到 content_id={args.content_id}", file=sys.stderr)
        sys.exit(1)

    save_queue(args.queue, records)
    print(json.dumps({"content_id": args.content_id, "verdict": verdict, "risk_level": risk_level}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="自动审核辅助脚本")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("list-pending", help="列出待审记录及可分析的图片/关键帧路径")
    p1.add_argument("--queue", required=True)
    p1.add_argument("--frames-dir", default="/tmp/content-review-frames")
    p1.add_argument("--interval", type=float, default=5.0)
    p1.set_defaults(func=cmd_list_pending)

    p2 = sub.add_parser("record-verdict", help="写入 agent 的分析结果并套用决策规则")
    p2.add_argument("--queue", required=True)
    p2.add_argument("--content-id", required=True)
    p2.add_argument("--risk-categories", default="", help="逗号分隔，如 porn_vulgar,privacy；无风险留空")
    p2.add_argument("--confidence", type=float, required=True)
    p2.add_argument("--reason", required=True)
    p2.set_defaults(func=cmd_record_verdict)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
