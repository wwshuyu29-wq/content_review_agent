from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.text_review.reviewers.base import AgentIssue, AgentReviewResult, EvidenceSpan
from scripts.text_review.reviewers.llm import OpenAICompatLLM, oneapi_strict_schema
from scripts.text_review.reviewers.tech_media import AGENT_ORDER, AGENT_VERSION, TechMediaReviewer, validate_agent_result
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
    compiled = compile_standard_package(load_standard_package(root / "data" / "standards", "bdmap_xdxx_tech_review_2026", "1.0"))
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
    with pytest.raises(ValidationError):
        AgentReviewResult(
            agent_id="COMPLIANCE", agent_version="v1", decision="PASS",
            summary="ok", score=None, confidence=1, issues=[],
        )
    unavailable_issue = AgentIssue(
        rule_id="SYSTEM-LLM-UNAVAILABLE", category="system", severity="HIGH", field="review",
        evidence=EvidenceSpan(quote=""), reason="不可用", suggestion="人工审核",
        source_reference=["SYSTEM:LLM_UNAVAILABLE"], auto_fixable=False,
        human_required=True, confidence=1,
    )
    with pytest.raises(ValidationError):
        AgentReviewResult(
            agent_id="COMPLIANCE", agent_version="v1", decision="PASS",
            summary="not unavailable", score=None, confidence=1, issues=[unavailable_issue],
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


def test_specialist_prompts_require_stable_protocol_identity() -> None:
    prompts = TechMediaReviewer().build_prompts(CONTEXT, PROFILE)

    for agent_id, prompt in prompts.items():
        assert f"Set agent_id exactly to {agent_id}." in prompt
        assert f"Set agent_version exactly to {AGENT_VERSION}." in prompt


def test_specialist_prompts_limit_issue_references_to_exact_allowlist() -> None:
    prompts = TechMediaReviewer().build_prompts(CONTEXT, PROFILE)

    for prompt in prompts.values():
        assert "Use only the exact allowed_source_references values" in prompt
        assert '"allowed_source_references"' in prompt
        assert '"CLAIM-001"' in prompt


def test_campaign_prompt_limits_suggestions_to_non_blocking_low_issues() -> None:
    prompt = TechMediaReviewer().build_prompts(CONTEXT, PROFILE)["CAMPAIGN_EFFECTIVENESS"]

    assert "Every suggestion issue must use severity LOW and human_required false" in prompt


def test_production_prompts_use_configured_public_specialist_and_one_primary_standard(
    representative_context_and_profile,
):
    context, profile = representative_context_and_profile
    prompts = TechMediaReviewer().build_prompts(context, profile)
    standard_markers = {
        "COMPLIANCE": "[COM-ABS-001]",
        "BRAND": "[BRAND-NAME-001]",
        "PRODUCT_ACCURACY": "[ACC-FUNC-001]",
        "TEST_CREDIBILITY": "[TEST-TRIGGER-001]",
        "CONTENT_QUALITY": "[QUAL-TITLE-001]",
        "CAMPAIGN_EFFECTIVENESS": "[CAM-GOAL-001]",
    }

    for agent_id, prompt in prompts.items():
        assert "六个审核 Agent 公共约束" in prompt
        assert profile.agent_prompts[agent_id].splitlines()[0] in prompt
        assert standard_markers[agent_id] in prompt
        assert sum(marker in prompt for marker in standard_markers.values()) == 1


def test_v1_prompt_building_rejects_missing_configured_binding() -> None:
    profile = PROFILE.model_copy(update={"package_version": "1.0"})

    with pytest.raises(ValueError, match="configured.*binding|binding.*configured"):
        TechMediaReviewer().build_prompts(CONTEXT, profile)


def test_clean_text_without_structured_media_does_not_load_authorization(
    representative_context_and_profile,
):
    _, profile = representative_context_and_profile
    context = ReviewContext(title="路线规划体验", body="路线结构清晰。", platform="xiaohongshu")
    prompts = TechMediaReviewer().build_prompts(context, profile)

    assert all("[AUTH-" not in prompt for prompt in prompts.values())


@pytest.mark.parametrize(
    "context_update",
    [
        {"evidence": [{"asset_id": "log-1", "kind": "test_log", "status": "approved"}]},
        {"evidence_assets": [{"asset_id": "screenshot-1", "source": "first_party", "license_status": "approved"}]},
    ],
)
def test_ordinary_bound_logs_and_approved_first_party_screenshots_do_not_load_authorization(
    representative_context_and_profile,
    context_update,
):
    _, profile = representative_context_and_profile
    context = ReviewContext(title="路线规划体验", body="路线结构清晰。", **context_update)
    prompts = TechMediaReviewer().build_prompts(context, profile)

    assert all("[AUTH-" not in prompt for prompt in prompts.values())


@pytest.mark.parametrize(
    "context_update",
    [
        {"evidence_assets": [{"asset_id": "asset-1", "source": "external", "license_status": "unknown"}]},
        {"evidence_assets": [{"asset_id": "asset-2", "privacy_sensitive": True}]},
        {"evidence_assets": [{"asset_id": "asset-3", "person_detected": True}]},
    ],
)
def test_authorization_relevance_metadata_loads_supplement_for_compliance_and_brand(
    representative_context_and_profile,
    context_update,
):
    _, profile = representative_context_and_profile
    context = ReviewContext(title="路线规划体验", body="路线结构清晰。", **context_update)
    prompts = TechMediaReviewer().build_prompts(context, profile)

    assert "[AUTH-" in prompts["COMPLIANCE"]
    assert "[AUTH-" in prompts["BRAND"]
    assert all("[AUTH-" not in prompts[agent_id] for agent_id in AGENT_ORDER[2:])


def test_authorization_standard_is_conditionally_supplemental_for_compliance_and_brand_only(
    representative_context_and_profile,
):
    context, profile = representative_context_and_profile
    context = context.model_copy(update={"body": context.body + " 素材来自第三方，授权情况待确认。"})
    prompts = TechMediaReviewer().build_prompts(context, profile)

    assert "[AUTH-" in prompts["COMPLIANCE"]
    assert "[AUTH-" in prompts["BRAND"]
    assert all("[AUTH-" not in prompts[agent_id] for agent_id in AGENT_ORDER[2:])


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


def test_adversarial_role_decisions_are_rejected_without_losing_legitimate_specialist_escalation():
    calls = {agent_id: 0 for agent_id in AGENT_ORDER}

    def finding(agent_id, category):
        return AgentIssue(
            rule_id=f"{agent_id}-001", category=category, severity="HIGH", field="body",
            evidence=EvidenceSpan(quote=agent_id), reason="adversarial result", suggestion="review",
            source_reference=["project.yaml"], auto_fixable=False, human_required=True, confidence=0.9,
        )

    outputs = {
        "COMPLIANCE": ("BLOCK", [finding("COMPLIANCE", "compliance")]),
        "BRAND": ("HUMAN_REVIEW", [finding("BRAND", "brand_tone")]),
        "PRODUCT_ACCURACY": ("HUMAN_REVIEW", [finding("PRODUCT_ACCURACY", "product_fact")]),
        "TEST_CREDIBILITY": ("HUMAN_REVIEW", [finding("TEST_CREDIBILITY", "test_evidence")]),
        "CONTENT_QUALITY": ("PASS", []),
        "CAMPAIGN_EFFECTIVENESS": ("BLOCK", [finding("CAMPAIGN_EFFECTIVENESS", "campaign")]),
    }

    class LLM:
        def chat_json(self, prompt, schema):
            agent_id = next(agent for agent in AGENT_ORDER if f"Specialist: {agent}" in prompt)
            calls[agent_id] += 1
            decision, issues = outputs[agent_id]
            return AgentReviewResult(
                agent_id=agent_id, agent_version=AGENT_VERSION, decision=decision,
                summary="adversarial", score=50, confidence=0.9, issues=issues,
            ).model_dump_json()

    results = TechMediaReviewer(llm=LLM()).review_structured(CONTEXT, PROFILE)
    by_agent = {result.agent_id: result for result in results}

    assert by_agent["COMPLIANCE"].decision == "BLOCK"
    assert by_agent["PRODUCT_ACCURACY"].decision == "HUMAN_REVIEW"
    assert by_agent["TEST_CREDIBILITY"].decision == "HUMAN_REVIEW"
    assert by_agent["BRAND"].decision == "PASS_WITH_SUGGESTIONS"
    assert by_agent["CAMPAIGN_EFFECTIVENESS"].decision == "PASS_WITH_SUGGESTIONS"
    assert calls["BRAND"] == 3
    assert calls["CAMPAIGN_EFFECTIVENESS"] == 3


def test_brand_fact_conflict_can_legitimately_escalate():
    issue = AgentIssue(
        rule_id="BRAND-FACT-001", category="brand_fact", severity="HIGH", field="body",
        evidence=EvidenceSpan(quote="错误产品名"), reason="与品牌事实冲突", suggestion="更正产品名",
        source_reference=["project.yaml"], auto_fixable=False, human_required=True, confidence=0.9,
    )

    TechMediaReviewer._validate_coherence(
        AgentReviewResult(
            agent_id="BRAND", agent_version=AGENT_VERSION, decision="HUMAN_REVIEW",
            summary="brand fact conflict", score=20, confidence=0.9, issues=[issue],
        ),
        "BRAND",
        PROFILE,
    )


def test_heuristic_mode_returns_nonblocking_campaign_fallback_and_human_review_for_required_roles():
    results = TechMediaReviewer().review_structured(CONTEXT, PROFILE)
    assert len(results) == 6
    assert all(result.decision == "HUMAN_REVIEW" for result in results[:-1])
    assert results[-1].decision == "PASS_WITH_SUGGESTIONS"
    assert all(result.issues for result in results)
    assert all(result.issues[0].rule_id == "SYSTEM-LLM-UNAVAILABLE" for result in results)
    assert all(result.score is None for result in results)
    assert all(result.confidence > 0.9 for result in results)
    assert all("不可用" in result.summary for result in results)
    assert all("不可用" in result.issues[0].reason for result in results)
    assert all("重试" in result.issues[0].suggestion or "人工审核" in result.issues[0].suggestion for result in results)


def test_protocol_validator_requires_score_but_allows_controlled_unavailable_null_score():
    unavailable = TechMediaReviewer._unavailable("CAMPAIGN_EFFECTIVENESS", "gateway failure").model_dump()

    assert unavailable["score"] is None
    assert validate_agent_result(unavailable, "CAMPAIGN_EFFECTIVENESS", set()) is None

    missing_score = {key: value for key, value in unavailable.items() if key != "score"}
    assert "missing" in validate_agent_result(missing_score, "CAMPAIGN_EFFECTIVENESS", set()).lower()

    noncontrolled = {**unavailable, "issues": [{**unavailable["issues"][0], "rule_id": "OTHER-ISSUE"}]}
    assert "score" in validate_agent_result(noncontrolled, "CAMPAIGN_EFFECTIVENESS", set()).lower()


def _protocol_result(
    agent_id: str,
    *,
    agent_version: str = AGENT_VERSION,
    decision: str = "PASS",
    issues: list[dict] | None = None,
) -> dict:
    return {
        "agent_id": agent_id,
        "agent_version": agent_version,
        "decision": decision,
        "summary": "协议行为测试",
        "score": 80,
        "confidence": 0.9,
        "issues": issues or [],
    }


def _protocol_issue(*, severity: str = "HIGH", human_required: bool = True, reference: str = "CLAIM-001") -> dict:
    return {
        "rule_id": "PROTOCOL-001",
        "category": "protocol",
        "severity": severity,
        "field": "body",
        "evidence": {"quote": "证据"},
        "reason": "协议行为测试",
        "suggestion": "修正协议输出",
        "source_reference": [reference],
        "auto_fixable": False,
        "human_required": human_required,
        "confidence": 0.9,
    }


@pytest.mark.parametrize(
    ("expected_agent_id", "invalid_result", "error_fragment"),
    [
        ("COMPLIANCE", _protocol_result("BRAND"), "agent_id must be COMPLIANCE"),
        (
            "COMPLIANCE",
            _protocol_result("COMPLIANCE", agent_version="unexpected-v2"),
            "unexpected agent_version",
        ),
        (
            "COMPLIANCE",
            _protocol_result(
                "COMPLIANCE",
                decision="HUMAN_REVIEW",
                issues=[_protocol_issue(reference="UNKNOWN-SOURCE")],
            ),
            "unknown issue references",
        ),
        (
            "CAMPAIGN_EFFECTIVENESS",
            _protocol_result(
                "CAMPAIGN_EFFECTIVENESS",
                decision="PASS_WITH_SUGGESTIONS",
                issues=[_protocol_issue(severity="HIGH", human_required=True)],
            ),
            "invalid suggestions",
        ),
    ],
)
def test_protocol_violations_are_rejected_then_retry_to_controlled_fallback(
    expected_agent_id: str,
    invalid_result: dict,
    error_fragment: str,
) -> None:
    parsed = AgentReviewResult.model_validate(invalid_result)
    assert error_fragment in validate_agent_result(
        parsed,
        expected_agent_id,
        set(PROFILE.known_source_references),
    )

    class LLM:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, prompt, schema):
            self.calls += 1
            return invalid_result

    llm = LLM()
    result = TechMediaReviewer(llm=llm)._llm_result(
        expected_agent_id,
        "behavioral protocol test",
        PROFILE,
    )

    assert llm.calls == 3
    assert result.score is None
    assert result.issues[0].rule_id == "SYSTEM-LLM-UNAVAILABLE"
    if expected_agent_id == "CAMPAIGN_EFFECTIVENESS":
        assert result.decision == "PASS_WITH_SUGGESTIONS"
        assert result.issues[0].severity == "LOW"
        assert result.issues[0].human_required is False
    else:
        assert result.decision == "HUMAN_REVIEW"
        assert result.issues[0].human_required is True


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


