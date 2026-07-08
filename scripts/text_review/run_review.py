#!/usr/bin/env python3
"""视频脚本/图文文本审核 —— 命令行入口（手动批量触发）。

子命令：
  run-batch      跑一批：流程三(读取) -> 四(审核) -> 五(低风险自动改写)
  list-human     列出待人工审核的行（供管理员在表里处理）
  report         输出审核报告（成果 + 问题汇总）
  distill-rules  规则沉淀：扫描问题产出新增规则建议；加 --confirm 才写入规则库

用法示例：
  # 用本地 CSV + 启发式模型跑通全流程
  python3 -m scripts.text_review.run_review run-batch \
      --table data/review.csv --project 五一KOL --standards-dir data/standards

  # 用文心模型审核（需 ERNIE_API_KEY / ERNIE_SECRET_KEY）
  python3 -m scripts.text_review.run_review run-batch --table data/review.csv --reviewer ernie

  # 输出报告
  python3 -m scripts.text_review.run_review report --table data/review.csv --out report.md

  # 规则沉淀（先看建议，确认后再写库）
  python3 -m scripts.text_review.run_review distill-rules --table data/review.csv
  python3 -m scripts.text_review.run_review distill-rules --table data/review.csv --confirm

后端切换：
  --backend csv|tencent           表格载体（tencent 为预留适配层）
  --reviewer heuristic|ernie      审核模型
  --standards-backend local|ku    标准/规则库存储（ku 为预留适配层）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 允许以脚本或模块方式运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.text_review import engine, report, schema  # noqa: E402
from scripts.text_review.reviewer import get_reviewer  # noqa: E402
from scripts.text_review.standards import get_standards_repo  # noqa: E402
from scripts.text_review.table_adapter import get_adapter  # noqa: E402


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--table", required=True, help="审核表路径（CSV）或腾讯文档标识")
    p.add_argument("--backend", default="csv", choices=["csv", "tencent"])
    p.add_argument("--standards-backend", default="local", choices=["local", "ku"])
    p.add_argument("--standards-dir", default="data/standards", help="本地标准/规则库目录")


def cmd_run_batch(args) -> None:
    # 便捷切换：--model / --base-url 覆盖环境变量（key 只走环境变量，不上命令行）
    if args.model:
        os.environ["ONEAPI_MODEL"] = args.model
    if args.base_url:
        os.environ["ONEAPI_BASE_URL"] = args.base_url

    adapter = get_adapter(args.backend, args.table)
    reviewer = get_reviewer(args.reviewer)
    repo = get_standards_repo(args.standards_backend, args.standards_dir)
    standards = repo.load(args.project)

    summary = engine.run_batch(adapter, reviewer, standards)
    print("批处理完成：")
    print(f"  流程三 内容读取：{summary['read']} 条")
    print(f"  流程四 批量审核：{summary['review']} 条")
    print(f"  流程五 自动改写：{summary['modify']} 条")
    print(f"  结果状态分布：{json.dumps(summary['status_dist'], ensure_ascii=False)}")
    wait_human = summary["status_dist"].get(schema.ST_WAIT_HUMAN, 0)
    if wait_human:
        print(f"  ⚠ 有 {wait_human} 条转人工，请用 list-human 查看并在表中处理")


def cmd_list_human(args) -> None:
    adapter = get_adapter(args.backend, args.table)
    rows = adapter.read_rows()
    pending = [r for r in rows if (r.get(schema.COL_STATUS) or "").strip() == schema.ST_WAIT_HUMAN]
    if not pending:
        print("无待人工审核内容")
        return
    print(f"待人工审核 {len(pending)} 条：")
    for r in pending:
        print(f"  [{r.get(schema.COL_ID)}] {r.get(schema.COL_TITLE)}")
        print(f"      风险/类别：{r.get(schema.COL_PROBLEM)}")
        print(f"      修改建议：{r.get(schema.COL_SUGGESTION)}")
    print("\n管理员在表中处理：保留->内容状态改「通过」；需润色->改「需修改」并填修改建议；"
          "删除->改「已删除」。改完重新跑 run-batch。")


def cmd_report(args) -> None:
    adapter = get_adapter(args.backend, args.table)
    rows = adapter.read_rows()
    reports = report.build_reports(rows)
    md = report.render_reports_md(reports)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"报告已写入 {args.out}")
    else:
        print(md)


def cmd_distill_rules(args) -> None:
    adapter = get_adapter(args.backend, args.table)
    rows = adapter.read_rows()
    proposals = report.distill_rule_proposals(rows, min_freq=args.min_freq)
    print("规则沉淀建议：")
    print(json.dumps(proposals, ensure_ascii=False, indent=2))
    if not args.confirm:
        print("\n（未写入规则库。确认无误后加 --confirm 执行写入。对应流程七需管理员确认）")
        return
    repo = get_standards_repo(args.standards_backend, args.standards_dir)
    added = report.apply_rule_proposals(repo, proposals)
    print(f"\n已写入规则库：{json.dumps(added, ensure_ascii=False)}")


def cmd_init_standards(args) -> None:
    repo = get_standards_repo(args.standards_backend, args.standards_dir)
    created = repo.scaffold(example_project=args.example_project)
    if not created:
        print(f"标准模板已存在，未覆盖：{args.standards_dir}")
    else:
        print("已生成标准模板（请填写真实要求后再跑 run-batch）：")
        for p in created:
            print(f"  {p}")
    print(f"\n规则库：{os.path.join(args.standards_dir, 'rules.json')}"
          f"（维护 deny_words / recommended / must_human_keywords / required_tags）")


def main() -> None:
    parser = argparse.ArgumentParser(description="视频脚本/图文文本审核 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("run-batch", help="跑一批：读取->审核->自动改写")
    _common_args(p1)
    p1.add_argument("--reviewer", default="heuristic", choices=["heuristic", "oneapi", "ernie"])
    p1.add_argument("--model", default=None,
                    help="模型名，覆盖 ONEAPI_MODEL（如 gpt-4o / ernie-4.0-8k / dodo-...）")
    p1.add_argument("--base-url", default=None, help="API 基址，覆盖 ONEAPI_BASE_URL")
    p1.add_argument("--project", default=None, help="项目名（加载对应补充标准）")
    p1.set_defaults(func=cmd_run_batch)

    p2 = sub.add_parser("list-human", help="列出待人工审核内容")
    _common_args(p2)
    p2.set_defaults(func=cmd_list_human)

    p3 = sub.add_parser("report", help="输出审核报告")
    _common_args(p3)
    p3.add_argument("--out", default=None, help="报告输出路径（markdown），不填打印到终端")
    p3.set_defaults(func=cmd_report)

    p4 = sub.add_parser("distill-rules", help="规则沉淀：产出新增规则建议")
    _common_args(p4)
    p4.add_argument("--min-freq", type=int, default=2, help="判定高频的最小出现次数")
    p4.add_argument("--confirm", action="store_true", help="确认后写入规则库")
    p4.set_defaults(func=cmd_distill_rules)

    p5 = sub.add_parser("init-standards", help="生成分维度标准模板供管理员填写")
    p5.add_argument("--standards-backend", default="local", choices=["local", "ku"])
    p5.add_argument("--standards-dir", default="data/standards")
    p5.add_argument("--example-project", default="五一KOL", help="示例项目标准文件名")
    p5.set_defaults(func=cmd_init_standards)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
