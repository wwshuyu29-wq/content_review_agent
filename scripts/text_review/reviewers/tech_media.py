"""Structured five-dimension scoring protocol for technology media product reviews."""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional, TYPE_CHECKING

from .base import (
    AgentIssue,
    AgentReviewResult,
    BatchManuscriptReviewResponse,
    DimensionReviewBatch,
    EvidenceSpan,
    LightweightBatchReviewResponse,
    allows_unavailable_score,
    parse_json,
)

if TYPE_CHECKING:
    from server.services.deterministic_rule_service import ReviewContext
    from server.services.review_profile_service import ReviewProfile

AGENT_ORDER = (
    "CONTENT_QUALITY",
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "CAMPAIGN_EFFECTIVENESS",
)
AGENT_VERSION = "tech-media-v1"
_DIMENSION_CATEGORY = {
    "CONTENT_QUALITY": "内容质量",
    "COMPLIANCE": "合规表达",
    "BRAND": "品牌一致性",
    "PRODUCT_ACCURACY": "产品准确性",
    "CAMPAIGN_EFFECTIVENESS": "传播有效性",
}
_DIMENSION_REFERENCE_HINTS = {
    "CONTENT_QUALITY": ("content_quality.md", "内容质量.md"),
    "COMPLIANCE": ("合规与广告表达.md", "compliance.md"),
    "BRAND": ("brand_consistency.md", "品牌一致性.md"),
    "PRODUCT_ACCURACY": ("content_accuracy.md", "内容准确性.md", "实测可信度.md"),
    "CAMPAIGN_EFFECTIVENESS": ("campaign_effectiveness.md", "传播有效性.md"),
}

_SHARED_RULES = """You are a structured technology-media content scoring reviewer.
Distinguish these four evidence classes and never merge them:
1. official product facts: claims explicitly supported by the supplied project facts or official sources;
2. actual test observations: what the supplied test cases and evidence manifest record as observed;
3. author subjective opinion: clearly marked personal impressions, not official facts or test results;
4. unsupported industry conclusions: broad market/industry claims without supplied authoritative support.
Never invent features, results, tests, quotes, assets, sources, or evidence. If the basis for a material conclusion
is missing, use HUMAN_REVIEW and explain what basis is missing. Do not infer a test result from a product fact or opinion.
Return exactly the requested JSON object and no markdown, prose, code fence, or explanation outside that object.
The object must contain only: agent_id, agent_version, decision, summary, score, confidence, issues.
Each issue must contain only the fields defined by the issue schema, including an evidence object.
"""

