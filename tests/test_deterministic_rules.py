from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.services.deterministic_rule_service import ReviewContext, StructuredIssue, evaluate_rules
from server.services.review_profile_service import get_review_profile
from server.services.standard_package_service import compile_standard_package, compute_package_digest, load_standard_package


def rule(rule_id, matcher, **kwargs):
    return {
        "rule_id": rule_id,
        "scope": kwargs.pop("scope", {"content_type": "TECH_MEDIA_REVIEW", "field": "body"}),
        "matcher": matcher,
        "severity": kwargs.pop("severity", "HIGH"),
        "action": kwargs.pop("action", "HUMAN_REVIEW"),
        "auto_fixable": kwargs.pop("auto_fixable", False),
        "source_reference": kwargs.pop("source_reference", ["CLAIM-001"]),
        **kwargs,
    }


def profile_with(*rules, platform_requirements=None, replacement_rules=None, platform_aliases=None):
    version = SimpleNamespace(
        business_domain="baidu_maps_marketing_review",
        document_type="project_standard",
        project_code="bdmap_xdxx_tech_review_2026",
        content_type="TECH_MEDIA_REVIEW",
        package_version="0.9",
        package_digest="",
        dimension_standards={
            "metadata": {
                "business_domain": "baidu_maps_marketing_review",
                "document_type": "project_standard",
                "project_code": "bdmap_xdxx_tech_review_2026",
                "content_type": "TECH_MEDIA_REVIEW",
                "version": "0.9",
            }
        },
        project_facts={"project_code": "bdmap_xdxx_tech_review_2026", "content_type": "TECH_MEDIA_REVIEW"},
        structured_rules={
            "rules": list(rules),
            "evidence_requirements": {"evidence_requirements": []},
            "platform_requirements": {
                key: {**value, "aliases": (platform_aliases or {}).get(key, value.get("aliases", [key]))}
                for key, value in (platform_requirements or {}).items()
            },
            "replacement_rules": {"replacement_rules": replacement_rules or []},
            "term_dictionary": {"terms": []},
        },
    )
    compiled = {
        "metadata": version.dimension_standards["metadata"],
        "project_facts": version.project_facts,
        "dimension_standards": version.dimension_standards,
        "structured_rules": version.structured_rules,
    }
    version.package_digest = compute_package_digest(compiled)
    return get_review_profile(version)


@pytest.fixture
def representative_draft():
    path = Path(__file__).parent / "fixtures" / "representative_tech_media_review.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def v09_profile():
    root = Path(__file__).resolve().parents[1] / "data" / "standards"
    compiled = compile_standard_package(load_standard_package(root, "bdmap_xdxx_tech_review_2026", "0.9"))
    version = SimpleNamespace(
        business_domain=compiled["metadata"]["business_domain"],
        document_type=compiled["metadata"]["document_type"],
        project_code=compiled["metadata"]["project_code"],
        content_type=compiled["metadata"]["content_type"],
        package_version=compiled["metadata"]["version"],
        package_digest=compute_package_digest(compiled),
        dimension_standards=compiled["dimension_standards"],
        project_facts=compiled["project_facts"],
        structured_rules=compiled["structured_rules"],
    )
    return get_review_profile(version)


def test_representative_v09_draft_stably_routes_text_fixes_and_verification(representative_draft, v09_profile):
    context = ReviewContext(
        title=representative_draft["title"],
        body=representative_draft["body"],
        platform=representative_draft["payload"]["platform"],
        project_code="bdmap_xdxx_tech_review_2026",
        test_cases=representative_draft["test_cases"],
        evidence=representative_draft["evidence"],
        evidence_assets=representative_draft["evidence_assets"],
    )

    issues = evaluate_rules(v09_profile, context)
    by_rule = {}
    for issue in issues:
        by_rule.setdefault(issue.rule_id, []).append(issue)

    assert {"TEST-COUNT-001", "TEST-EVIDENCE-001", "CLAIM-UNSUPPORTED-ABSOLUTE-001", "CLAIM-PENDING-001"} <= set(by_rule)
    assert {issue.evidence for issue in by_rule["CLAIM-UNSUPPORTED-ABSOLUTE-001"]} >= {
        "全赢", "天花板", "越复杂的需求，它越能扛",
    }
    assert any(issue.evidence == "最优解" and issue.suggestion == "一种可行方案" for issue in issues)
    assert {issue.evidence for issue in by_rule["CLAIM-PENDING-001"]} >= {
        "AI订酒店", "自动比出哪家更划算",
    }
    assert by_rule["TEST-COUNT-001"][0].human_required is True
    assert "4 个" in by_rule["TEST-COUNT-001"][0].reason
    assert by_rule["TEST-EVIDENCE-001"][0].human_required is True
    assert all(issue.action == "REQUIRE_TEXT_FIX" for issue in by_rule["CLAIM-UNSUPPORTED-ABSOLUTE-001"])
    assert all(issue.human_required is False for issue in by_rule["CLAIM-UNSUPPORTED-ABSOLUTE-001"])
    assert all(issue.human_required for issue in by_rule["CLAIM-PENDING-001"])


