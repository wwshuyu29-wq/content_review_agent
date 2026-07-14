from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from scripts.text_review.reviewers.base import AgentIssue, AgentReviewResult, EvidenceSpan
from scripts.text_review.reviewers.tech_media import AGENT_ORDER, TechMediaReviewer
from server.services.deterministic_rule_service import ReviewContext
from server.services.review_profile_service import ReviewProfile


PROFILE = ReviewProfile(
    business_domain="baidu_maps_marketing_review",
    document_type="project_standard",
    project_code="project",
    content_type="TECH_MEDIA_REVIEW",
    package_version="0.9",
    package_digest="digest",
    rules=(),
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
    assert "范丞丞" not in json.dumps(prompts, ensure_ascii=False)
    assert "test_cases" in prompts["TEST_CREDIBILITY"]
    assert "test_cases" not in prompts["BRAND"]
    for prompt in prompts.values():
        assert "official product facts" in prompt
        assert "actual test observations" in prompt
        assert "subjective opinion" in prompt
        assert "unsupported industry conclusions" in prompt


def test_heuristic_mode_returns_six_schema_valid_results_without_semantic_findings():
    results = TechMediaReviewer().review_structured(CONTEXT, PROFILE)
    assert len(results) == 6
    assert all(result.decision in {"PASS", "PASS_WITH_SUGGESTIONS"} for result in results)
    assert all(not result.issues for result in results)


def test_llm_parse_retry_succeeds_on_third_call():
    class LLM:
        def __init__(self):
            self.calls = 0

        def chat(self, prompt):
            self.calls += 1
            return "not json" if self.calls < 3 else AgentReviewResult(
                agent_id="COMPLIANCE", agent_version="v1", decision="PASS",
                summary="ok", score=90, confidence=0.9, issues=[]
            ).model_dump_json()

    llm = LLM()
    results = TechMediaReviewer(llm=llm).review_structured(CONTEXT, PROFILE)
    assert llm.calls == 18
    assert results[0].decision == "PASS"


def test_exhausted_llm_retry_returns_human_review_not_pass():
    class LLM:
        def chat(self, prompt):
            return "not json"

    result = TechMediaReviewer(llm=LLM()).review_structured(CONTEXT, PROFILE)[0]
    assert result.decision == "HUMAN_REVIEW"
    assert result.issues[0].human_required is True
    assert "unavailable review" in result.issues[0].reason
    assert result.confidence > 0.8
