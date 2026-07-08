"""审核标准仓库 + 规则库。

标准分两类（流程一）：
  - 全局标准：长期适用，存 references/text_review_standards.md（或 Ku）
  - 项目补充标准：每项目一份，存 <standards_dir>/<project>.md（或 Ku），管理员随时改

规则库（流程七 规则沉淀的产物，也供审核直接使用）：
  - deny_words：禁用词
  - recommended：推荐表达（原词 -> 建议词）
  - must_human_keywords：命中即必须人工确认的关键词（明星/IP/第三方品牌等）
  存 <standards_dir>/rules.json

Ku 适配：KuStandardsRepo 为预留桩，接入时实现 ku-doc-manage 读写即可，
其余流程不变。默认用 LocalStandardsRepo 在本地跑通。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import schema

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_GLOBAL_DOC = os.path.join(SKILL_DIR, "references", "text_review_standards.md")

# 规则库种子（首次运行时若不存在则写入）
_SEED_RULES = {
    "deny_words": [
        "国家级", "最高级", "最佳", "第一品牌", "绝对", "100%有效", "包治",
        "永久", "根治", "彻底", "史上最强",
    ],
    "recommended": {
        "秒杀全网": "优惠力度大",
        "全网最低": "价格优惠",
    },
    "must_human_keywords": [
        "明星", "代言", "IP", "联名", "官方合作",
    ],
    "required_tags": [],
}


@dataclass
class Standards:
    """一次审核会话使用的合并标准。"""
    global_text: str = ""
    project_text: str = ""
    dimension_docs: dict = field(default_factory=dict)   # key -> 该维度全局标准文本
    deny_words: list[str] = field(default_factory=list)
    recommended: dict[str, str] = field(default_factory=dict)
    must_human_keywords: list[str] = field(default_factory=list)
    required_tags: list[str] = field(default_factory=list)

    def prompt_context(self) -> str:
        """拼给审核模型的标准上下文（全维度）。"""
        parts = ["【全局审核标准】", self.global_text or "(未配置)"]
        if self.project_text:
            parts += ["", "【本项目补充标准】", self.project_text]
        if self.deny_words:
            parts += ["", "【禁用词】", "、".join(self.deny_words)]
        if self.must_human_keywords:
            parts += ["", "【必须人工确认关键词】", "、".join(self.must_human_keywords)]
        return "\n".join(parts)

    def dim_context(self, key: str) -> str:
        """某个维度子 agent 的标准切片：该维度全局标准 + 项目补充标准。"""
        parts = [self.dimension_docs.get(key, "") or "(该维度全局标准未配置)"]
        if self.project_text:
            parts += ["", "【本项目补充标准】", self.project_text]
        return "\n".join(parts)


class LocalStandardsRepo:
    """本地文件版标准仓库（默认，可离线跑通）。"""

    def __init__(self, standards_dir: str, global_doc: str | None = None):
        self.standards_dir = standards_dir
        self.global_doc = global_doc or DEFAULT_GLOBAL_DOC
        os.makedirs(standards_dir, exist_ok=True)
        self.rules_path = os.path.join(standards_dir, "rules.json")
        if not os.path.exists(self.rules_path):
            self._save_rules(_SEED_RULES)

    def _load_rules(self) -> dict:
        with open(self.rules_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_rules(self, rules: dict) -> None:
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)

    def load(self, project: str | None = None) -> Standards:
        # 分维度加载全局标准：<standards_dir>/global/<维度文件>
        gdir = os.path.join(self.standards_dir, "global")
        dimension_docs: dict[str, str] = {}
        for key, fname in schema.DIMENSION_FILES.items():
            fp = os.path.join(gdir, fname)
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    dimension_docs[key] = f.read()
            else:
                dimension_docs[key] = ""

        # 全局标准合并文本（供 prompt_context 兜底；无分维度文件时回退到旧的单文件）
        global_text = "\n\n".join(t for t in dimension_docs.values() if t)
        if not global_text and os.path.exists(self.global_doc):
            with open(self.global_doc, "r", encoding="utf-8") as f:
                global_text = f.read()

        # 项目补充标准：<standards_dir>/projects/<project>.md
        project_text = ""
        if project:
            ppath = os.path.join(self.standards_dir, "projects", f"{project}.md")
            if os.path.exists(ppath):
                with open(ppath, "r", encoding="utf-8") as f:
                    project_text = f.read()

        rules = self._load_rules()
        return Standards(
            global_text=global_text,
            project_text=project_text,
            dimension_docs=dimension_docs,
            deny_words=rules.get("deny_words", []),
            recommended=rules.get("recommended", {}),
            must_human_keywords=rules.get("must_human_keywords", []),
            required_tags=rules.get("required_tags", []),
        )

    def scaffold(self, example_project: str = "五一KOL") -> list[str]:
        """生成分维度标准模板 + 示例项目标准，供管理员填写。返回创建的文件列表。"""
        created = []
        gdir = os.path.join(self.standards_dir, "global")
        pdir = os.path.join(self.standards_dir, "projects")
        os.makedirs(gdir, exist_ok=True)
        os.makedirs(pdir, exist_ok=True)

        for key, fname in schema.DIMENSION_FILES.items():
            fp = os.path.join(gdir, fname)
            if not os.path.exists(fp):
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(_DIM_TEMPLATES[key])
                created.append(fp)

        ppath = os.path.join(pdir, f"{example_project}.md")
        if not os.path.exists(ppath):
            with open(ppath, "w", encoding="utf-8") as f:
                f.write(_PROJECT_TEMPLATE)
            created.append(ppath)
        return created

    # ── 规则沉淀写入（流程七，管理员确认后调用）─────────────
    def add_rules(self, deny_words=None, recommended=None, must_human_keywords=None) -> dict:
        rules = self._load_rules()
        added = {"deny_words": [], "recommended": {}, "must_human_keywords": []}
        for w in deny_words or []:
            if w not in rules["deny_words"]:
                rules["deny_words"].append(w)
                added["deny_words"].append(w)
        for k, v in (recommended or {}).items():
            if k not in rules["recommended"]:
                rules["recommended"][k] = v
                added["recommended"][k] = v
        for w in must_human_keywords or []:
            if w not in rules["must_human_keywords"]:
                rules["must_human_keywords"].append(w)
                added["must_human_keywords"].append(w)
        self._save_rules(rules)
        return added


class KuStandardsRepo:
    """如流知识库 Ku 版标准仓库（预留桩）。

    接入时用 ku-doc-manage 读取全局/项目标准文档与规则表，实现与
    LocalStandardsRepo 相同的 load()/add_rules() 接口即可，引擎无需改动。
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "KuStandardsRepo 为预留适配层。接入内网 Ku 时，用 ku-doc-manage skill "
            "实现 load()/add_rules()，签名与 LocalStandardsRepo 一致。当前请用 "
            "--standards-backend local。"
        )


