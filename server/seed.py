from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project, RuleVersion


DEFAULT_PROJECT_NAME = "百度地图小度想想 × 范丞丞短期合作"

DIMENSION_STANDARDS = {
    "compliance": "遵守广告法，不得使用虚假、夸大或绝对化表达。",
    "brand": "品牌名与产品名须使用“百度地图”和“小度想想”，合作身份须按项目事实表述。",
    "accuracy": "功能、活动、Slogan 和合作信息须与项目事实库一致，不得扩展未确认能力。",
    "quality": "检查错别字、语病、重复表达、信息缺失和不自然表述。",
    "external": "明星合作身份、授权范围和风险舆情必须按项目事实核验，不确定时转人工。",
}

PROJECT_FACTS = {
    "brand": "百度地图",
    "product": "小度想想",
    "partner": "范丞丞",
    "partnership_type": "短期合作伙伴",
    "is_spokesperson": False,
    "official_slogan": "出行更简单，AI更懂你",
    "travel_features": [
        "AI安排行程",
        "个性化出游推荐",
        "智能规划游玩路线",
        "AI导游跟随讲解",
        "AR导览实景沉浸式游玩",
    ],
}

STRUCTURED_RULES = {
    "deny_words": ["代言", "代言人"],
    "recommended": {
        "范丞丞代言百度地图": "范丞丞与百度地图开展短期合作",
        "百度地图代言人范丞丞": "百度地图短期合作伙伴范丞丞",
    },
    "must_human_keywords": ["明星", "合作", "授权", "范丞丞"],
    "required_tags": [],
}


def seed_default_project(session: Session) -> Project:
    project = session.scalar(select(Project).where(Project.name == DEFAULT_PROJECT_NAME))
    if project is not None:
        return project

    project = Project(
        name=DEFAULT_PROJECT_NAME,
        description="百度地图小度想想与范丞丞的短期合作内容审核项目。",
    )
    rule_version = RuleVersion(
        project=project,
        version=1,
        dimension_standards=DIMENSION_STANDARDS,
        project_facts=PROJECT_FACTS,
        structured_rules=STRUCTURED_RULES,
        prompt_version="review-prompt-v1",
    )
    project.current_rule_version = rule_version
    session.add(project)
    session.flush()
    return project
