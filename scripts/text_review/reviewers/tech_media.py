"""Structured six-agent protocol for technology media product reviews."""
from __future__ import annotations

import json
from typing import Any, Optional, TYPE_CHECKING

from .base import AgentIssue, AgentReviewResult, EvidenceSpan

if TYPE_CHECKING:
    from server.services.deterministic_rule_service import ReviewContext
    from server.services.review_profile_service import ReviewProfile

AGENT_ORDER = (
    "COMPLIANCE",
    "BRAND",
    "PRODUCT_ACCURACY",
    "TEST_CREDIBILITY",
    "CONTENT_QUALITY",
    "CAMPAIGN_EFFECTIVENESS",
)
AGENT_VERSION = "tech-media-v1"

_SHARED_RULES = """You are a structured technology-media review agent.
Distinguish these four evidence classes and never merge them:
1. official product facts: claims explicitly supported by the supplied project facts or official sources;
2. actual test observations: what the supplied test cases and evidence manifest record as observed;
3. author subjective opinion: clearly marked personal impressions, not official facts or test results;
4. unsupported industry conclusions: broad market/industry claims without supplied authoritative support.
Never invent features, results, tests, quotes, assets, sources, or evidence. If the basis for a material conclusion
is missing, use HUMAN_REVIEW and explain what basis is missing. Do not infer a test result from a product fact or opinion.
Return exactly one JSON object and no markdown, prose, code fence, or explanation outside that object.
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
    "TEST_CREDIBILITY": (
        "Check whether test methodology, observed results, and evidence assets support every test claim; unbound 亲测/实测 claims and "
        "missing test conditions or boundaries require HUMAN_REVIEW. Use the full version-specific test/evidence context."
    ),
    "CONTENT_QUALITY": (
        "Check clarity, structure, completeness, title/body consistency, and readable expression; ad-like unsupported conclusions may "
        "require NEED_TEXT_FIX. Do not invent factual or semantic findings."
    ),
    "CAMPAIGN_EFFECTIVENESS": (
        "Check whether the content communicates the supplied campaign objective and platform requirements. This role is suggestions-only "
        "and cannot independently block; return only PASS or PASS_WITH_SUGGESTIONS and never override factual, compliance, or evidence findings."
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
    if agent_id == "BRAND" and decision in {"NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"}:
        fact_categories = {"BRAND_FACT", "BRAND_IDENTITY", "BRAND_NAME", "BRAND_POSITIONING_FACT"}
        if not any(str(_value(issue, "category", "")).upper() in fact_categories for issue in issues):
            return "role boundary: BRAND escalation requires an explicit brand fact or identity conflict"
    return None


def validate_agent_result(result: Any, expected_agent_id: str, known_references: set[str]) -> Optional[str]:
    if _value(result, "agent_id", _value(result, "agent_name")) != expected_agent_id:
        return f"agent_id must be {expected_agent_id}"
    if _value(result, "agent_version") != AGENT_VERSION:
        return f"Agent {expected_agent_id} has an unexpected agent_version"
    if any(_value(result, key) in (None, "") for key in ("decision", "summary", "score")):
        return f"Agent {expected_agent_id} is missing a stable protocol field"
    decision = str(_value(result, "decision", "")).upper()
    if decision not in {"PASS", "PASS_WITH_SUGGESTIONS", "NEED_TEXT_FIX", "HUMAN_REVIEW", "BLOCK"}:
        return f"Agent {expected_agent_id} has an invalid decision"
    issues = list(_value(result, "issues", []) or [])
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
    """Runs the fixed six-agent protocol in heuristic or controlled LLM mode."""

    name = "tech-media-six-agent-v1"

    def __init__(self, llm=None):
        self.llm = llm

    def _relevant_rules(self, agent_id: str, profile: ReviewProfile) -> list[dict[str, Any]]:
        prefixes = {
            "COMPLIANCE": ("COMPLIANCE", "CLAIM", "LEGAL"),
            "BRAND": ("BRAND",),
            "PRODUCT_ACCURACY": ("PRODUCT", "CLAIM", "FACT"),
            "TEST_CREDIBILITY": ("TEST", "EVIDENCE"),
            "CONTENT_QUALITY": ("CONTENT", "QUALITY"),
            "CAMPAIGN_EFFECTIVENESS": ("CAMPAIGN", "PLATFORM"),
        }[agent_id]
        return [rule.model_dump(mode="json") for rule in profile.rules if rule.rule_id.upper().startswith(prefixes)]

    @staticmethod
    def _needs_authorization_standard(context: ReviewContext) -> bool:
        content = f"{context.title}\n{context.body}".lower()
        if any(term in content for term in (
            "授权", "版权", "第三方素材", "素材来源", "隐私", "个人信息",
            "公众人物", "竞品", "社会事件", "截图来源",
        )):
            return True
        structured_assets = list(context.evidence or []) + list(context.evidence_assets or [])
        return bool(structured_assets)

    @staticmethod
    def _is_v1(profile: ReviewProfile) -> bool:
        try:
            return int(profile.package_version.split(".", 1)[0]) >= 1
        except (TypeError, ValueError):
            return False

    def build_prompts(self, context: ReviewContext, profile: ReviewProfile) -> dict[str, str]:
        if self._is_v1(profile):
            if (
                set(profile.agent_standard_bindings) != set(AGENT_ORDER)
                or set(profile.agent_prompts) != set(AGENT_ORDER)
                or not profile.public_prompt
            ):
                raise ValueError("V1 prompt building requires every configured Agent binding and prompt")
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
        }
        claims = {
            "approved_claims": list(profile.approved_claims),
            "pending_claims": list(profile.pending_claims),
        }
        legacy_standard_names = {
            "COMPLIANCE": "compliance",
            "BRAND": "brand_consistency",
            "PRODUCT_ACCURACY": "content_accuracy",
            "TEST_CREDIBILITY": "test_credibility",
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
            "TEST_CREDIBILITY": {
                "standard": primary_standard("TEST_CREDIBILITY"),
                "evidence_requirements": list(profile.evidence_requirements),
                "test_cases": list(context.test_cases),
                "evidence_manifest": list(context.evidence) + list(context.evidence_assets),
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
                + (configured_specialist + "\n" if configured_specialist else "")
                + _AGENT_INSTRUCTIONS[agent_id]
                + "\nSupplied context (the only permissible basis for findings):\n"
                + _json(payload)
            )
        return prompts

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
                reason=f"nonblocking unavailable review: {reason}",
                suggestion="Retry this specialist review before using its optional suggestions.",
                source_reference=["SYSTEM:LLM_UNAVAILABLE"],
                auto_fixable=False,
                human_required=False,
                confidence=0.99,
            )
            return AgentReviewResult(
                agent_id=agent_id,
                agent_version=AGENT_VERSION,
                decision="PASS_WITH_SUGGESTIONS",
                summary="Suggestions-only specialist output was unavailable or outside its role boundary.",
                score=0,
                confidence=0.99,
                issues=[issue],
            )
        issue = AgentIssue(
            rule_id="SYSTEM-LLM-UNAVAILABLE",
            category="system",
            severity="HIGH",
            field="review",
            evidence=EvidenceSpan(quote=""),
            reason=f"unavailable review: {reason}",
            suggestion="Retry the review or route this content to a human reviewer.",
            source_reference=["SYSTEM:LLM_UNAVAILABLE"],
            auto_fixable=False,
            human_required=True,
            confidence=0.99,
        )
        return AgentReviewResult(
            agent_id=agent_id,
            agent_version=AGENT_VERSION,
            decision="HUMAN_REVIEW",
            summary="LLM review unavailable; human review required.",
            score=0,
            confidence=0.99,
            issues=[issue],
        )

    @staticmethod
    def _validate_coherence(result: AgentReviewResult, agent_id: str, profile: ReviewProfile) -> None:
        error = validate_agent_result(result, agent_id, set(profile.known_source_references))
        if error:
            raise ValueError(error)

    def _llm_result(self, agent_id: str, prompt: str, profile: ReviewProfile) -> AgentReviewResult:
        last_error = "no response"
        for _attempt in range(3):
            try:
                if callable(getattr(self.llm, "chat_json", None)):
                    raw = self.llm.chat_json(prompt, AgentReviewResult)
                else:
                    raw = self.llm.chat(prompt)
                data = json.loads((raw or "").strip())
                result = AgentReviewResult.model_validate(data)
                self._validate_coherence(result, agent_id, profile)
                return result
            except Exception as exc:  # transport, JSON, and protocol failures all require retry/human review
                last_error = str(exc)
        return self._unavailable(agent_id, last_error)

    def review_structured(self, context: ReviewContext, profile: ReviewProfile) -> list[AgentReviewResult]:
        prompts = self.build_prompts(context, profile)
        if self.llm is None:
            return [self._heuristic(agent_id) for agent_id in AGENT_ORDER]
        return [self._llm_result(agent_id, prompts[agent_id], profile) for agent_id in AGENT_ORDER]

    def rewrite(self, row: dict, standards) -> tuple[str, str]:
        """Keep compatibility with the legacy workflow without inventing an edit."""
        return str(row.get("title", "")), str(row.get("body", ""))
