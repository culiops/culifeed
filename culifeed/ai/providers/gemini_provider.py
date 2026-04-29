"""
Google Gemini AI provider implementation for CuliFeed.

This module provides integration with Google's Gemini AI API for content relevance
analysis and summary generation with comprehensive error handling and rate limiting.
"""

import asyncio
import json
import time
from typing import Dict, Any, List, Optional

from culifeed.ai.providers.base import AIProvider, AIResult, RateLimitInfo
from culifeed.config.settings import AIProvider as ConfigAIProvider
from culifeed.database.models import Article, Topic
from culifeed.utils.exceptions import AIError, ErrorCode
from culifeed.utils.logging import get_logger_for_component

# Try to import Gemini library
try:
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold

    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class AIProviderType:
    """AI provider types enum."""

    GEMINI = "gemini"
    GROQ = "groq"
    OPENAI = "openai"


class GeminiProvider(AIProvider):
    """Google Gemini AI provider with async support and comprehensive error handling."""

    # Gemini rate limits for free tier
    DEFAULT_RATE_LIMITS = RateLimitInfo(
        requests_per_minute=15,
        requests_per_day=1500,  # Conservative estimate for free tier
        tokens_per_minute=32000,
        tokens_per_day=None,  # No daily token limit specified
    )

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        """Initialize Gemini provider.

        Args:
            api_key: Google Gemini API key
            model_name: Model to use (default: gemini-1.5-flash)

        Raises:
            AIError: If Gemini library not available or invalid configuration
        """
        if not GEMINI_AVAILABLE:
            raise AIError(
                "Google Generative AI library not installed. Run: pip install google-generativeai",
                provider="gemini",
                error_code=ErrorCode.AI_PROVIDER_UNAVAILABLE,
            )

        if not api_key:
            raise AIError(
                "Gemini API key is required",
                provider="gemini",
                error_code=ErrorCode.AI_INVALID_CREDENTIALS,
            )

        super().__init__(api_key, model_name, AIProviderType.GEMINI)

        # Configure Gemini
        genai.configure(api_key=api_key)

        # Initialize model with safety settings
        self.model = genai.GenerativeModel(
            model_name=model_name,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )

        # Set up logging and rate limiting
        self.logger = get_logger_for_component("gemini_provider")
        self._rate_limit_info = self.DEFAULT_RATE_LIMITS
        self._last_request_time = 0.0
        self._request_count_minute = 0
        self._minute_start = time.time()

        self.logger.info(f"Gemini provider initialized with model: {model_name}")

    async def analyze_relevance(self, article: Article, topic: Topic) -> AIResult:
        """Analyze article relevance using Gemini.

        Args:
            article: Article to analyze
            topic: Topic to match against

        Returns:
            AIResult with relevance analysis
        """
        if not self.can_make_request():
            return self._create_error_result("Rate limit exceeded")

        start_time = time.time()

        try:
            # Build prompt
            prompt = self._build_relevance_prompt(article, topic)

            # Make API request
            self.logger.debug(
                f"Analyzing relevance for article: {article.title[:50]}..."
            )

            response = await self._make_gemini_request(prompt)

            # Parse response - handle safety blocks and empty responses
            try:
                response_text = response.text
            except ValueError as e:
                # Handle cases where response is blocked by safety filters or empty
                if "finish_reason" in str(e):
                    self.logger.warning(f"Gemini response blocked or empty: {e}")
                    return self._create_error_result(
                        "Content blocked by safety filters"
                    )
                else:
                    raise

            relevance_score, confidence, matched_keywords, reasoning = (
                self._parse_relevance_response(response_text)
            )

            # Calculate processing time
            processing_time_ms = int((time.time() - start_time) * 1000)

            # Update usage tracking
            tokens_used = getattr(response, "usage_metadata", None)
            if tokens_used:
                total_tokens = getattr(tokens_used, "total_token_count", 0)
                self.update_rate_limit_usage(total_tokens)
            else:
                self.update_rate_limit_usage(0)

            self.logger.debug(
                f"Relevance analysis complete: score={relevance_score:.3f}, "
                f"confidence={confidence:.3f}, time={processing_time_ms}ms"
            )

            return self._create_success_result(
                relevance_score=relevance_score,
                confidence=confidence,
                reasoning=reasoning,
                matched_keywords=matched_keywords,
                tokens_used=total_tokens if tokens_used else None,
                processing_time_ms=processing_time_ms,
            )

        except Exception as e:
            self.logger.error(f"Gemini relevance analysis error: {e}", exc_info=True)

            # Handle specific Gemini errors
            if "quota" in str(e).lower() or "rate limit" in str(e).lower():
                self._handle_rate_limit_error(e)
                return self._create_error_result("Rate limit exceeded")
            elif "api key" in str(e).lower() or "authentication" in str(e).lower():
                raise AIError(
                    "Invalid Gemini API key",
                    provider="gemini",
                    error_code=ErrorCode.AI_INVALID_CREDENTIALS,
                )
            else:
                raise AIError(
                    f"Gemini API error: {e}",
                    provider="gemini",
                    error_code=ErrorCode.AI_API_ERROR,
                    retryable=True,
                )

    async def generate_summary(
        self, article: Article, max_sentences: int = 3
    ) -> AIResult:
        """Generate article summary using Gemini.

        Args:
            article: Article to summarize
            max_sentences: Maximum sentences in summary

        Returns:
            AIResult with generated summary
        """
        if not self.can_make_request():
            return self._create_error_result("Rate limit exceeded")

        start_time = time.time()

        try:
            # Build prompt
            prompt = self._build_summary_prompt(article, max_sentences)

            # Make API request
            self.logger.debug(
                f"Generating summary for article: {article.title[:50]}..."
            )

            response = await self._make_gemini_request(prompt)

            # Handle safety blocks and empty responses
            try:
                summary = response.text.strip()
            except ValueError as e:
                if "finish_reason" in str(e):
                    self.logger.warning(f"Gemini summary blocked or empty: {e}")
                    return self._create_error_result(
                        "Summary blocked by safety filters"
                    )
                else:
                    raise

            # Clean up summary format
            if summary.startswith("SUMMARY:"):
                summary = summary.replace("SUMMARY:", "").strip()

            # Calculate processing time
            processing_time_ms = int((time.time() - start_time) * 1000)

            # Update usage tracking
            tokens_used = getattr(response, "usage_metadata", None)
            if tokens_used:
                total_tokens = getattr(tokens_used, "total_token_count", 0)
                self.update_rate_limit_usage(total_tokens)
            else:
                self.update_rate_limit_usage(0)

            self.logger.debug(
                f"Summary generated: {len(summary)} chars, time={processing_time_ms}ms"
            )

            return self._create_success_result(
                relevance_score=1.0,  # Summary always succeeds if we get here
                confidence=0.9,  # High confidence for summarization
                summary=summary,
                tokens_used=total_tokens if tokens_used else None,
                processing_time_ms=processing_time_ms,
            )

        except Exception as e:
            self.logger.error(f"Gemini summary generation error: {e}", exc_info=True)
            return self._create_error_result(f"Summary generation failed: {e}")

    async def generate_keywords(
        self, topic_name: str, context: str = "", max_keywords: int = 7
    ) -> AIResult:
        """Generate keywords for a topic using Gemini.

        Args:
            topic_name: Topic name to generate keywords for
            context: Additional context (e.g., existing user topics)
            max_keywords: Maximum number of keywords to generate

        Returns:
            AIResult with generated keywords
        """
        if not self.can_make_request():
            return self._create_error_result("Rate limit exceeded")

        start_time = time.time()

        try:
            # Build prompt for keyword generation
            prompt = f"Generate {max_keywords} relevant keywords for '{topic_name}'.{context} Return comma-separated keywords only."

            # Make API request
            self.logger.debug(f"Generating keywords for topic: {topic_name}")

            response = await self._make_gemini_request(prompt)

            # Handle safety blocks and empty responses
            try:
                response_text = response.text.strip()
            except ValueError as e:
                if "finish_reason" in str(e):
                    self.logger.warning(
                        f"Gemini keyword generation blocked or empty: {e}"
                    )
                    return self._create_error_result(
                        "Keywords blocked by safety filters"
                    )
                else:
                    raise

            # Parse keywords
            keywords = [
                k.strip().strip("\"'") for k in response_text.split(",") if k.strip()
            ]
            keywords = keywords[:max_keywords]  # Ensure max limit

            # Calculate processing time
            processing_time_ms = int((time.time() - start_time) * 1000)

            # Update usage tracking
            tokens_used = getattr(response, "usage_metadata", None)
            if tokens_used:
                total_tokens = getattr(tokens_used, "total_token_count", 0)
                self.update_rate_limit_usage(total_tokens)
            else:
                self.update_rate_limit_usage(0)

            self.logger.debug(
                f"Keywords generated: {len(keywords)} keywords, time={processing_time_ms}ms"
            )

            return self._create_success_result(
                relevance_score=1.0,  # Keywords always succeed if we get here
                confidence=0.8,  # High confidence for keyword generation
                content=keywords,  # Store keywords in content field
                tokens_used=total_tokens if tokens_used else None,
                processing_time_ms=processing_time_ms,
            )

        except Exception as e:
            self.logger.error(f"Gemini keyword generation error: {e}", exc_info=True)
            return self._create_error_result(f"Keyword generation failed: {e}")

    async def test_connection(self) -> bool:
        """Test Gemini API connection and authentication.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.logger.info("Testing Gemini connection...")

            # Simple test request
            response = await self._make_gemini_request(
                "Respond with exactly: 'Connection test successful'"
            )

            success = "successful" in response.text.lower()

            if success:
                self.logger.info("Gemini connection test successful")
            else:
                self.logger.warning(
                    "Gemini connection test failed - unexpected response"
                )

            return success

        except Exception as e:
            self.logger.error(f"Gemini connection test failed: {e}")
            return False

    def get_rate_limits(self) -> RateLimitInfo:
        """Get current rate limit information.

        Returns:
            RateLimitInfo with current usage and limits
        """
        # Update minute-based tracking
        current_time = time.time()
        if current_time - self._minute_start >= 60:
            self._request_count_minute = 0
            self._minute_start = current_time

        # Update rate limit info
        self._rate_limit_info.current_usage = self._request_count_minute

        return self._rate_limit_info

    def can_make_request(self) -> bool:
        """Check if we can make another request within rate limits.

        Returns:
            True if request can be made, False if rate limited
        """
        current_time = time.time()

        # Reset minute counter if needed
        if current_time - self._minute_start >= 60:
            self._request_count_minute = 0
            self._minute_start = current_time

        # Check per-minute rate limit
        if self._request_count_minute >= self.DEFAULT_RATE_LIMITS.requests_per_minute:
            return False

        # Check minimum time between requests (basic throttling)
        if current_time - self._last_request_time < 0.2:  # 200ms minimum for Gemini
            return False

        return True

    def update_rate_limit_usage(self, tokens_used: int = 0) -> None:
        """Update rate limit usage tracking.

        Args:
            tokens_used: Number of tokens consumed
        """
        self._request_count_minute += 1
        self._last_request_time = time.time()

        if self._rate_limit_info:
            self._rate_limit_info.current_usage = self._request_count_minute

    def set_model(self, model_name: str) -> None:
        """Switch to a different model for this provider instance.

        Args:
            model_name: New model name to use
        """
        if model_name in self.get_available_models():
            self.model_name = model_name
            # Reinitialize model with new name
            self.model = genai.GenerativeModel(
                model_name=model_name,
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                },
            )
            self.logger.info(f"Switched Gemini model to: {model_name}")
        else:
            self.logger.warning(
                f"Unknown Gemini model: {model_name}, keeping current: {self.model_name}"
            )

    async def analyze_relevance_with_model(
        self, article: Article, topic: Topic, model_name: str
    ) -> AIResult:
        """Analyze relevance with a specific model.

        Args:
            article: Article to analyze
            topic: Topic to match against
            model_name: Specific model to use for this request

        Returns:
            AIResult with relevance analysis
        """
        # Temporarily switch model
        original_model = self.model_name
        original_model_obj = self.model
        self.set_model(model_name)

        try:
            result = await self.analyze_relevance(article, topic)
            return result
        finally:
            # Restore original model
            self.model_name = original_model
            self.model = original_model_obj

    async def generate_summary_with_model(
        self, article: Article, model_name: str, max_sentences: int = 3
    ) -> AIResult:
        """Generate summary with a specific model.

        Args:
            article: Article to summarize
            model_name: Specific model to use for this request
            max_sentences: Maximum sentences in summary

        Returns:
            AIResult with generated summary
        """
        # Temporarily switch model
        original_model = self.model_name
        original_model_obj = self.model
        self.set_model(model_name)

        try:
            result = await self.generate_summary(article, max_sentences)
            return result
        finally:
            # Restore original model
            self.model_name = original_model
            self.model = original_model_obj

    async def complete(self, prompt: str) -> str:
        """Raw single-prompt completion. Returns the model's text output."""
        response = await self._make_gemini_request(prompt)
        return response.text

    async def _make_gemini_request(self, prompt: str) -> Any:
        """Make request to Gemini API.

        Args:
            prompt: Prompt text

        Returns:
            Gemini response object
        """
        # Add small delay for rate limiting
        await asyncio.sleep(0.2)

        # Configure safety settings for RSS content analysis
        # Use minimal blocking since we're analyzing legitimate news/technical content
        safety_settings = [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_ONLY_HIGH",
            },
        ]

        # Generate content asynchronously
        response = await self.model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1, max_output_tokens=500, top_p=0.9, top_k=40
            ),
            safety_settings=safety_settings,
        )

        return response

    def _handle_rate_limit_error(self, error) -> None:
        """Handle rate limit error and update internal tracking.

        Args:
            error: Rate limit error from Gemini API
        """
        self.logger.warning(f"Rate limit hit: {error}")

        # Set reset time (estimate 1 minute)
        if self._rate_limit_info:
            self._rate_limit_info.reset_time = time.time() + 60
            self._rate_limit_info.current_usage = (
                self._rate_limit_info.requests_per_minute
            )

    @staticmethod
    def get_available_models() -> List[str]:
        """Get list of available Gemini models.

        Returns:
            List of model names
        """
        return [
            "gemini-2.5-flash",  # Fast and efficient (recommended)
            "gemini-2.5-flash-lite",  # Ultra fast, lighter model
            "gemini-1.5-flash",  # Previous generation
            "gemini-1.5-pro",  # More capable but slower
        ]

    def __str__(self) -> str:
        """String representation of provider."""
        return f"GeminiProvider(model={self.model_name})"

    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"GeminiProvider(model={self.model_name}, rate_limit={self._request_count_minute}/{self.DEFAULT_RATE_LIMITS.requests_per_minute})"
