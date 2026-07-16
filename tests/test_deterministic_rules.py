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
    compiled = compile_standard_package(load_standard_package(root, "bdmap_xdxx_tech_review_2026", "1.3"))
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

    assert {"CLAIM-UNSUPPORTED-ABSOLUTE-001", "CLAIM-PENDING-001"} <= set(by_rule)
    assert "TEST-COUNT-001" not in by_rule
    assert "TEST-EVIDENCE-001" not in by_rule
    assert {issue.evidence for issue in by_rule["CLAIM-UNSUPPORTED-ABSOLUTE-001"]} >= {
        "全赢", "天花板", "越复杂的需求，它越能扛",
    }
    assert any(issue.evidence == "最优解" and issue.suggestion == "一种可行方案" for issue in issues)
    hotel_evidence = "\n".join(issue.evidence for issue in by_rule["CLAIM-PENDING-001"])
    assert "AI订酒店" in hotel_evidence
    assert "自动比出哪家更划算" in hotel_evidence
    assert all(issue.action == "REQUIRE_TEXT_FIX" for issue in by_rule["CLAIM-UNSUPPORTED-ABSOLUTE-001"])
    assert all(issue.human_required is False for issue in by_rule["CLAIM-UNSUPPORTED-ABSOLUTE-001"])
    assert all(issue.human_required for issue in by_rule["CLAIM-PENDING-001"])


@pytest.mark.parametrize(
    ("body", "should_match"),
    [
        ("再复杂的路线需求它都能轻松搞定", True),
        ("任何多约束行程都可以处理", True),
        ("路线规划准确率100%", True),
        ("所有场景都能稳定支持", True),
        ("本次样本覆盖率100%", False),
        ("电量从80%降到60%", False),
        ("“绝对领先”这种说法缺少依据", False),
        ("它并非所有复杂需求都能处理", False),
    ],
)
def test_unsupported_claim_composition_matches_reusable_claims_and_guards_context(v09_profile, body, should_match):
    matched = any(
        issue.rule_id == "CLAIM-UNSUPPORTED-ABSOLUTE-001"
        for issue in evaluate_rules(v09_profile, ReviewContext(body=body))
    )
    assert matched is should_match


@pytest.mark.parametrize(
    "body",
    [
        "它不但支持所有复杂路线，还能自动筛选酒店",
        "没有限制，所有路线都能处理",
        "官方声称它行业第一",
    ],
)
def test_guarded_claim_uses_local_predicate_context_for_mixed_clauses(v09_profile, body):
    issues = evaluate_rules(v09_profile, ReviewContext(body=body))
    assert any(issue.rule_id == "CLAIM-UNSUPPORTED-ABSOLUTE-001" for issue in issues)


@pytest.mark.parametrize(
    "body",
    [
        "它不支持所有复杂路线",
        "所谓行业第一缺少依据",
        "它并不能处理任何复杂行程",
    ],
)
def test_guarded_claim_keeps_genuine_predicate_negation_and_criticism_negative(v09_profile, body):
    issues = evaluate_rules(v09_profile, ReviewContext(body=body))
    assert not any(issue.rule_id == "CLAIM-UNSUPPORTED-ABSOLUTE-001" for issue in issues)


def test_evidenced_percentage_observation_is_not_treated_as_unsupported_absolute(v09_profile):
    context = ReviewContext(
        body="亲测路线规划准确率100%",
        test_cases=[{
            "test_case_id": "T1", "claim": "路线规划准确率", "command": "连续规划五次",
            "observed_result": "路线规划准确率100%", "evidence_asset_ids": ["asset-1"],
            "app_version": "1.0", "tested_at": "2026-07-15", "device": "phone",
            "operating_system": "iOS", "network_environment": "wifi",
        }],
        evidence=[{"test_case_id": "T1", "asset_id": "asset-1"}],
        evidence_assets=[{"asset_id": "asset-1"}],
    )
    assert not any(
        issue.rule_id == "CLAIM-UNSUPPORTED-ABSOLUTE-001"
        for issue in evaluate_rules(v09_profile, context)
    )


@pytest.mark.parametrize(
    ("body", "should_match"),
    [
        ("小度想想可以预订酒店", True),
        ("它会自动比较住宿价格", True),
        ("不用自己比较酒店，它会推荐最划算的一家", True),
        ("支持酒店智能比价", True),
        ("AI订酒店", True),
        ("代订酒店", True),
        ("小度想想预订酒店", True),
        ("小度想想比较酒店价格", True),
        ("小度想想推荐性价比最高的住宿", True),
        ("小度想想按性价比给住宿排序", True),
        ("我预订酒店后继续导航", False),
        ("我会预订酒店并比较价格", False),
        ("朋友推荐了这家住宿", False),
        ("酒店按价格排序展示", False),
        ("今天讨论酒店价格变化", False),
        ("它没有订酒店功能", False),
        ("不能自动比较酒店价格", False),
        ("小度想想不能预订酒店", False),
    ],
)
def test_pending_hotel_composition_matches_capabilities_with_negation_guard(v09_profile, body, should_match):
    matched = any(
        issue.rule_id == "CLAIM-PENDING-001"
        for issue in evaluate_rules(v09_profile, ReviewContext(body=body))
    )
    assert matched is should_match