def test_oneapi_schema_recursively_requires_all_properties_and_preserves_nullable_optionals():
    source = AgentReviewResult.model_json_schema()
    adapted = oneapi_strict_schema(source)
    evidence = adapted["$defs"]["EvidenceSpan"]

    assert set(adapted["required"]) == set(adapted["properties"])
    assert set(adapted["$defs"]["AgentIssue"]["required"]) == set(adapted["$defs"]["AgentIssue"]["properties"])
    assert set(evidence["required"]) == set(evidence["properties"])
    assert evidence["additionalProperties"] is False
    assert {branch.get("type") for branch in evidence["properties"]["asset_id"]["anyOf"]} >= {"string", "null"}
    assert "asset_id" not in source["$defs"]["EvidenceSpan"].get("required", [])


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
    schema = body["response_format"]["json_schema"]["schema"]
    assert "agent_id" in schema["properties"]
    assert set(schema["required"]) == set(schema["properties"])
    assert "secret-key" not in json.dumps(body)


def test_oneapi_http_error_is_useful_and_sanitized(monkeypatch):
    class Response:
        status_code = 400

        def raise_for_status(self):
            raise RuntimeError("400 for https://gateway.example/v1/chat/completions?key=secret-key")

        def json(self):
            return {
                "error": {
                    "message": "schema rejected by https://gateway.example using secret-key",
                    "type": "invalid_request_error",
                    "code": "bad_schema",
                }
            }

    class Requests:
        def post(self, url, **kwargs):
            return Response()

    monkeypatch.setenv("ONEAPI_KEY", "secret-key")
    monkeypatch.setenv("ONEAPI_MODEL", "model")
    client = OpenAICompatLLM()
    client._requests = Requests()

    with pytest.raises(RuntimeError) as raised:
        client.chat_json("prompt", AgentReviewResult)

    message = str(raised.value)
    assert "HTTP 400" in message
    assert "invalid_request_error" in message
    assert "bad_schema" in message
    assert "secret-key" not in message
    assert "https://" not in message


