from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.text_review.reviewers.base import AgentIssue, AgentReviewResult, EvidenceSpan
from scripts.text_review.reviewers.llm import OpenAICompatLLM
from scripts.text_review.reviewers.tech_media import AGENT_ORDER, AGENT_VERSION, TechMediaReviewer
from server.models import PublishStatus, ReviewStatus
from server.services.deterministic_rule_service import ReviewContext, evaluate_rules
from server.services.review_arbiter_service import arbitrate_review
from server.services.review_profile_service import ReviewProfile, get_review_profile
from server.services.standard_package_service import compile_standard_package, compute_package_digest, load_standard_package


PROFILE = ReviewProfile(
    business_domain="baidu_maps_marketing_review",
    document_type="project_standard",
    project_code="project",
    content_type="TECH_MEDIA_REVIEW",
    package_version="0.9",
    package_digest="digest",
    rules=(),
    project_facts={"product": "小度想想", "goal": "可信测评"},
    global_standards={
        "compliance": "合规标准文本",
        "brand_consistency": "品牌标准文本",
        "content_accuracy": "准确性标准文本",
        "test_credibility": "测试标准文本",
        "content_quality": "质量标准文本",
        "campaign_effectiveness": "传播标准文本",
    },
    approved_claims=({"claim_id": "CLAIM-001", "text": "官方支持多点出行"},),
    pending_claims=({"claim_id": "PENDING-002", "text": "小度想想可以自动筛选、比较酒店并判断最划算"},),
    known_source_references=("CLAIM-001", "PENDING-002", "project.yaml"),
    platform_requirements={"xiaohongshu": {"status": "ACTIVE"}},
)
CONTEXT = ReviewContext(
    title="亲测：3 个测试",
    body="我在三个场景中测试了产品。",
    platform="xiaohongshu",
    project_code="project",
    test_cases=[{"test_case_id": "T1", "command": "打开应用", "observed_result": "成功"}],
    evidence=[{"asset_id": "asset-1", "timestamp": "00:01", "quote": "成功"}],
)


@pytest.fixture
def representative_context_and_profile():
    root = Path(__file__).resolve().parents[1]
    draft = json.loads((root / "tests" / "fixtures" / "representative_tech_media_review.json").read_text(encoding="utf-8"))
    compiled = compile_standard_package(load_standard_package(root / "data" / "standards", "bdmap_xdxx_tech_review_2026", "0.9"))
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
    context = ReviewContext(
        title=draft["title"], body=draft["body"], platform=draft["payload"]["platform"],
        project_code=compiled["metadata"]["project_code"], test_cases=draft["test_cases"],
        evidence=draft["evidence"], evidence_assets=draft["evidence_assets"],
    )
    return context, get_review_profile(version)


def test_all_six_agents_return_strict_results_in_fixed_order():
    results = TechMediaReviewer().review_structured(CONTEXT, PROFILE)
    assert tuple(result.agent_id for result in results) == AGENT_ORDER
    assert all(isinstance(result, AgentReviewResult) for result in results)
    assert all(result.model_dump() for result in results)


def test_output_models_forbid_extra_fields_and_bound_scores():
    with pytest.raises(ValidationError):
        EvidenceSpan(quote="x", extra="not allowed")
    with pytest.raises(ValidationError):
        AgentReviewResult(
            agent_id="COMPLIANCE", agent_version="v1", decision="PASS",
            summary="ok", score=101, confidence=1, issues=[], extra="nope",
        )


def test_specialist_prompts_slice_standards_and_never_include_artist_rules():
    reviewer = TechMediaReviewer()
    prompts = reviewer.build_prompts(CONTEXT, PROFILE)
    assert set(prompts) == set(AGENT_ORDER)
    prompt_text = json.dumps(prompts, ensure_ascii=False)
    assert "范丞丞" not in prompt_text
    assert all(term not in prompt_text.lower() for term in ("celebrity", "artist", "明星", "艺人"))
    assert "test_cases" in prompts["TEST_CREDIBILITY"]
    assert "test_cases" not in prompts["BRAND"]
    assert "官方支持多点出行" in prompts["PRODUCT_ACCURACY"]
    assert "小度想想可以自动筛选、比较酒店并判断最划算" in prompts["PRODUCT_ACCURACY"]
    assert "小度想想可以自动筛选、比较酒店并判断最划算" not in prompts["CONTENT_QUALITY"]
    assert "合规标准文本" in prompts["COMPLIANCE"]
    assert "品牌标准文本" in prompts["BRAND"]
    for prompt in prompts.values():
        assert "official product facts" in prompt
        assert "actual test observations" in prompt
        assert "subjective opinion" in prompt
        assert "unsupported industry conclusions" in prompt


def test_representative_prompts_encode_non_overlapping_role_boundaries(representative_context_and_profile):
    context, profile = representative_context_and_profile
    prompts = TechMediaReviewer().build_prompts(context, profile)

    assert "unsupported absolute or superlative claims require NEED_TEXT_FIX" in prompts["COMPLIANCE"]
    assert "tone or editorial-independence concerns alone use PASS_WITH_SUGGESTIONS" in prompts["BRAND"]
    assert "pending hotel capabilities or comparisons require HUMAN_REVIEW" in prompts["PRODUCT_ACCURACY"]
    assert "unbound 亲测/实测 claims and missing test conditions or boundaries require HUMAN_REVIEW" in prompts["TEST_CREDIBILITY"]
    assert "ad-like unsupported conclusions may require NEED_TEXT_FIX" in prompts["CONTENT_QUALITY"]
    assert "suggestions-only and cannot independently block" in prompts["CAMPAIGN_EFFECTIVENESS"]