def _bound_test_context(**test_case_updates):
    test_case = {
        "test_case_id": "T1", "claim": "路线规划", "command": "规划路线",
        "observed_result": "返回路线", "evidence_asset_ids": ["asset-1"],
        "app_version": "1.0", "tested_at": "2026-07-15", "device": "phone",
        "operating_system": "iOS", "network_environment": "wifi",
    }
    test_case.update(test_case_updates)
    return ReviewContext(
        title="亲测导航能力", body="实测后返回路线", test_cases=[test_case],
        evidence=[{"test_case_id": "T1", "asset_id": "asset-1"}],
        evidence_assets=[{"asset_id": "asset-1"}],
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"claim": " "},
        {"claim": None},
        {"command": " "},
        {"observed_result": ""},
        {"evidence_asset_ids": []},
        {"evidence_asset_ids": ["missing-asset"]},
    ],
)
def test_invalid_or_unbound_test_case_fields_do_not_require_evidence_review(v09_profile, updates):
    issues = evaluate_rules(v09_profile, _bound_test_context(**updates))
    assert not any(issue.rule_id == "TEST-EVIDENCE-001" for issue in issues)


def test_unrelated_evidence_binding_does_not_satisfy_test_case(v09_profile):
    context = _bound_test_context()
    context = context.model_copy(update={
        "evidence": [{"test_case_id": "OTHER", "asset_id": "asset-1"}],
    })
    assert not any(issue.rule_id == "TEST-EVIDENCE-001" for issue in evaluate_rules(v09_profile, context))


def test_fully_bound_test_case_and_manifest_satisfy_evidence_rule(v09_profile):
    assert not any(
        issue.rule_id == "TEST-EVIDENCE-001"
        for issue in evaluate_rules(v09_profile, _bound_test_context())
    )


def test_unbound_test_records_do_not_require_version_and_conditions(v09_profile):
    context = ReviewContext(
        title="自用实测导航能力",
        body="亲测后认为路线可用",
        test_cases=[{"test_case_id": "T1", "command": "规划路线", "observed_result": "返回路线"}],
        evidence=[{"asset_id": "asset-1"}],
    )

    assert not any(issue.rule_id == "TEST-EVIDENCE-001" for issue in evaluate_rules(v09_profile, context))


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
    assert evaluate_rules(profile, context) == []


def test_evidence_trigger_without_evidence_does_not_route_to_human():
    profile = profile_with(rule("TEST-EVIDENCE-001", "evidence_required", trigger_terms=["亲测"], required_fields=["test_cases"]))
    issues = evaluate_rules(profile, ReviewContext(title="亲测小度想想", body="亲测结果很好", test_cases=[]))
    assert issues == []


def test_evidence_present_does_not_fire():
    profile = profile_with(rule("TEST-EVIDENCE-001", "evidence_required", trigger_terms=["亲测"], required_fields=["test_cases"]))
    context = ReviewContext(
        title="亲测小度想想",
        body="亲测结果很好",
        test_cases=[{
            "test_case_id": "T1", "claim": "路线规划", "command": "输入路线",
            "observed_result": "返回方案", "evidence_asset_ids": ["asset-1"],
        }],
        evidence=[{"test_case_id": "T1", "asset_id": "asset-1"}],
        evidence_assets=[{"asset_id": "asset-1"}],
    )
    assert evaluate_rules(profile, context) == []


def test_no_approved_trigger_has_no_evidence_issue():
    profile = profile_with(rule(
        "TEST-EVIDENCE-001", "evidence_required", trigger_terms=["体验", "亲测"],
        required_fields=["test_cases", "evidence"],
    ))

    issues = evaluate_rules(profile, ReviewContext(
        title="路线规划产品介绍", body="这是普通功能体验介绍。",
        evidence_assets=[{"asset_id": "cover", "kind": "SCREENSHOT"}],
    ))

    assert not any(issue.rule_id == "TEST-EVIDENCE-001" for issue in issues)


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


def test_invalid_test_and_evidence_records_do_not_create_missing_evidence_issue():
    profile = profile_with(rule("TEST-EVIDENCE-001", "evidence_required", trigger_terms=["亲测"], required_fields=["test_cases", "evidence"]))
    context = ReviewContext(title="亲测", body="亲测结果", test_cases=[{}], evidence=[{"asset_id": "  "}])
    issues = evaluate_rules(profile, context)
    assert issues == []


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
