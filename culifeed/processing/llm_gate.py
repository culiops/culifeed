"""Single yes/no LLM judgment over a pre-selected article+topic pair (v2 stage 3)."""

import re
from dataclasses import dataclass

from ..database.models import Article, Topic
from ..utils.logging import get_logger_for_component


@dataclass
class GateResult:
    passed: bool
    confidence: float
    reasoning: str


class LLMGate:
    """Calibrated yes/no judge for the v2 pipeline."""

    def __init__(self, ai_manager):
        self._ai = ai_manager
        self._logger = get_logger_for_component("llm_gate")

    def _build_gate_prompt(self, article: Article, topic: Topic) -> str:
        description = (
            topic.description
            if topic.description
            else f"{topic.name}. Keywords: {', '.join(topic.keywords)}"
        )
        body = (article.content or "")[:1500]
        return f"""You are deciding whether an article is centrally about a topic.

TOPIC: {topic.name}
DESCRIPTION: {description}
KEYWORDS: {', '.join(topic.keywords)}

ARTICLE TITLE: {article.title}
ARTICLE BODY: {body}

Decide:
- "PASS" only if the article's CENTRAL subject matches the topic.
  Tangential mentions, passing references, or different-but-adjacent
  subjects = FAIL.
- Confidence: 0.9+ = strongly central, 0.7 = clearly relevant,
  0.5 = borderline.

Respond in this exact format:
DECISION: PASS | FAIL
CONFIDENCE: 0.0-1.0
REASONING: <one sentence>"""

    @staticmethod
    def _parse(text: str) -> GateResult:
        decision_m = re.search(r"DECISION:\s*(PASS|FAIL)", text, re.IGNORECASE)
        conf_m = re.search(r"CONFIDENCE:\s*([0-9.]+)", text)
        reason_m = re.search(r"REASONING:\s*(.+?)(?:\n|$)", text, re.IGNORECASE | re.DOTALL)

        if not decision_m or not conf_m:
            return GateResult(
                passed=False, confidence=0.0,
                reasoning="Malformed model response",
            )

        try:
            confidence = max(0.0, min(1.0, float(conf_m.group(1))))
        except ValueError:
            confidence = 0.0

        passed = decision_m.group(1).upper() == "PASS"
        reasoning = reason_m.group(1).strip() if reason_m else ""
        return GateResult(passed=passed, confidence=confidence, reasoning=reasoning)

    async def judge(self, article: Article, topic: Topic) -> GateResult:
        prompt = self._build_gate_prompt(article, topic)
        result = await self._ai.complete(prompt)
        if not result.success:
            return GateResult(
                passed=False, confidence=0.0,
                reasoning=f"LLM unavailable: {result.error_message or 'unknown'}",
            )
        return self._parse(result.content or "")