def test_representative_valid_protocol_preserves_revision_and_human_review(representative_context_and_profile):
    context, profile = representative_context_and_profile

    def result(agent_id, decision, issues):
        return AgentReviewResult(
            agent_id=agent_id,
            agent_version=AGENT_VERSION,
            decision=decision,
            summary="deterministic calibration output",
            score=70,
            confidence=0.95,
            issues=issues,
        ).model_dump_json()

    def finding(rule_id, severity, reference, *, human_required=False):
        return AgentIssue(
            rule_id=rule_id,
            category="calibration",
            severity=severity,
            field="body",
            evidence=EvidenceSpan(quote=rule_id),
            reason="representative calibration finding",
            suggestion="revise or verify the claim",
            source_reference=[reference],
            auto_fixable=False,
            human_required=human_required,
            confidence=0.95,
        )

    outputs = {
        "COMPLIANCE": result("COMPLIANCE", "NEED_TEXT_FIX", [finding("COMPLIANCE-ABSOLUTE", "MEDIUM", "compliance.md")]),
        "BRAND": result("BRAND", "PASS_WITH_SUGGESTIONS", [finding("BRAND-TONE", "LOW", "brand_consistency.md")]),
        "PRODUCT_ACCURACY": result("PRODUCT_ACCURACY", "HUMAN_REVIEW", [finding("PENDING-HOTEL", "HIGH", "PENDING-002", human_required=True)]),
        "TEST_CREDIBILITY": result("TEST_CREDIBILITY", "HUMAN_REVIEW", [finding("EVIDENCE-UNBOUND", "HIGH", "test_credibility.md", human_required=True)]),
        "CONTENT_QUALITY": result("CONTENT_QUALITY", "NEED_TEXT_FIX", [finding("QUALITY-ADLIKE", "MEDIUM", "content_quality.md")]),
        "CAMPAIGN_EFFECTIVENESS": result("CAMPAIGN_EFFECTIVENESS", "PASS_WITH_SUGGESTIONS", [finding("CAMPAIGN-HOOK", "LOW", "campaign_effectiveness.md")]),
    }

    class LLM:
        def chat_json(self, prompt, schema):
            agent_id = next(agent for agent in AGENT_ORDER if f"Specialist: {agent}" in prompt)
            return outputs[agent_id]

    agent_results = TechMediaReviewer(llm=LLM()).review_structured(context, profile)
    deterministic = evaluate_rules(profile, context)
    arbitration = arbitrate_review(agent_results, deterministic, safe_auto_fix_rule_ids=set(profile.safe_replacement_map))

    assert [item.agent_id for item in agent_results] == list(AGENT_ORDER)
    assert [item.decision for item in agent_results] == [
        "NEED_TEXT_FIX", "PASS_WITH_SUGGESTIONS", "HUMAN_REVIEW",
        "HUMAN_REVIEW", "NEED_TEXT_FIX", "PASS_WITH_SUGGESTIONS",
    ]
    assert arbitration.review_status is ReviewStatus.HUMAN_REVIEW_REQUIRED
    assert arbitration.publish_status is PublishStatus.NOT_READY
    assert {task.task_type for task in arbitration.task_specs} == {"HUMAN_REVIEW", "SUPPLIER_REVISION"}


def test_heuristic_mode_returns_six_human_review_results_without_semantic_findings():
    results = TechMediaReviewer().review_structured(CONTEXT, PROFILE)
    assert len(results) == 6
    assert all(result.decision == "HUMAN_REVIEW" for result in results)
    assert all(result.issues and result.issues[0].human_required for result in results)
    assert all(result.issues[0].rule_id == "SYSTEM-LLM-UNAVAILABLE" for result in results)
    assert all(result.confidence > 0.9 for result in results)


def test_llm_parse_retry_succeeds_on_third_call():
    class LLM:
        def __init__(self):
            self.calls = 0

        def chat(self, prompt):
            self.calls += 1
            return "not json" if self.calls < 3 else AgentReviewResult(
                agent_id="COMPLIANCE", agent_version=AGENT_VERSION, decision="PASS",
                summary="ok", score=90, confidence=0.9, issues=[]
            ).model_dump_json()

    llm = LLM()
    results = TechMediaReviewer(llm=llm).review_structured(CONTEXT, PROFILE)
    assert llm.calls == 18
    assert results[0].decision == "PASS"


def test_oneapi_chat_json_sends_strict_schema_without_api_key_in_body(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    class Requests:
        def post(self, url, **kwargs):
            captured.update(url=url, kwargs=kwargs)
            return Response()

    monkeypatch.setenv("ONEAPI_KEY", "secret-key")
    monkeypatch.setenv("ONEAPI_MODEL", "model")
    client = OpenAICompatLLM()
    client._requests = Requests()
    client.chat_json("prompt", AgentReviewResult)

    body = captured["kwargs"]["json"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "agent_id" in body["response_format"]["json_schema"]["schema"]["properties"]
    assert "secret-key" not in json.dumps(body)


def test_exhausted_llm_retry_returns_human_review_not_pass():
    class LLM:
        def chat(self, prompt):
            return "not json"

    result = TechMediaReviewer(llm=LLM()).review_structured(CONTEXT, PROFILE)[0]
    assert result.decision == "HUMAN_REVIEW"
    assert result.issues[0].human_required is True
    assert "unavailable review" in result.issues[0].reason
    assert result.confidence > 0.8