_AGENT_INSTRUCTIONS = {
    "COMPLIANCE": (
        "Check legal/safety/compliance wording and unsupported claims; unsupported absolute or superlative claims "
        "require NEED_TEXT_FIX. Do not decide unknown product capabilities and ignore unrelated external identity or endorsement policy."
    ),
    "BRAND": (
        "Check supplied product and brand naming, positioning, and verified brand facts; tone or editorial-independence concerns alone "
        "use PASS_WITH_SUGGESTIONS; only a true conflict with a supplied brand fact may escalate. Ignore unrelated external identity or endorsement policy."
    ),
    "PRODUCT_ACCURACY": (
        "Check product features and capabilities only against supplied official product facts; pending hotel capabilities or comparisons "
        "require HUMAN_REVIEW. Never infer or invent unknown product behavior."
    ),
    "CONTENT_QUALITY": (
        "Run the first-pass basic content proofreading before specialist review: catch typos, wrong brand names, punctuation, "
        "obvious grammar, title/body consistency, duplicate or broken wording, and low-level readability defects. Do not invent "
        "factual or semantic findings."
    ),
    "CAMPAIGN_EFFECTIVENESS": (
        "Check whether the content communicates the supplied campaign objective and platform requirements. This role is suggestions-only "
        "and cannot independently block; return only PASS or PASS_WITH_SUGGESTIONS and never override factual, compliance, or evidence findings. "
        "Every suggestion issue must use severity LOW and human_required false."
    ),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _value(record: Any, name: str, default: Any = None) -> Any:
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def role_boundary_error(result: Any) -> Optional[str]:
    agent_id = str(_value(result, "agent_id", _value(result, "agent_name", "")))
    decision = str(_value(result, "decision", "")).upper()
    issues = list(_value(result, "issues", []) or [])
    if agent_id == "CAMPAIGN_EFFECTIVENESS" and decision not in {"PASS", "PASS_WITH_SUGGESTIONS"}:
        return "role boundary: CAMPAIGN_EFFECTIVENESS is suggestions-only"
    controlled_unavailable = (
        _value(result, "score") is None
        and allows_unavailable_score(decision, issues)
    )
    if (
        agent_id == "BRAND"
        and decision in {"NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"}
        and not controlled_unavailable
    ):
        fact_categories = {"BRAND_FACT", "BRAND_IDENTITY", "BRAND_NAME", "BRAND_POSITIONING_FACT"}
        if not any(str(_value(issue, "category", "")).upper() in fact_categories for issue in issues):
            return "role boundary: BRAND escalation requires an explicit brand fact or identity conflict"
    return None


def validate_agent_result(result: Any, expected_agent_id: str, known_references: set[str]) -> Optional[str]:
    if _value(result, "agent_id", _value(result, "agent_name")) != expected_agent_id:
        return f"agent_id must be {expected_agent_id}"
    if _value(result, "agent_version") != AGENT_VERSION:
        return f"Agent {expected_agent_id} has an unexpected agent_version"
    if any(_value(result, key) in (None, "") for key in ("decision", "summary")):
        return f"Agent {expected_agent_id} is missing a stable protocol field"
    if isinstance(result, Mapping):
        score_present = "score" in result
    else:
        score_present = hasattr(result, "score")
    if not score_present:
        return f"Agent {expected_agent_id} is missing required score"
    decision = str(_value(result, "decision", "")).upper()
    if decision not in {"PASS", "PASS_WITH_SUGGESTIONS", "NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"}:
        return f"Agent {expected_agent_id} has an invalid decision"
    issues = list(_value(result, "issues", []) or [])
    score = _value(result, "score")
    if score == "" or (score is None and not allows_unavailable_score(decision, issues)):
        return f"Agent {expected_agent_id} has an invalid score"
    for issue in issues:
        references = list(_value(issue, "source_reference", []) or [])
        rule_id = str(_value(issue, "rule_id", ""))
        if rule_id.startswith("SYSTEM-"):
            if not all(str(reference).startswith("SYSTEM:") for reference in references):
                return f"Agent {expected_agent_id} has invalid system issue references"
        elif not references or not set(references) <= known_references:
            return f"Agent {expected_agent_id} has missing or unknown issue references"
    if decision == "PASS" and issues:
        return f"Agent {expected_agent_id} returned PASS with issues"
    if decision in {"HUMAN_REVIEW", "BLOCK"} and not any(
        bool(_value(issue, "human_required", False))
        or str(_value(issue, "severity", "")).upper() in {"HIGH", "CRITICAL"}
        for issue in issues
    ):
        return f"Agent {expected_agent_id} returned a blocking decision without a blocking issue"
    if decision == "NEED_TEXT_FIX" and not any(
        str(_value(issue, "severity", "")).upper() in {"MEDIUM", "MID"}
        and not bool(_value(issue, "human_required", False))
        for issue in issues
    ):
        return f"Agent {expected_agent_id} returned NEED_TEXT_FIX without a non-human medium issue"
    if decision == "PASS_WITH_SUGGESTIONS" and any(
        bool(_value(issue, "human_required", False))
        or str(_value(issue, "severity", "")).upper() != "LOW"
        for issue in issues
    ):
        return f"Agent {expected_agent_id} returned invalid suggestions"
    return role_boundary_error(result)


class TechMediaReviewer:
    """Runs the fixed five-dimension scoring protocol in heuristic or controlled LLM mode."""

    name = "tech-media-five-agent-v1"

    def __init__(self, llm=None):
        self.llm = llm

    def _relevant_rules(self, agent_id: str, profile: ReviewProfile) -> list[dict[str, Any]]:
        prefixes = {
            "COMPLIANCE": ("COMPLIANCE", "CLAIM", "LEGAL"),
            "BRAND": ("BRAND",),
            "PRODUCT_ACCURACY": ("PRODUCT", "CLAIM", "FACT"),
            "CONTENT_QUALITY": ("CONTENT", "QUALITY"),
            "CAMPAIGN_EFFECTIVENESS": ("CAMPAIGN", "PLATFORM"),
        }[agent_id]
        return [rule.model_dump(mode="json") for rule in profile.rules if rule.rule_id.upper().startswith(prefixes)]

    @staticmethod
    def _metadata_flag(record: Mapping[str, Any], *keys: str) -> bool:
        true_values = {"true", "yes", "1", "detected", "sensitive", "external", "third_party"}
        for key in keys:
            value = record.get(key)
            if value is True:
                return True
            if isinstance(value, str) and value.strip().lower() in true_values:
                return True
        return False

    @classmethod
    def _authorization_metadata_relevant(cls, record: Mapping[str, Any]) -> bool:
        source = str(record.get("source", record.get("source_type", record.get("origin", "")))).strip().lower()
        external = source in {"external", "third_party", "third-party", "outside"} or cls._metadata_flag(
            record, "third_party", "external_source"
        )
        license_status = str(record.get("license_status", record.get("license", ""))).strip().lower()
        if external and license_status in {"unknown", "denied", "rejected", "unverified"}:
            return True
        if cls._metadata_flag(
            record,
            "privacy_sensitive", "privacy_flag", "contains_pii", "ocr_sensitive",
            "sensitive_data", "person_detected", "public_figure", "face_detected",
            "competitor", "third_party_brand", "synthetic", "ai_generated",
            "face_swap", "voice_clone", "social_event",
        ):
            return True
        classification = str(record.get("classification", record.get("event_type", ""))).strip().lower()
        return classification in {"social_event", "public_event", "sensitive_event"}

    @classmethod
    def _needs_authorization_standard(cls, context: ReviewContext) -> bool:
        content = f"{context.title}\n{context.body}".lower()
        if any(term in content for term in (
            "授权", "版权", "第三方素材", "素材来源", "隐私", "个人信息",
            "公众人物", "竞品", "社会事件", "截图来源",
        )):
            return True
        structured_records = list(context.evidence or []) + list(context.evidence_assets or [])
        return any(
            cls._authorization_metadata_relevant(record)
            for record in structured_records
            if isinstance(record, Mapping)
        )

    @staticmethod
    def _is_v1(profile: ReviewProfile) -> bool:
        try:
            return int(profile.package_version.split(".", 1)[0]) >= 1
        except (TypeError, ValueError):
            return False

    def build_prompts(self, context: ReviewContext, profile: ReviewProfile) -> dict[str, str]:
        if self._is_v1(profile):
            if (
                not set(AGENT_ORDER).issubset(set(profile.agent_standard_bindings))
                or not set(AGENT_ORDER).issubset(set(profile.agent_prompts))
                or not profile.public_prompt
            ):
                raise ValueError("V1 prompt building requires every active Agent binding and prompt")
        common = {
            "identity": {
                "business_domain": profile.business_domain,
                "project_code": profile.project_code,
                "content_type": profile.content_type,
                "package_version": profile.package_version,
            },
            "platform": context.platform,
            "content": {"title": context.title, "body": context.body},
            "platform_requirements": dict(profile.platform_requirements),
            "allowed_source_references": list(profile.known_source_references),
        }
        claims = {
            "approved_claims": list(profile.approved_claims),
            "pending_claims": list(profile.pending_claims),
        }
        legacy_standard_names = {
            "COMPLIANCE": "compliance",
            "BRAND": "brand_consistency",
            "PRODUCT_ACCURACY": "content_accuracy",
            "CONTENT_QUALITY": "content_quality",
            "CAMPAIGN_EFFECTIVENESS": "campaign_effectiveness",
        }

        def primary_standard(agent_id: str) -> str:
            binding = profile.agent_standard_bindings.get(agent_id, {})
            filename = binding.get("global_standard") if isinstance(binding, dict) else None
            if self._is_v1(profile):
                if not filename or filename not in profile.global_standards:
                    raise ValueError(f"missing configured primary standard binding for {agent_id}")
                return profile.global_standards[filename]
            return profile.global_standards.get(filename or legacy_standard_names[agent_id], "")

        prompt_slices = {
            "COMPLIANCE": {
                "standard": primary_standard("COMPLIANCE"),
                **claims,
            },
            "BRAND": {
                "standard": primary_standard("BRAND"),
                "project_facts": dict(profile.project_facts),
            },
            "PRODUCT_ACCURACY": {
                "standard": primary_standard("PRODUCT_ACCURACY"),
                "project_facts": dict(profile.project_facts),
                **claims,
            },
            "CONTENT_QUALITY": {
                "standard": primary_standard("CONTENT_QUALITY"),
            },
            "CAMPAIGN_EFFECTIVENESS": {
                "standard": primary_standard("CAMPAIGN_EFFECTIVENESS"),
                "platform_requirements": dict(profile.platform_requirements),
                "project_facts": dict(profile.project_facts),
            },
        }
        prompts = {}
        authorization_required = self._needs_authorization_standard(context)
        for agent_id in AGENT_ORDER:
            payload = dict(common)
            standard_slice = dict(prompt_slices[agent_id])
            binding = profile.agent_standard_bindings.get(agent_id, {})
            supplemental_names = binding.get("supplemental_standards", []) if isinstance(binding, dict) else []
            if authorization_required and agent_id in {"COMPLIANCE", "BRAND"}:
                standard_slice["supplemental_standard"] = "\n".join(
                    profile.global_standards.get(filename, "") for filename in supplemental_names
                )
            payload["standard_slice"] = standard_slice
            payload["relevant_structured_rules"] = self._relevant_rules(agent_id, profile)
            configured_public = profile.public_prompt
            configured_specialist = profile.agent_prompts.get(agent_id, "")
            prompts[agent_id] = (
                (configured_public + "\n" if configured_public else "")
                + _SHARED_RULES
                + "\nSpecialist: " + agent_id + "\n"
                + f"Set agent_id exactly to {agent_id}.\n"
                + f"Set agent_version exactly to {AGENT_VERSION}.\n"
                + "Use only the exact allowed_source_references values for every non-system issue source_reference.\n"
                + (configured_specialist + "\n" if configured_specialist else "")
                + _AGENT_INSTRUCTIONS[agent_id]
                + "\nSupplied context (the only permissible basis for findings):\n"
                + _json(payload)
            )
        return prompts

    def build_scoring_prompt(self, context: ReviewContext, profile: ReviewProfile) -> str:
        prompts = self.build_prompts(context, profile)
        dimension_payloads: dict[str, dict[str, Any]] = {}
        for dimension_id, prompt in prompts.items():
            marker = "Supplied context (the only permissible basis for findings):\n"
            _, _, payload_json = prompt.partition(marker)
            dimension_payloads[dimension_id] = json.loads(payload_json)
        dimension_descriptions = {
            "CONTENT_QUALITY": "基础内容校对：错别字、品牌名、语病、重复表达、标题正文一致性。",
            "COMPLIANCE": "合规表达：绝对化、保证式、无依据宣传主张。",
            "BRAND": "品牌一致性：产品名、品牌事实、定位和口径一致性。",
            "PRODUCT_ACCURACY": "产品准确性：功能、能力和未确认口径是否被扩写。",
            "CAMPAIGN_EFFECTIVENESS": "传播有效性：是否表达清楚项目Brief中的营销点，只给非阻断建议。",
        }
        payload = {
            "dimensions": [
                {
                    "dimension_id": dimension_id,
                    "name": dimension_descriptions[dimension_id],
                    "instruction": _AGENT_INSTRUCTIONS[dimension_id],
                    "context": dimension_payloads[dimension_id],
                }
                for dimension_id in AGENT_ORDER
            ],
            "required_order": list(AGENT_ORDER),
            "output_contract": {
                "top_level": "Return one object with key results.",
                "results": "Array of exactly five dimension result objects in required_order.",
                "internal_field_note": "Use agent_id and agent_version only as stable internal field names; treat each object as a scoring dimension, not as a separate agent.",
                "score": "0-100. Higher means the draft is stronger on that dimension.",
                "campaign_boundary": "CAMPAIGN_EFFECTIVENESS may only return PASS or PASS_WITH_SUGGESTIONS.",
            },
        }
        return (
            (profile.public_prompt + "\n" if profile.public_prompt else "")
            + _SHARED_RULES
            + "\nScore this manuscript once across the five dimensions. Do not split this into separate model tasks.\n"
            + "Use the batch/project review_brief as the current product-function and marketing-positioning source of truth. "
            + "Every issue suggestion must be specific to that brief and explain what wording or capability boundary should change.\n"
            + "Set agent_version exactly to " + AGENT_VERSION + " for every result.\n"
            + "Use only the exact allowed_source_references values for every non-system issue source_reference.\n"
            + "Supplied scoring packet:\n"
            + _json(payload)
        )

    def build_batch_scoring_prompt(
        self,
        manuscripts: list[Mapping[str, Any]],
        profile: ReviewProfile,
    ) -> str:
        if not manuscripts:
            raise ValueError("batch scoring requires at least one manuscript")
        first_context = manuscripts[0]["context"]
        project_facts = dict(profile.project_facts)
        brief = str(project_facts.get("batch_review_brief") or project_facts.get("review_brief") or "")[:3000]
        standards = {
            dimension_id: {
                "goal": _AGENT_INSTRUCTIONS[dimension_id],
                "reference": [
                    reference for reference in _DIMENSION_REFERENCE_HINTS[dimension_id]
                    if reference in set(profile.known_source_references)
                ][:2],
            }
            for dimension_id in AGENT_ORDER
        }
        manuscript_payloads = []
        for manuscript in manuscripts:
            context = manuscript["context"]
            manuscript_payloads.append({
                "content_id": manuscript["content_item_id"],
                "platform": context.platform,
                "content": {"title": context.title, "body": context.body},
            })
        payload = {
            "brief": brief,
            "approved_claims": list(profile.approved_claims)[:80],
            "pending_claims": list(profile.pending_claims)[:80],
            "dimensions": standards,
            "manuscripts": manuscript_payloads,
            "required_dimension_order": list(AGENT_ORDER),
        }
        return (
            "你是百度地图小度想想科技媒体内容审核助手。请一次审核这一批稿件。\n"
            "只依据 brief、claims 和每篇正文判断；不要编造测试证据。每篇必须返回五个维度，顺序严格等于 required_dimension_order。\n"
            "MVP 五维度判定标准：\n"
            "基础内容校对：只抓错别字、标点异常、明显语病、重复词句、标题正文不一致、字段缺失导致读不通。\n"
            "合规审核：只抓明显绝对化、第一/唯一/最强、保证效果、无依据承诺、明显虚假宣传或高风险暗示。\n"
            "品牌一致性：只抓品牌名/产品名写错、品牌关系混乱、功能归错产品、官方称谓错误。\n"
            "产品准确性：只抓功能讲错、能力说过头、未确认功能说成确定功能。\n"
            "传播有效性：只判断是否跑题、卖点缺失、平台语感明显不适合；默认只给建议，不阻断。\n"
            "评分逻辑：90-100 分：无影响发布的问题；80-89 分：只有轻微建议；70-79 分：有一处需要修改但不需要人工判断；60-69 分：需要人工确认；60 分以下：明显不可发布。\n"
            "每个维度最多返回 1 个最关键问题；没有明确影响发布的问题就返回空 issues。\n"
            "不要追踪证据字段、测试字段或测试条件缺失，不要因为未上传 test_cases/evidence/实测材料而判问题。\n"
            "不要因为文案没有展开实测过程、实测细节、截图说明或素材证据而判问题；这类佐证暂由供应商素材承担，当前只审文字本身。\n"
            "不要把低风险表达建议计入问题数；低风险只允许放在 summary 或 PASS_WITH_SUGGESTIONS，不放入 issues。\n"
            "CAMPAIGN_EFFECTIVENESS 只能 PASS 或 PASS_WITH_SUGGESTIONS；需要人工判断时用 human=true。\n"
            "输出 JSON，结构为 {\"reviews\":[{\"content_id\":数字,\"dimensions\":[{\"dimension\":\"CONTENT_QUALITY\",\"decision\":\"PASS\",\"score\":90,\"summary\":\"...\",\"issues\":[{\"level\":\"LOW\",\"field\":\"body\",\"quote\":\"原文片段\",\"problem\":\"问题\",\"advice\":\"建议\",\"human\":false}]}]}]}。\n"
            "不要输出 markdown 或解释。\n"
            "审核包：\n"
            + _json(payload)
        )

    @staticmethod
    def _reference_for_dimension(agent_id: str, profile: ReviewProfile) -> list[str]:
        known = set(profile.known_source_references)
        references = [
            reference for reference in _DIMENSION_REFERENCE_HINTS[agent_id]
            if reference in known
        ]
        if references:
            return references[:1]
        return [next(iter(sorted(known)))] if known else [f"SYSTEM:{agent_id}"]

    def _lightweight_to_agent_results(
        self,
        response: LightweightBatchReviewResponse,
        profile: ReviewProfile,
    ) -> dict[int, list[AgentReviewResult]]:
        converted: dict[int, list[AgentReviewResult]] = {}
        for review in response.reviews:
            dimension_results: list[AgentReviewResult] = []
            for dimension in review.dimensions:
                issues = [
                    AgentIssue(
                        rule_id=f"BATCH-{dimension.dimension}-{index:03d}",
                        category=_DIMENSION_CATEGORY[dimension.dimension],
                        severity=issue.level,
                        field=issue.field,
                        evidence=EvidenceSpan(quote=issue.quote),
                        reason=issue.problem,
                        suggestion=issue.advice,
                        source_reference=self._reference_for_dimension(dimension.dimension, profile),
                        auto_fixable=False,
                        human_required=issue.human,
                        confidence=0.85,
                    )
                    for index, issue in enumerate(dimension.issues, start=1)
                ]
                dimension_results.append(AgentReviewResult(
                    agent_id=dimension.dimension,
                    agent_version=AGENT_VERSION,
                    decision=dimension.decision,
                    summary=dimension.summary,
                    score=dimension.score,
                    confidence=0.85,
                    issues=issues,
                ))
            converted[review.content_id] = dimension_results
        return converted

    @staticmethod
    def _heuristic(agent_id: str) -> AgentReviewResult:
        return TechMediaReviewer._unavailable(
            agent_id,
            "semantic review was not performed because LLM is disabled",
        )

    @staticmethod
    def _unavailable(agent_id: str, reason: str) -> AgentReviewResult:
        if agent_id == "CAMPAIGN_EFFECTIVENESS" or (
            agent_id == "BRAND" and reason.startswith("role boundary:")
        ):
            issue = AgentIssue(
                rule_id="SYSTEM-LLM-UNAVAILABLE",
                category="system_suggestion",
                severity="LOW",
                field="review",
                evidence=EvidenceSpan(quote=""),
                reason="建议型专项审核暂时不可用，系统未生成可采信的审核结论。",
                suggestion="请重试该专项审核；在审核恢复前，不要采用其可选建议。",
                source_reference=["SYSTEM:LLM_UNAVAILABLE"],
                auto_fixable=False,
                human_required=False,
                confidence=0.99,
            )
            return AgentReviewResult(
                agent_id=agent_id,
                agent_version=AGENT_VERSION,
                decision="PASS_WITH_SUGGESTIONS",
                summary="建议型专项审核不可用，当前仅返回非阻断性系统提示。",
                score=None,
                confidence=0.99,
                issues=[issue],
            )
        issue = AgentIssue(
            rule_id="SYSTEM-LLM-UNAVAILABLE",
            category="system",
            severity="HIGH",
            field="review",
            evidence=EvidenceSpan(quote=""),
            reason="模型审核暂时不可用，系统未生成可采信的审核结论。",
            suggestion="请重试审核；若仍不可用，请转交人工审核。",
            source_reference=["SYSTEM:LLM_UNAVAILABLE"],
            auto_fixable=False,
            human_required=True,
            confidence=0.99,
        )
        return AgentReviewResult(
            agent_id=agent_id,
            agent_version=AGENT_VERSION,
            decision="HUMAN_REVIEW",
            summary="模型审核不可用，需要人工审核。",
            score=None,
            confidence=0.99,
            issues=[issue],
        )

    @staticmethod
    def _validate_coherence(result: AgentReviewResult, agent_id: str, profile: ReviewProfile) -> None:
        error = validate_agent_result(result, agent_id, set(profile.known_source_references))
        if error:
            raise ValueError(error)

    def _llm_result(
        self,
        agent_id: str,
        prompt: str,
        profile: ReviewProfile,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> AgentReviewResult:
        last_error = "no response"
        if progress_callback is not None:
            progress_callback("agent_started", agent_id=agent_id, attempt=1)
        for attempt in range(1, 4):
            try:
                if callable(getattr(self.llm, "chat_json", None)):
                    raw = self.llm.chat_json(prompt, AgentReviewResult)
                else:
                    raw = self.llm.chat(prompt)
                data = raw if isinstance(raw, Mapping) else json.loads((raw or "").strip())
                result = AgentReviewResult.model_validate(data)
                self._validate_coherence(result, agent_id, profile)
            except Exception as exc:  # transport, JSON, and protocol failures all require retry/human review
                last_error = str(exc)
                if progress_callback is not None and attempt < 3:
                    progress_callback("agent_retry", agent_id=agent_id, attempt=attempt)
                continue
            if progress_callback is not None:
                progress_callback(
                    "agent_completed",
                    agent_id=agent_id,
                    attempt=attempt,
                    result=result,
                )
            return result
        result = self._unavailable(agent_id, last_error)
        if progress_callback is not None:
            progress_callback("agent_failed", agent_id=agent_id, attempt=3, result=result)
        return result

    def _llm_batch_results(
        self,
        prompt: str,
        profile: ReviewProfile,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> list[AgentReviewResult]:
        last_error = "no response"
        if progress_callback is not None:
            for dimension_id in AGENT_ORDER:
                progress_callback("agent_started", agent_id=dimension_id, attempt=1)
        for attempt in range(1, 4):
            try:
                if callable(getattr(self.llm, "chat_json", None)):
                    raw = self.llm.chat_json(prompt, DimensionReviewBatch)
                else:
                    raw = self.llm.chat(prompt)
                data = raw if isinstance(raw, Mapping) else json.loads((raw or "").strip())
                batch = DimensionReviewBatch.model_validate(data)
                results: list[AgentReviewResult] = []
                for result in batch.results:
                    error = validate_agent_result(result, result.agent_id, set(profile.known_source_references))
                    results.append(self._unavailable(result.agent_id, error) if error else result)
            except Exception as exc:  # transport, JSON, and protocol failures all require retry/human review
                last_error = str(exc)
                if progress_callback is not None and attempt < 3:
                    for dimension_id in AGENT_ORDER:
                        progress_callback("agent_retry", agent_id=dimension_id, attempt=attempt)
                continue
            if progress_callback is not None:
                for result in results:
                    progress_callback(
                        "agent_completed",
                        agent_id=result.agent_id,
                        attempt=attempt,
                        result=result,
                    )
            return results
        results = [self._unavailable(dimension_id, last_error) for dimension_id in AGENT_ORDER]
        if progress_callback is not None:
            for result in results:
                progress_callback("agent_failed", agent_id=result.agent_id, attempt=3, result=result)
        return results

    def review_manuscript_batch_structured(
        self,
        manuscripts: list[Mapping[str, Any]],
        profile: ReviewProfile,
    ) -> dict[int, list[AgentReviewResult]]:
        if self.llm is None:
            return {
                int(manuscript["content_item_id"]): [
                    self._heuristic(agent_id) for agent_id in AGENT_ORDER
                ]
                for manuscript in manuscripts
            }
        prompt = self.build_batch_scoring_prompt(manuscripts, profile)
        last_error = "no response"
        for _attempt in range(1, 4):
            try:
                if callable(getattr(self.llm, "chat", None)):
                    raw = self.llm.chat(prompt)
                elif callable(getattr(self.llm, "chat_json", None)):
                    raw = self.llm.chat_json(prompt, LightweightBatchReviewResponse)
                else:
                    raise RuntimeError("LLM client does not support chat")
                if isinstance(raw, Mapping):
                    data = raw
                else:
                    data = parse_json(str(raw)) or json.loads((raw or "").strip())
                if isinstance(data, Mapping) and "items" in data:
                    batch = BatchManuscriptReviewResponse.model_validate(data)
                    expected_ids = [int(manuscript["content_item_id"]) for manuscript in manuscripts]
                    actual_ids = [item.content_item_id for item in batch.items]
                    if actual_ids != expected_ids:
                        raise ValueError("batch result items do not match requested manuscript order")
                    results: dict[int, list[AgentReviewResult]] = {
                        item.content_item_id: list(item.results)
                        for item in batch.items
                    }
                else:
                    lightweight = LightweightBatchReviewResponse.model_validate(data)
                    results = self._lightweight_to_agent_results(lightweight, profile)
                expected_ids = [int(manuscript["content_item_id"]) for manuscript in manuscripts]
                actual_ids = list(results)
                if actual_ids != expected_ids:
                    raise ValueError("batch result items do not match requested manuscript order")
                normalized_by_item: dict[int, list[AgentReviewResult]] = {}
                for content_item_id, item_results in results.items():
                    normalized_item_results = []
                    for result in item_results:
                        error = validate_agent_result(
                            result,
                            result.agent_id,
                            set(profile.known_source_references),
                        )
                        normalized_item_results.append(
                            self._unavailable(result.agent_id, error) if error else result
                        )
                    normalized_by_item[content_item_id] = normalized_item_results
                return normalized_by_item
            except Exception as exc:
                last_error = str(exc)
                continue
        return {
            int(manuscript["content_item_id"]): [
                self._unavailable(agent_id, last_error) for agent_id in AGENT_ORDER
            ]
            for manuscript in manuscripts
        }

    def review_structured(
        self,
        context: ReviewContext,
        profile: ReviewProfile,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> list[AgentReviewResult]:
        prompts = self.build_prompts(context, profile)
        if self.llm is None:
            results = []
            for agent_id in AGENT_ORDER:
                if progress_callback is not None:
                    progress_callback("agent_started", agent_id=agent_id, attempt=1)
                result = self._heuristic(agent_id)
                if progress_callback is not None:
                    progress_callback("agent_failed", agent_id=agent_id, attempt=1, result=result)
                results.append(result)
            return results
        return self._llm_batch_results(
            self.build_scoring_prompt(context, profile),
            profile,
            progress_callback,
        )

    def rewrite(self, row: dict, standards) -> tuple[str, str]:
        """Keep compatibility with the legacy workflow without inventing an edit."""
        return str(row.get("title", "")), str(row.get("body", ""))