def test_unbound_test_records_still_require_version_and_conditions(v09_profile):
    context = ReviewContext(
        title="自用实测导航能力",
        body="亲测后认为路线可用",
        test_cases=[{"test_case_id": "T1", "command": "规划路线", "observed_result": "返回路线"}],
        evidence=[{"asset_id": "asset-1"}],
    )

    issue = next(issue for issue in evaluate_rules(v09_profile, context) if issue.rule_id == "TEST-EVIDENCE-001")
    assert issue.human_required is True
    assert "app_version" in issue.reason
    assert "tested_at" in issue.reason
    assert "device" in issue.reason
    assert "network_environment" in issue.reason


def test_tech_profile_does_not_load_celebrity_rules():
    profile = profile_with(rule("TECH-001", "exact_phrase", phrases=["AI订酒店"]))
    assert profile.content_type == "TECH_MEDIA_REVIEW"
    assert all("范丞丞" not in json.dumps(item.model_dump(), ensure_ascii=False) for item in profile.rules)


def test_exact_phrase_reports_structured_metadata():
    profile = profile_with(rule("PENDING-001", "exact_phrase", phrases=["AI订酒店"], source_reference=["PENDING-002"]))
    issues = evaluate_rules(profile, ReviewContext(title="标题", body="本文提到AI订酒店", platform="xiaohongshu"))
    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, StructuredIssue)
    assert issue.rule_id == "PENDING-001"
    assert issue.field == "body"
    assert issue.evidence == "AI订酒店"
    assert issue.source_reference == ["PENDING-002"]
    assert issue.action == "HUMAN_REVIEW"
    assert issue.auto_fixable is False


def test_count_mismatch_uses_declared_count_and_numbered_sections():
    profile = profile_with(rule("TEST-COUNT-001", "count_consistency", title_pattern=r"(\d+)个测试", scope={"content_type": "TECH_MEDIA_REVIEW", "fields": ["title", "body"]}))
    context = ReviewContext(title="亲测：5个测试", body="1. 场景一\n2. 场景二", test_cases=[])
    assert {issue.rule_id for issue in evaluate_rules(profile, context)} == {"TEST-COUNT-001"}


def test_evidence_trigger_without_evidence_routes_to_human():
    profile = profile_with(rule("TEST-EVIDENCE-001", "evidence_required", trigger_terms=["亲测"], required_fields=["test_cases"]))
    issues = evaluate_rules(profile, ReviewContext(title="亲测小度想想", body="亲测结果很好", test_cases=[]))
    assert issues[0].human_required is True
    assert "造假" not in issues[0].reason


def test_evidence_present_does_not_fire():
    profile = profile_with(rule("TEST-EVIDENCE-001", "evidence_required", trigger_terms=["亲测"], required_fields=["test_cases"]))
    context = ReviewContext(
        title="亲测小度想想",
        body="亲测结果很好",
        test_cases=[{"test_case_id": "T1", "command": "输入路线", "observed_result": "返回方案"}],
    )
    assert evaluate_rules(profile, context) == []


