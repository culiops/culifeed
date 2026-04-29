"""
Base AI Provider Interface
=========================

Abstract base classes and data models for AI provider implementations
supporting article relevance analysis and summarization.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum
import time

from ...database.models import Article, Topic
from ...utils.exceptions import CuliFeedError, ErrorCode


class AIProviderType(str, Enum):
    """Available AI provider types."""

    GROQ = "groq"
    GEMINI = "gemini"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"


@dataclass
class RateLimitInfo:
    """Rate limit information for API usage tracking."""

    requests_per_minute: int
    requests_per_day: int
    tokens_per_minute: Optional[int] = None
    tokens_per_day: Optional[int] = None
    current_usage: int = 0
    reset_time: Optional[float] = None

    def is_exhausted(self) -> bool:
        """Check if rate limit is exhausted."""
        return self.current_usage >= self.requests_per_day

    def can_make_request(self) -> bool:
        """Check if we can make another request."""
        if self.reset_time and time.time() < self.reset_time:
            return False
        return not self.is_exhausted()


@dataclass
class AIResult:
    """Result from AI processing with relevance score and summary."""

    success: bool
    relevance_score: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    summary: Optional[str] = None
    content: Optional[Any] = None  # General content field for any result type
    reasoning: Optional[str] = None
    matched_keywords: Optional[List[str]] = None
    provider: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    processing_time_ms: Optional[int] = None
    error_message: Optional[str] = None

    @property
    def is_relevant(self) -> bool:
        """Check if article is considered relevant (score >= 0.7)."""
        return self.success and self.relevance_score >= 0.7

    @property
    def quality_score(self) -> float:
        """Combined quality score factoring relevance and confidence."""
        if not self.success:
            return 0.0
        return (self.relevance_score * 0.7) + (self.confidence * 0.3)


class AIError(CuliFeedError):
    """AI processing specific error."""

    def __init__(
        self,
        message: str,
        provider: str,
        error_code: ErrorCode = None,
        rate_limited: bool = False,
        retryable: bool = False,
    ):
        super().__init__(
            message,
            error_code or ErrorCode.AI_PROCESSING_ERROR,
            recoverable=retryable,  # Map retryable to recoverable for parent class
        )
        self.provider = provider
        self.rate_limited = rate_limited
        self.retryable = retryable


class AIProvider(ABC):
    """Abstract base class for AI provider implementations."""

    def __init__(self, api_key: str, model_name: str, provider_type: AIProviderType):
        """Initialize AI provider.

        Args:
            api_key: API key for the provider
            model_name: Model to use for requests
            provider_type: Type of provider
        """
        self.api_key = api_key
        self.model_name = model_name
        self.provider_type = provider_type
        self._rate_limit_info: Optional[RateLimitInfo] = None

        # Import settings for centralized config access
        from ...config.settings import get_settings

        self._settings = get_settings()

    async def complete(self, prompt: str) -> str:
        """Raw single-prompt completion. Returns the model's text output.

        Subclasses should override.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement complete()")

    @abstractmethod
    async def analyze_relevance(self, article: Article, topic: Topic) -> AIResult:
        """Analyze article relevance to a topic.

        Args:
            article: Article to analyze
            topic: Topic to match against

        Returns:
            AIResult with relevance analysis

        Raises:
            AIError: If analysis fails
        """
        pass

    @abstractmethod
    async def generate_summary(
        self, article: Article, max_sentences: int = 3
    ) -> AIResult:
        """Generate article summary.

        Args:
            article: Article to summarize
            max_sentences: Maximum sentences in summary

        Returns:
            AIResult with generated summary

        Raises:
            AIError: If summarization fails
        """
        pass

    @abstractmethod
    async def generate_keywords(
        self, topic_name: str, context: str = "", max_keywords: int = 7
    ) -> AIResult:
        """Generate keywords for a topic.

        Args:
            topic_name: Topic name to generate keywords for
            context: Additional context (e.g., existing user topics)
            max_keywords: Maximum number of keywords to generate

        Returns:
            AIResult with generated keywords in content field
        """
        pass

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test API connection and authentication.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    def get_rate_limits(self) -> RateLimitInfo:
        """Get rate limit information for this provider.

        Returns:
            RateLimitInfo with current limits and usage
        """
        pass

    def update_rate_limit_usage(self, tokens_used: int = 0) -> None:
        """Update rate limit usage tracking.

        Args:
            tokens_used: Number of tokens consumed in last request
        """
        if self._rate_limit_info:
            self._rate_limit_info.current_usage += 1

    def can_make_request(self) -> bool:
        """Check if provider can make another request within rate limits.

        Returns:
            True if request can be made, False if rate limited
        """
        if not self._rate_limit_info:
            return True
        return self._rate_limit_info.can_make_request()

    def _create_success_result(
        self,
        relevance_score: float,
        confidence: float,
        summary: str = None,
        reasoning: str = None,
        matched_keywords: List[str] = None,
        tokens_used: int = None,
        processing_time_ms: int = None,
        content: Any = None,
    ) -> AIResult:
        """Create successful AI result.

        Args:
            relevance_score: Relevance score 0.0-1.0
            confidence: Confidence score 0.0-1.0
            summary: Optional article summary
            reasoning: Optional reasoning explanation
            matched_keywords: Optional matched keywords
            tokens_used: Optional token usage count
            processing_time_ms: Optional processing time
            content: Optional content for general use (e.g., keywords list)

        Returns:
            AIResult indicating success
        """
        return AIResult(
            success=True,
            relevance_score=max(0.0, min(1.0, relevance_score)),  # Clamp to 0-1
            confidence=max(0.0, min(1.0, confidence)),  # Clamp to 0-1
            summary=summary,
            content=content,
            reasoning=reasoning,
            matched_keywords=matched_keywords or [],
            provider=(
                self.provider_type.value
                if hasattr(self.provider_type, "value")
                else str(self.provider_type)
            ),
            model_used=self.model_name,
            tokens_used=tokens_used,
            processing_time_ms=processing_time_ms,
        )

    def _create_error_result(self, error_message: str) -> AIResult:
        """Create error AI result.

        Args:
            error_message: Error description

        Returns:
            AIResult indicating failure
        """
        return AIResult(
            success=False,
            relevance_score=0.0,
            confidence=0.0,
            provider=(
                self.provider_type.value
                if hasattr(self.provider_type, "value")
                else str(self.provider_type)
            ),
            model_used=self.model_name,
            error_message=error_message,
        )

    def _build_relevance_prompt(self, article: Article, topic: Topic) -> str:
        """Build relevance analysis prompt.

        Args:
            article: Article to analyze
            topic: Topic to match against

        Returns:
            Formatted prompt string
        """
        keywords_str = (
            ", ".join(topic.keywords) if topic.keywords else "general interest"
        )
        exclude_str = (
            f"\nEXCLUDE articles about: {', '.join(topic.exclude_keywords)}"
            if topic.exclude_keywords
            else ""
        )

        return f"""Analyze this article's relevance to the topic "{topic.name}".

TOPIC: {topic.name}
KEYWORDS: {keywords_str}{exclude_str}

ARTICLE TITLE: {article.title}
ARTICLE CONTENT: {article.content[:1500]}...

Instructions:
1. Analyze how well this article matches the topic keywords and description
2. Consider exclude keywords as negative signals
3. Provide relevance score from 0.0 to 1.0 (1.0 = perfect match)
4. Provide confidence score from 0.0 to 1.0 (1.0 = very confident)
5. List matched keywords if any
6. Explain your reasoning briefly

Respond in this exact format:
RELEVANCE_SCORE: [0.0-1.0]
CONFIDENCE: [0.0-1.0]
MATCHED_KEYWORDS: [keyword1, keyword2, ...]
REASONING: [brief explanation]"""

    def _build_summary_prompt(self, article: Article, max_sentences: int = 3) -> str:
        """Build summarization prompt with character length constraints.

        Args:
            article: Article to summarize
            max_sentences: Maximum sentences in summary (kept for backward compatibility)

        Returns:
            Formatted prompt string with length constraints
        """
        # Use centralized config - SINGLE SOURCE OF TRUTH
        max_chars = self._settings.delivery_quality.max_summary_length
        min_chars = max(300, int(max_chars * 0.7))  # Minimum 70% of max, at least 300

        return f"""Summarize this article in {min_chars}-{max_chars} characters. This summary will be displayed on mobile devices, so it must be concise yet comprehensive.

ARTICLE TITLE: {article.title}
ARTICLE CONTENT: {article.content}

Requirements:
1. Keep summary between {min_chars}-{max_chars} characters (including spaces)
2. Write 2-4 clear, informative sentences 
3. Capture the main points and key benefits/implications
4. Use clear, concise language suitable for mobile reading
5. Maintain factual accuracy
6. Focus on the most important aspects and business value
7. Do NOT exceed {max_chars} characters total

SUMMARY:"""

    def _parse_relevance_response(
        self, response_text: str
    ) -> tuple[float, float, List[str], str]:
        """Parse relevance analysis response.

        Args:
            response_text: AI response text to parse

        Returns:
            Tuple of (relevance_score, confidence, matched_keywords, reasoning)
        """
        relevance_score = 0.0
        confidence = 0.0
        matched_keywords = []
        reasoning = "No reasoning provided"

        lines = response_text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("RELEVANCE_SCORE:"):
                try:
                    relevance_score = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("MATCHED_KEYWORDS:"):
                keywords_str = line.split(":", 1)[1].strip()
                if keywords_str and keywords_str != "[]":
                    # Parse keywords from string like "[keyword1, keyword2]" or "keyword1, keyword2"
                    keywords_str = keywords_str.strip("[]")
                    matched_keywords = [
                        k.strip() for k in keywords_str.split(",") if k.strip()
                    ]
            elif line.startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()

        return relevance_score, confidence, matched_keywords, reasoning