def get_standards_repo(backend: str, standards_dir: str, global_doc: str | None = None):
    if backend == "ku":
        return KuStandardsRepo(standards_dir, global_doc)
    return LocalStandardsRepo(standards_dir, global_doc)


# ── 分维度标准模板（scaffold 用，管理员照着填真实要求）─────────
_DIM_TEMPLATES = {
    "compliance": """# 合规 / 广告法标准（全局）

> 供「合规」子 agent 使用。写清楚不可触碰的红线，越具体模型判得越准。

## 广告法高风险红线（命中即高风险转人工）
- 政治敏感：<在此列举具体禁提内容>
- 色情低俗：<...>
- 暴力血腥：<...>
- 虚假信息 / 夸大产品功能：如"根治""100%有效"等绝对化用语
- 版权侵权：<...>
- 人身攻击：<...>

## 说明
- 精确禁用词请维护到 rules.json 的 deny_words（确定性精确匹配，无需 LLM）
- 本文件写"语义层面"的红线描述，供 LLM 结合上下文判断
""",
    "brand": """# 品牌一致性标准（全局）

> 供「品牌一致性」子 agent 使用。

## 品牌名 / 功能名正确写法
- 正确：<品牌全称/简称>；错误示例：<常见写错的形式>
- 功能名：<正确功能名> ；不可写成：<错误叫法>

## 品牌调性 / 口径
- 调性关键词：<如 专业、年轻、可信赖>
- 禁止表达：<与品牌定位不符的说法>
""",
    "accuracy": """# 内容准确性标准（全局）

> 供「内容准确性」子 agent 使用。项目相关的卖点/规则/优惠请写在项目标准里。

## 通用准确性要求
- 功能介绍需与官方口径一致，不得编造
- 活动规则、优惠内容需与项目标准完全一致
- 必带标签/话题：请维护到 rules.json 的 required_tags（确定性检查是否齐全）
""",
    "quality": """# 内容质量标准（全局）

> 供「内容质量」子 agent 使用。这类问题属低风险，可自动改写。

## 检查项
- 错别字、语病、标点误用
- 重复表达、信息缺失、卖点不清
- 表达不自然、口语化过度
""",
    "external": """# 舆情与授权标准（全局）

> 供「舆情与授权」子 agent 使用。命中即转人工（需外部数据核对）。

## 明星 / IP / 第三方品牌
- 需人工确认关键词请维护到 rules.json 的 must_human_keywords
- 本项目已授权的明星/IP/第三方：<在项目标准里列清单>

## 近2周风险舆情
- 舆情风险词由管理员手动维护（后续接入微博抓取）
""",
}

_PROJECT_TEMPLATE = """# 项目补充标准（示例：五一KOL）

> 每个项目审核前临时补充，管理员随时可改。会附加到各维度子 agent 的上下文里。

## 核心卖点（内容准确性依据）
- <卖点1>
- <卖点2>

## 活动规则 / 优惠内容
- <活动时间、参与方式、优惠力度……需与此处一致>

## 必带标签 / 话题
- <#话题#、@账号 等；也可维护到 rules.json 的 required_tags 做确定性校验>

## 本项目已授权的明星 / IP / 第三方品牌
- <列清单，未在清单内的一律转人工>

## 本项目特别禁提
- <本次活动不能提的内容>
"""
