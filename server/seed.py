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
    "official_slogan": "再复杂的出行，问小度想想就能搞定！",
    "pre_post_trip_assistant": {
        "positioning": "行前/行后AI公共出行助手",
        "capabilities": [
            "支持自然语言提出多点、多约束、多方式的出行需求",
            "支持时间窗口规划",
            "支持按人群特征和消费偏好规划",
            "支持公交、骑行、打车组合出行",
            "支持往返行程闭环规划",
        ],
    },
    "in_trip_walking_companion": {
        "positioning": "行中AI步行陪伴助手",
        "capabilities": [
            "支持语音问答",
            "支持景点讲解及附近美食、厕所查询",
            "支持下雨、带儿童、步行场景路线规划",
            "支持精准游览时间规划",
            "支持动态调整路线",
            "支持终点餐饮和商圈推荐",
            "支持记忆用户出行偏好",
        ],
    },
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