def test_oneapi_http_200_error_envelope_is_sanitized_without_raw_payload(monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "error": {
                    "message": "failed at https://gateway.example/v1/chat/completions with secret-key",
                    "type": "gateway_error",
                    "code": "upstream_failed",
                    "request": {"messages": [{"content": "private prompt fragment"}]},
                    "response_body": "private response fragment",
                }
            }

    class Requests:
        def post(self, url, **kwargs):
            return Response()

    monkeypatch.setenv("ONEAPI_KEY", "secret-key")
    monkeypatch.setenv("ONEAPI_MODEL", "model")
    client = OpenAICompatLLM()
    client._requests = Requests()

    with pytest.raises(RuntimeError) as raised:
        client.chat_json("private prompt fragment", AgentReviewResult)

    message = str(raised.value)
    assert "HTTP 200" in message
    assert "gateway_error" in message
    assert "upstream_failed" in message
    for sensitive in (
        "secret-key", "https://", "private prompt fragment", "private response fragment",
        "messages", "request", "response_body",
    ):
        assert sensitive not in message


def test_exhausted_llm_retry_returns_human_review_not_pass():
    class LLM:
        def chat(self, prompt):
            return "not json"

    result = TechMediaReviewer(llm=LLM()).review_structured(CONTEXT, PROFILE)[0]
    assert result.decision == "HUMAN_REVIEW"
    assert result.issues[0].human_required is True
    assert "不可用" in result.issues[0].reason
    assert result.confidence > 0.8
