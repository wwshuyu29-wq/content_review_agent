"""Structured six-agent protocol for technology media product reviews."""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

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
    "COMPLIANCE": "Check legal/safety/compliance wording and unsupported claims; do not assess celebrity or artist rules.",
    "BRAND": "Check supplied product and brand naming, positioning, and tone against relevant supplied facts; do not assess celebrity or artist rules.",
    "PRODUCT_ACCURACY": "Check product features and capabilities only against supplied official product facts; missing support requires HUMAN_REVIEW.",
    "TEST_CREDIBILITY": "Check whether test methodology, observed results, and evidence assets support every test claim. Use the full test/evidence context.",
    "CONTENT_QUALITY": "Check clarity, structure, completeness, and readable expression without inventing factual or semantic findings.",
    "CAMPAIGN_EFFECTIVENESS": "Check whether the content communicates the supplied campaign objective and platform requirements; a low score alone must not block factual compliance.",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


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

    def build_prompts(self, context: ReviewContext, profile: ReviewProfile) -> dict[str, str]:
        common = {
            "project_facts": {
                "business_domain": profile.business_domain,
                "project_code": profile.project_code,
                "content_type": profile.content_type,
                "package_version": profile.package_version,
            },
            "platform": context.platform,
            "content": {"title": context.title, "body": context.body},
            "platform_requirements": dict(profile.platform_requirements),
        }
        prompts = {}
        for agent_id in AGENT_ORDER:
            payload = dict(common)
            payload["relevant_standard_slice"] = self._relevant_rules(agent_id, profile)
            if agent_id == "TEST_CREDIBILITY":
                payload["test_cases"] = list(context.test_cases)
                payload["evidence_manifest"] = list(context.evidence) + list(context.evidence_assets)
            prompts[agent_id] = (
                _SHARED_RULES
                + "\nSpecialist: " + agent_id + "\n"
                + _AGENT_INSTRUCTIONS[agent_id]
                + "\nSupplied context (the only permissible basis for findings):\n"
                + _json(payload)
            )
        return prompts

    @staticmethod
    def _heuristic(agent_id: str) -> AgentReviewResult:
        return AgentReviewResult(
            agent_id=agent_id,
            agent_version=AGENT_VERSION,
            decision="PASS",
            summary="Heuristic mode performed no semantic finding; LLM review is not enabled.",
            score=100,
            confidence=0.0,
            issues=[],
        )

    @staticmethod
    def _unavailable(agent_id: str, reason: str) -> AgentReviewResult:
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

    def _llm_result(self, agent_id: str, prompt: str) -> AgentReviewResult:
        last_error = "no response"
        for _attempt in range(3):
            try:
                raw = self.llm.chat(prompt)
                data = json.loads((raw or "").strip())
                result = AgentReviewResult.model_validate(data)
                if result.agent_id != agent_id:
                    raise ValueError(f"agent_id must be {agent_id}")
                return result
            except Exception as exc:  # transport, JSON, and schema failures all require retry/human review
                last_error = str(exc)
        return self._unavailable(agent_id, last_error)

    def review_structured(self, context: ReviewContext, profile: ReviewProfile) -> list[AgentReviewResult]:
        prompts = self.build_prompts(context, profile)
        if self.llm is None:
            return [self._heuristic(agent_id) for agent_id in AGENT_ORDER]
        return [self._llm_result(agent_id, prompts[agent_id]) for agent_id in AGENT_ORDER]