def test_scoped_platform_rule_only_matches_platform():
    profile = profile_with(
        rule("PLATFORM-001", "required_term", required_terms=["#小度想想#"], scope={"content_type": "TECH_MEDIA_REVIEW", "platforms": ["xiaohongshu"], "field": "body"}),
        platform_requirements={"xiaohongshu": {"status": "ACTIVE"}},
    )
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="douyin")) == []
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="xiaohongshu"))[0].rule_id == "PLATFORM-001"


def test_replacement_rule_reports_replacement_guidance():
    profile = profile_with(rule("REPLACE-001", "replacement_map", replacement_map={"最优解": "一种可行方案"}, auto_fixable=True))
    issue = evaluate_rules(profile, ReviewContext(body="这是最优解"))[0]
    assert issue.suggestion == "一种可行方案"
    assert issue.auto_fixable is True


def test_pending_platform_config_does_nothing():
    profile = profile_with(
        rule("PLATFORM-002", "required_term", required_terms=["必须带标签"], scope={"content_type": "TECH_MEDIA_REVIEW", "platforms": ["xiaohongshu"], "field": "body"}),
        platform_requirements={"xiaohongshu": {"status": "PENDING", "requirements": ["必须带标签"]}},
    )
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="xiaohongshu")) == []


def test_replacement_rules_snapshot_is_executable_without_deterministic_duplicate():
    profile = profile_with(replacement_rules=[{"replacement_id": "REPLACE-ONLY", "from": "最优解", "to": "一种可行方案", "source_reference": ["CLAIM-001"]}])
    issue = evaluate_rules(profile, ReviewContext(body="这是最优解"))[0]
    assert issue.rule_id == "REPLACE-ONLY"
    assert issue.suggestion == "一种可行方案"
    assert [rule.rule_id for rule in profile.rules] == ["REPLACE-ONLY"]


def test_platform_aliases_normalize_before_scope_and_pending_lookup():
    profile = profile_with(
        rule("PLATFORM-001", "required_term", required_terms=["#小度想想#"], scope={"content_type": "TECH_MEDIA_REVIEW", "platforms": ["xiaohongshu"], "field": "body"}),
        platform_requirements={"xiaohongshu": {"status": "ACTIVE"}},
        platform_aliases={"xiaohongshu": ["xiaohongshu", "小红书"]},
    )
    assert profile.platform_aliases["小红书"] == "xiaohongshu"
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="小红书"))[0].rule_id == "PLATFORM-001"
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="xiaohongshu"))[0].rule_id == "PLATFORM-001"
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="unknown")) == []
    assert evaluate_rules(profile, ReviewContext(body="正文", platform="")) == []


def test_invalid_test_and_evidence_records_remain_missing():
    profile = profile_with(rule("TEST-EVIDENCE-001", "evidence_required", trigger_terms=["亲测"], required_fields=["test_cases", "evidence"]))
    context = ReviewContext(title="亲测", body="亲测结果", test_cases=[{}], evidence=[{"asset_id": "  "}])
    issues = evaluate_rules(profile, context)
    assert issues[0].human_required is True
    assert "证据" in issues[0].reason
    assert "造假" not in issues[0].reason


def test_text_scope_is_applied_per_field_and_duplicate_issues_are_removed():
    profile = profile_with(
        rule("TITLE-001", "exact_phrase", phrases=["命中"], scope={"content_type": "TECH_MEDIA_REVIEW", "fields": ["title"]}),
        rule("BODY-001", "exact_phrase", phrases=["命中"], scope={"content_type": "TECH_MEDIA_REVIEW", "fields": ["body"]}),
        rule("DUP-001", "exact_phrase", phrases=["重复"], scope={"content_type": "TECH_MEDIA_REVIEW", "fields": ["body"]}),
        rule("DUP-001", "exact_phrase", phrases=["重复"], scope={"content_type": "TECH_MEDIA_REVIEW", "fields": ["body"]}),
    )
    issues = evaluate_rules(profile, ReviewContext(title="命中", body="命中 重复"))
    assert {(issue.rule_id, issue.field, issue.evidence) for issue in issues} == {
        ("TITLE-001", "title", "命中"),
        ("BODY-001", "body", "命中"),
        ("DUP-001", "body", "重复"),
    }


def test_unknown_matcher_is_rejected():
    with pytest.raises(ValueError, match="unsupported matcher"):
        profile_with(rule("BAD-001", "semantic_topic"))
