#!/usr/bin/env python3
"""
人工审核队列操作脚本：认领（claim）与提交结论（submit）。

用法：
  # 查看当前所有 needs_human 记录
  python3 human_queue.py list --queue queue.jsonl

  # 认领一条记录（全员可认领，认领后独占，避免重复劳动）
  python3 human_queue.py claim --queue queue.jsonl --content-id abc123 --reviewer zhangsan

  # 提交人工结论（命中高风险类别时 --reason 必填且不能为空）
  python3 human_queue.py submit --queue queue.jsonl --content-id abc123 \
      --reviewer zhangsan --decision approved --reason "人工复核确认无风险"
"""
import argparse
import json
import os
import sys
import time

HIGH_RISK = {"illegal", "porn_vulgar", "violence_terror"}


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


def cmd_list(args):
    records = load_queue(args.queue)
    pending = [r for r in records if r.get("status") == "needs_human"]
    print(json.dumps(pending, ensure_ascii=False, indent=2))


def cmd_claim(args):
    records = load_queue(args.queue)
    for r in records:
        if r["content_id"] == args.content_id:
            if r.get("status") != "needs_human":
                print(f"记录当前状态为 {r.get('status')}，不是待人工审核状态", file=sys.stderr)
                sys.exit(1)
            if r.get("claimed_by") and r.get("claimed_by") != args.reviewer:
                print(f"已被 {r['claimed_by']} 认领，认领时间 {r.get('claim_time')}", file=sys.stderr)
                sys.exit(1)
            r["claimed_by"] = args.reviewer
            r["claim_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_queue(args.queue, records)
            print(json.dumps({"content_id": args.content_id, "claimed_by": args.reviewer}, ensure_ascii=False))
            return
    print(f"未找到 content_id={args.content_id}", file=sys.stderr)
    sys.exit(1)


def cmd_submit(args):
    if not args.reason or not args.reason.strip():
        print("提交人工结论必须填写 --reason", file=sys.stderr)
        sys.exit(1)

    records = load_queue(args.queue)
    for r in records:
        if r["content_id"] == args.content_id:
            if r.get("claimed_by") != args.reviewer:
                print(f"未认领该记录（当前认领人：{r.get('claimed_by')}），请先 claim", file=sys.stderr)
                sys.exit(1)
            risk_cats = set(r.get("risk_categories") or [])
            if risk_cats & HIGH_RISK and len(args.reason.strip()) < 5:
                print("命中高风险类别，理由说明过于简单，请补充具体判断依据", file=sys.stderr)
                sys.exit(1)
            r["human_decision"] = args.decision
            r["human_reason"] = args.reason
            r["human_reviewer"] = args.reviewer
            r["human_decision_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            r["status"] = "human_approved" if args.decision == "approved" else "human_rejected"
            save_queue(args.queue, records)
            print(json.dumps({"content_id": args.content_id, "status": r["status"]}, ensure_ascii=False))
            return
    print(f"未找到 content_id={args.content_id}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="人工审核队列操作")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("list", help="列出所有待人工审核记录")
    p1.add_argument("--queue", required=True)
    p1.set_defaults(func=cmd_list)

    p2 = sub.add_parser("claim", help="认领一条待审记录")
    p2.add_argument("--queue", required=True)
    p2.add_argument("--content-id", required=True)
    p2.add_argument("--reviewer", required=True)
    p2.set_defaults(func=cmd_claim)

    p3 = sub.add_parser("submit", help="提交人工审核结论")
    p3.add_argument("--queue", required=True)
    p3.add_argument("--content-id", required=True)
    p3.add_argument("--reviewer", required=True)
    p3.add_argument("--decision", required=True, choices=["approved", "rejected"])
    p3.add_argument("--reason", required=True)
    p3.set_defaults(func=cmd_submit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
