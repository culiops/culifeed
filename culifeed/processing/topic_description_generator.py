"""LLM-drafted topic description generator (used by /addtopic + backfill)."""

from typing import List

from ..utils.logging import get_logger_for_component


_MAX_DESCRIPTION_LEN = 300


class TopicDescriptionGenerator:
    """Generate a 1-2 sentence topic description from name + keywords.

    Falls back to a deterministic string when the LLM is unavailable.
    """

    def __init__(self, ai_manager):
        self._ai = ai_manager
        self._logger = get_logger_for_component("topic_desc_generator")

    async def generate(self, name: str, keywords: List[str]) -> str:
        prompt = self._build_prompt(name, keywords)
        result = await self._ai.complete(prompt)
        if not result.success or not result.content:
            self._logger.warning(
                "Description generation failed; using fallback",
                extra={"error": result.error_message},
            )
            return self._fallback(name, keywords)
        text = str(result.content).strip().strip('"').strip()
        if not text:
            return self._fallback(name, keywords)
        return text[:_MAX_DESCRIPTION_LEN]

    @staticmethod
    def _fallback(name: str, keywords: List[str]) -> str:
        return f"{name}. Keywords: {', '.join(keywords)}"[:_MAX_DESCRIPTION_LEN]

    @staticmethod
    def _build_prompt(name: str, keywords: List[str]) -> str:
        return f"""Write a 1-2 sentence description of this RSS-feed topic. The description will be used to match articles to the topic via semantic similarity, so be concrete about what the topic IS and what it is NOT.

TOPIC NAME: {name}
KEYWORDS: {', '.join(keywords)}

Constraints:
- Maximum 250 characters.
- Plain prose, no quotes, no lists.
- Be specific about scope: subject area + types of content (announcements, tutorials, analysis).

Respond with the description text only, no preamble."""
