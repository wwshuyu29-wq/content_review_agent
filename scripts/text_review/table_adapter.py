"""表格适配层 —— 屏蔽底层载体（腾讯文档 / 本地 CSV）差异。

引擎只依赖 read_rows() / write_rows() 两个方法，行是 dict（列名 -> 值）。

  - LocalCsvAdapter：默认，可离线跑通全流程，也用于自动化测试
  - TencentDocAdapter：预留桩。腾讯文档经 dodo-happywork-v1-internal 读取；
    回写状态列需腾讯文档开放平台写权限，接入时实现 read_rows/write_rows 即可。
"""
from __future__ import annotations

import csv
import os

from . import schema


def _ensure_columns(row: dict) -> dict:
    """补齐缺失列，保证行结构完整。"""
    for col in schema.ALL_COLUMNS:
        row.setdefault(col, "")
    return row


class LocalCsvAdapter:
    """本地 CSV 版表格（UTF-8，表头为中文列名）。"""

    def __init__(self, path: str):
        self.path = path

    def read_rows(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            return [_ensure_columns(dict(r)) for r in reader]

    def write_rows(self, rows: list[dict]) -> None:
        with open(self.path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=schema.ALL_COLUMNS)
            writer.writeheader()
            for r in rows:
                writer.writerow({c: r.get(c, "") for c in schema.ALL_COLUMNS})


class TencentDocAdapter:
    """腾讯文档在线表格适配层（预留桩）。

    读取：可用 dodo-happywork-v1-internal 的在线文档读取能力拉取表格内容。
    回写：需腾讯文档开放平台的表格写接口（应用凭据）。
    接入时实现 read_rows()/write_rows() 与 LocalCsvAdapter 相同语义即可。
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "TencentDocAdapter 为预留适配层。接入时用 dodo 读取 + 腾讯文档开放平台"
            "写接口实现 read_rows()/write_rows()。当前请用 --backend csv 跑通全流程。"
        )


def get_adapter(backend: str, path: str):
    if backend == "tencent":
        return TencentDocAdapter(path)
    return LocalCsvAdapter(path)
