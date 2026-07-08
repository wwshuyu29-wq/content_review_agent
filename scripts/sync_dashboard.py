#!/usr/bin/env python3
"""
看板同步脚本：将审核队列状态同步到全员可见的 Ku 数据表。

用法：
  # 首次运行（没有 dist-id）：脚本会提示先用 ku-doc-manage 创建数据表
  python3 sync_dashboard.py --queue queue.jsonl

  # 已有数据表后，增量同步
  python3 sync_dashboard.py --queue queue.jsonl --dist-id dstxxxxxxxx [--view-id viwxxxxxxxx]

设计说明：
  - 首次同步会自动在数据表上补齐所需字段（若字段已存在则跳过，按字段名去重）。
  - 按 content_id 做增量判断：数据表中已存在该 content_id 的记录 -> update，否则 -> add。
    通过给每条记录维护一个 "内容ID" 字段并在同步前拉取现有记录建立 content_id -> recordId 映射实现。
  - 安全策略（不可绕过）：仅 auto_passed / human_approved 状态展示缩略图（Attachment 字段填 bos_url）；
    其余状态（queued_for_review / needs_human / auto_rejected / human_rejected / intake_failed）
    的"缩略图"字段留空，只同步元信息 + 风险标签，避免看板成为违规内容二次扩散渠道。
  - 本脚本调用 ku-doc-manage 的 CLI 二进制（bin/ku），不直接发 HTTP 请求。
"""
import argparse
import json
import os
import subprocess
import sys

FIELDS = [
    ("内容ID", "SingleText", None),
    ("类型", "SingleSelect", {"options": [{"name": "image"}, {"name": "video"}]}),
    ("供应商", "SingleText", None),
    ("缩略图", "Attachment", None),
    ("提交时间", "SingleText", None),
    ("自动审核结论", "SingleSelect", {"options": [
        {"name": "pass"}, {"name": "reject"}, {"name": "needs_human"}, {"name": "-"},
    ]}),
    ("风险等级", "SingleSelect", {"options": [
        {"name": "high"}, {"name": "mid"}, {"name": "low"}, {"name": "-"},
    ]}),
    ("风险类别", "Text", None),
    ("状态", "SingleSelect", {"options": [
        {"name": "queued_for_review"}, {"name": "auto_passed"}, {"name": "auto_rejected"},
        {"name": "needs_human"}, {"name": "human_approved"}, {"name": "human_rejected"},
        {"name": "intake_failed"},
    ]}),
    ("认领人", "SingleText", None),
    ("人工结论", "SingleText", None),
    ("结论时间", "SingleText", None),
    ("备注", "Text", None),
]

APPROVED_STATUS = {"auto_passed", "human_approved"}

KU_BIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ku-doc-manage", "bin", "ku",
)


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


def run_ku(args_list):
    result = subprocess.run(
        [KU_BIN] + args_list, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ku 命令执行失败: {' '.join(args_list)}\nstderr: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"ku 命令输出非 JSON: {result.stdout.strip()}")


def ensure_fields(dist_id):
    """确保数据表已包含所需字段，缺失的补齐；已存在的（按字段名）跳过。"""
    resp = run_ku(["get-datasheet-fields", "--dist-id", dist_id])
    existing_names = {f.get("name") for f in resp.get("result", {}).get("fields", [])}

    for name, ftype, prop in FIELDS:
        if name in existing_names:
            continue
        args = ["add-datasheet-field", "--dist-id", dist_id, "--type", ftype, "--name", name]
        if prop is not None:
            args += ["--property", json.dumps(prop, ensure_ascii=False)]
        run_ku(args)
        print(f"[字段] 已创建缺失字段: {name}", file=sys.stderr)


def fetch_existing_map(dist_id, view_id):
    """拉取数据表现有记录，建立 content_id -> recordId 映射。"""
    args = ["get-datasheet-records", "--dist-id", dist_id, "--page-size", "1000"]
    if view_id:
        args += ["--view-id", view_id]
    resp = run_ku(args)
    records = resp.get("result", {}).get("records", [])
    mapping = {}
    for r in records:
        cid = r.get("fields", {}).get("内容ID")
        if cid:
            mapping[cid] = r["recordId"]
    return mapping


def build_fields(record):
    status = record.get("status", "")
    show_thumbnail = status in APPROVED_STATUS
    risk_categories = record.get("risk_categories") or []
    fields = {
        "内容ID": record["content_id"],
        "类型": record.get("content_type") or "",
        "供应商": record.get("supplier_id") or "",
        "提交时间": record.get("submit_time") or "",
        "自动审核结论": record.get("auto_verdict") or "-",
        "风险等级": record.get("risk_level") or "-",
        "风险类别": ",".join(risk_categories) if risk_categories else "",
        "状态": status,
        "认领人": record.get("claimed_by") or "",
        "人工结论": record.get("human_decision") or "",
        "结论时间": record.get("human_decision_time") or "",
        "备注": record.get("auto_reason") or record.get("error") or "",
    }
    if show_thumbnail and record.get("bos_url"):
        fields["缩略图"] = [{"url": record["bos_url"], "name": f"{record['content_id']}"}]
    else:
        fields["缩略图"] = []
        if status in ("needs_human",) or (record.get("risk_categories") and set(record["risk_categories"]) & {"illegal", "porn_vulgar", "violence_terror"}):
            fields["备注"] = (fields["备注"] + " | 内容待审核/高风险，原图不在看板展示，需通过 dodo_cli bos url 走内网授权访问").strip(" |")
    return fields


def main():
    parser = argparse.ArgumentParser(description="队列状态同步到 Ku 看板")
    parser.add_argument("--queue", required=True)
    parser.add_argument("--dist-id", help="Ku 数据表 ID；不传则提示先创建")
    parser.add_argument("--view-id", default=None, help="视图 ID，不传则用默认视图")
    args = parser.parse_args()

    if not args.dist_id:
        print(
            "未提供 --dist-id。请先用 ku-doc-manage 的 create-datasheet 在一个数据表夹文档下创建数据表，"
            "并将返回的 dstId 作为 --dist-id 传入本脚本。",
            file=sys.stderr,
        )
        sys.exit(1)

    records = load_queue(args.queue)
    if not records:
        print("队列为空，无需同步", file=sys.stderr)
        return

    ensure_fields(args.dist_id)
    existing_map = fetch_existing_map(args.dist_id, args.view_id)

    to_add = []
    to_update = []
    for r in records:
        fields = build_fields(r)
        rec_id = existing_map.get(r["content_id"])
        if rec_id:
            to_update.append({"recordId": rec_id, "fields": fields})
        else:
            to_add.append({"fields": fields})

    add_args = ["add-datasheet-records", "--dist-id", args.dist_id, "--records", json.dumps(to_add, ensure_ascii=False)]
    if args.view_id:
        add_args += ["--view-id", args.view_id]
    if to_add:
        run_ku(add_args)

    update_args = ["update-datasheet-records", "--dist-id", args.dist_id, "--records", json.dumps(to_update, ensure_ascii=False)]
    if args.view_id:
        update_args += ["--view-id", args.view_id]
    if to_update:
        run_ku(update_args)

    print(f"看板同步完成：新增 {len(to_add)} 条，更新 {len(to_update)} 条，数据表: {args.dist_id}")


if __name__ == "__main__":
    main()
