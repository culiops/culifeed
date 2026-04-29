"""
AI Manager - Multi-Provider Orchestration
=========================================

Manages multiple AI providers with intelligent fallback, load balancing,
and cost optimization for article relevance analysis and summarization.
"""

import asyncio
import time
from typing import Dict, List, Optional, Tuple, Union, Any
from dataclasses import dataclass
from enum import Enum

from .providers.base import AIProvider, AIResult, AIError, AIProviderType, RateLimitInfo
from .providers.groq_provider import GroqProvider

from .providers.gemini_provider import GeminiProvider
from .providers.openai_provider import OpenAIProvider
from .providers.deepseek_provider import DeepSeekProvider
from ..database.models import Article, Topic
from ..config.settings import (
    get_settings,
    AIProvider as ConfigAIProvider,
    ProviderPriority,
)
from ..utils.logging import get_logger_for_component
from ..utils.exceptions import ErrorCode

# Monitoring imports removed - simplified system


@dataclass
class ProviderHealth:
    """Health status of an AI provider."""

    provider_type: AIProviderType
    available: bool
    last_success: Optional[float] = None
    last_error: Optional[float] = None
    error_count: int = 0
    consecutive_errors: int = 0
    rate_limited: bool = False
    rate_limit_reset: Optional[float] = None

    @property
    def is_healthy(self) -> bool:
        """Check if provider is considered healthy."""
        if not self.available:
            return False
        if (
            self.rate_limited
            and self.rate_limit_reset
            and time.time() < self.rate_limit_reset
        ):
            return False
        return self.consecutive_errors < 3

    def record_success(self):
        """Record successful request."""
        self.last_success = time.time()
        self.consecutive_errors = 0
        self.rate_limited = False

    def record_error(self, is_rate_limit: bool = False):
        """Record failed request."""
        self.last_error = time.time()
        self.error_count += 1
        self.consecutive_errors += 1

        if is_rate_limit:
            self.rate_limited = True
            self.rate_limit_reset = time.time() + 300  # 5 minute cooldown


class FallbackStrategy(str, Enum):
    """Fallback strategies when primary provider fails."""

    NEXT_AVAILABLE = "next_available"  # Try next healthy provider
    KEYWORDS_ONLY = "keywords_only"  # Fall back to keyword matching
    FAIL_FAST = "fail_fast"  # Don't try alternatives


class AIManager:
    """Multi-provider AI manager with intelligent fallback."""

    def __init__(
        self,
        settings: Optional["CuliFeedSettings"] = None,
        primary_provider: Optional[ConfigAIProvider] = None,
    ):
        """Initialize AI manager.

        Args:
            settings: CuliFeed settings (default: load from config)
            primary_provider: Primary provider to use (default from settings)
        """
        self.settings = settings or get_settings()
        self.logger = get_logger_for_component("ai_manager")

        # Provider management
        self.providers: Dict[AIProviderType, AIProvider] = {}
        self.provider_health: Dict[AIProviderType, ProviderHealth] = {}
        self.primary_provider = primary_provider or self.settings.processing.ai_provider

        # Initialize available providers
        self._initialize_providers()

        # Fallback configuration
        self.fallback_strategy = FallbackStrategy.NEXT_AVAILABLE
        self.enable_keyword_fallback = self.settings.limits.fallback_to_keywords

        # Monitoring systems removed for simplification

        # Validate provider priority configuration
        self._validate_and_log_provider_configuration()

        self.logger.info(
            f"AI Manager initialized with primary: {self.primary_provider}, "
            f"available providers: {list(self.providers.keys())}"
        )

    def _initialize_providers(self) -> None:
        """Initialize all available AI providers."""
        # Initialize Gemini if API key available
        if self.settings.ai.gemini_api_key:
            try:
                gemini_provider = GeminiProvider(
                    api_key=self.settings.ai.gemini_api_key,
                    model_name=self.settings.ai.gemini_model,
                )
                self.providers[AIProviderType.GEMINI] = gemini_provider
                self.provider_health[AIProviderType.GEMINI] = ProviderHealth(
                    provider_type=AIProviderType.GEMINI, available=True
                )
                self.logger.info("Gemini provider initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Gemini provider: {e}")

        # Initialize Groq if API key available
        if self.settings.ai.groq_api_key:
            try:
                groq_provider = GroqProvider(
                    api_key=self.settings.ai.groq_api_key,
                    model_name=self.settings.ai.groq_model,
                )
                self.providers[AIProviderType.GROQ] = groq_provider
                self.provider_health[AIProviderType.GROQ] = ProviderHealth(
                    provider_type=AIProviderType.GROQ, available=True
                )
                self.logger.info("Groq provider initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Groq provider: {e}")

        # Initialize OpenAI if API key available
        if self.settings.ai.openai_api_key:
            try:
                openai_provider = OpenAIProvider(
                    api_key=self.settings.ai.openai_api_key,
                    model_name=self.settings.ai.openai_model,
                )
                self.providers[AIProviderType.OPENAI] = openai_provider
                self.provider_health[AIProviderType.OPENAI] = ProviderHealth(
                    provider_type=AIProviderType.OPENAI, available=True
                )
                self.logger.info("OpenAI provider initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize OpenAI provider: {e}")

        # Initialize DeepSeek if API key available
        if self.settings.ai.deepseek_api_key:
            try:
                deepseek_provider = DeepSeekProvider(
                    api_key=self.settings.ai.deepseek_api_key,
                    model_name=self.settings.ai.deepseek_model,
                )
                self.providers[AIProviderType.DEEPSEEK] = deepseek_provider
                self.provider_health[AIProviderType.DEEPSEEK] = ProviderHealth(
                    provider_type=AIProviderType.DEEPSEEK, available=True
                )
                self.logger.info("DeepSeek provider initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize DeepSeek provider: {e}")

        if not self.providers:
            self.logger.error(
                "No AI providers available! Check API keys in configuration."
            )

    def _get_provider_model_combinations(self) -> List[Tuple[str, str]]:
        """Get (provider_type, model_name) combinations in priority order.

        Returns:
            List of (provider_type, model_name) tuples for two-level fallback
        """
        combinations = []

        # Get provider priority order
        provider_order = self._get_provider_priority_order()

        for provider_type in provider_order:
            # Get models for this provider from settings
            if provider_type == AIProviderType.GROQ:
                models = self.settings.ai.get_models_for_provider(ConfigAIProvider.GROQ)
            elif provider_type == AIProviderType.GEMINI:
                models = self.settings.ai.get_models_for_provider(
                    ConfigAIProvider.GEMINI
                )
            elif provider_type == AIProviderType.OPENAI:
                models = self.settings.ai.get_models_for_provider(
                    ConfigAIProvider.OPENAI
                )
            elif provider_type == AIProviderType.DEEPSEEK:
                models = self.settings.ai.get_models_for_provider(
                    ConfigAIProvider.DEEPSEEK
                )
            else:
                models = []

            # Add all models for this provider
            for model_name in models:
                combinations.append((provider_type, model_name))

        self.logger.debug(f"Provider-model combinations: {combinations}")
        return combinations

    async def analyze_relevance(
        self,
        article: Article,
        topic: Topic,
        fallback_strategy: FallbackStrategy = None,
    ) -> AIResult:
        """Analyze article relevance with two-level fallback and validation.

        Args:
            article: Article to analyze
            topic: Topic to match against
            fallback_strategy: Override default fallback strategy


        Returns:
            AIResult with relevance analysis and validation metadata
        """
        strategy = fallback_strategy or self.fallback_strategy

        # Get provider-model combinations in priority order
        combinations = self._get_provider_model_combinations()

        last_error = None
        processing_start_time = time.time()

        for provider_type, model_name in combinations:
            provider = self.providers.get(provider_type)
            health = self.provider_health.get(provider_type)

            if not provider or not health or not health.is_healthy:
                continue

            try:
                self.logger.debug(
                    f"Analyzing relevance with {provider_type}/{model_name}"
                )

                # Use model-specific method if provider supports it
                if hasattr(provider, "analyze_relevance_with_model"):
                    result = await provider.analyze_relevance_with_model(
                        article, topic, model_name
                    )
                else:
                    # Fallback to basic analyze_relevance (single model providers)
                    result = await provider.analyze_relevance(article, topic)

                if result.success:
                    health.record_success()

                    # Apply provider quality adjustments
                    adjusted_result = self._apply_provider_quality_adjustments(
                        result, provider_type
                    )

                    self.logger.debug(
                        f"Relevance analysis successful: {provider_type}/{model_name} "
                        f"raw_score={result.relevance_score:.3f}, adjusted_score={adjusted_result.relevance_score:.3f}, "
                        f"confidence={adjusted_result.confidence:.3f}"
                    )
                    return adjusted_result
                else:
                    health.record_error()
                    last_error = AIError(
                        result.error_message or "Unknown analysis error",
                        provider=f"{provider_type}/{model_name}",
                    )

            except AIError as e:
                health.record_error(e.rate_limited)
                last_error = e
                self.logger.warning(
                    f"AI provider {provider_type}/{model_name} failed: {e.user_message}"
                )

                if strategy == FallbackStrategy.FAIL_FAST:
                    raise e

            except Exception as e:
                health.record_error()
                last_error = AIError(
                    f"Unexpected error: {e}",
                    provider=f"{provider_type}/{model_name}",
                    error_code=ErrorCode.AI_PROCESSING_ERROR,
                )
                self.logger.error(
                    f"Unexpected error with {provider_type}/{model_name}: {e}"
                )

        # All provider-model combinations failed - try keyword fallback if enabled
        if strategy == FallbackStrategy.NEXT_AVAILABLE and self.enable_keyword_fallback:
            self.logger.info(
                "All AI provider-model combinations failed, falling back to keyword matching"
            )
            fallback_result = self._keyword_fallback_analysis(article, topic)

            # Apply quality adjustment for keyword fallback too
            adjusted_fallback = self._apply_provider_quality_adjustments(
                fallback_result, "keyword_fallback"
            )

            return adjusted_fallback

        # No fallback or fallback disabled
        error_msg = f"All AI provider-model combinations failed. Last error: {last_error.user_message if last_error else 'Unknown'}"
        self.logger.error(error_msg)

        return AIResult(
            success=False, relevance_score=0.0, confidence=0.0, error_message=error_msg
        )

    async def complete(self, prompt: str) -> AIResult:
        """Provider-agnostic raw completion. Used by v2 LLMGate.

        Tries providers in priority order with the existing fallback chain.
        Returns AIResult where `content` holds the model's text output.
        """
        start_time = time.time()
        last_error: Optional[str] = None

        for provider_type, model_name in self._get_provider_model_combinations():
            provider = self.providers.get(provider_type)
            health = self.provider_health.get(provider_type)

            if not provider or not health or not health.is_healthy:
                continue

            try:
                text = await provider.complete(prompt)
                return AIResult(
                    success=True,
                    relevance_score=0.0,
                    confidence=0.0,
                    content=text,
                    provider=provider_type.value,
                    model_used=model_name,
                    processing_time_ms=int((time.time() - start_time) * 1000),
                )
            except NotImplementedError:
                continue
            except Exception as e:
                last_error = f"{provider_type.value}: {e}"
                self.logger.warning(
                    f"Provider {provider_type.value} complete() failed: {e}"
                )
                continue

        return AIResult(
            success=False,
            relevance_score=0.0,
            confidence=0.0,
            error_message=last_error or "All providers exhausted",
            processing_time_ms=int((time.time() - start_time) * 1000),
        )

    def _apply_provider_quality_adjustments(
        self, result: AIResult, provider_type: Union[AIProviderType, str]
    ) -> AIResult:
        """Apply provider quality adjustments to AI results.

        Args:
            result: Original AI result
            provider_type: Provider type or string identifier

        Returns:
            AIResult with adjusted scores based on provider quality settings
        """
        if not result.success:
            return result

        # Get provider quality factor from settings
        provider_key = (
            provider_type.value
            if hasattr(provider_type, "value")
            else str(provider_type)
        )
        quality_factor = getattr(
            self.settings.provider_quality, provider_key.lower(), 1.0
        )

        # Apply quality adjustments
        adjusted_relevance = result.relevance_score * quality_factor
        adjusted_confidence = result.confidence * quality_factor

        # Log quality adjustment if significant
        if quality_factor != 1.0:
            self.logger.debug(
                f"Applied quality adjustment for {provider_key}: "
                f"relevance {result.relevance_score:.3f} → {adjusted_relevance:.3f} "
                f"(factor: {quality_factor})"
            )

        # Create new result with adjusted scores
        adjusted_result = AIResult(
            success=result.success,
            relevance_score=adjusted_relevance,
            confidence=adjusted_confidence,
            reasoning=result.reasoning,
            provider=result.provider,
            processing_time_ms=result.processing_time_ms,
            error_message=result.error_message,
            matched_keywords=getattr(result, "matched_keywords", None),
            summary=getattr(result, "summary", None),
        )

        return adjusted_result

    async def generate_summary(
        self, article: Article, max_sentences: int = 3
    ) -> AIResult:
        """Generate article summary with two-level fallback (model + provider).

        Args:
            article: Article to summarize
            max_sentences: Maximum sentences in summary

        Returns:
            AIResult with generated summary
        """
        # Get provider-model combinations in priority order
        combinations = self._get_provider_model_combinations()

        for provider_type, model_name in combinations:
            provider = self.providers.get(provider_type)
            health = self.provider_health.get(provider_type)

            if not provider or not health or not health.is_healthy:
                continue

            try:
                self.logger.debug(
                    f"Generating summary with {provider_type}/{model_name}"
                )

                # Use model-specific method if provider supports it
                if hasattr(provider, "generate_summary_with_model"):
                    result = await provider.generate_summary_with_model(
                        article, model_name, max_sentences
                    )
                else:
                    # Fallback to basic generate_summary (single model providers)
                    result = await provider.generate_summary(article, max_sentences)

                if result.success:
                    health.record_success()
                    self.logger.debug(
                        f"Summary generation successful: {provider_type}/{model_name}"
                    )
                    return result
                else:
                    health.record_error()

            except AIError as e:
                health.record_error(e.rate_limited)
                self.logger.warning(
                    f"Summary generation failed with {provider_type}/{model_name}: {e.user_message}"
                )

            except Exception as e:
                health.record_error()
                self.logger.error(
                    f"Unexpected summary error with {provider_type}/{model_name}: {e}"
                )

        # All provider-model combinations failed - create simple fallback summary
        self.logger.warning(
            "All provider-model combinations failed for summarization, creating fallback summary"
        )
        return self._create_fallback_summary(article, max_sentences)

    async def generate_keywords(
        self, topic_name: str, context: str = "", max_keywords: int = 7
    ) -> AIResult:
        """Generate keywords for a topic with two-level fallback (model + provider).

        Args:
            topic_name: Topic name to generate keywords for
            context: Additional context (e.g., existing user topics)
            max_keywords: Maximum number of keywords to generate

        Returns:
            AIResult with generated keywords
        """
        # Get provider-model combinations in priority order
        combinations = self._get_provider_model_combinations()

        for provider_type, model_name in combinations:
            provider = self.providers.get(provider_type)
            health = self.provider_health.get(provider_type)

            if not provider or not health or not health.is_healthy:
                continue

            try:
                self.logger.debug(
                    f"Generating keywords with {provider_type}/{model_name}"
                )

                # Use model-specific method if provider supports it
                if hasattr(provider, "generate_keywords_with_model"):
                    result = await provider.generate_keywords_with_model(
                        topic_name, context, model_name, max_keywords
                    )
                elif hasattr(provider, "generate_keywords"):
                    # Fallback to basic generate_keywords (single model providers)
                    result = await provider.generate_keywords(
                        topic_name, context, max_keywords
                    )
                else:
                    # Provider doesn't support keyword generation, skip
                    continue

                if result.success:
                    health.record_success()
                    self.logger.debug(
                        f"Keyword generation successful: {provider_type}/{model_name}"
                    )
                    return result
                else:
                    health.record_error()

            except AIError as e:
                health.record_error(e.rate_limited)
                self.logger.warning(
                    f"Keyword generation failed with {provider_type}/{model_name}: {e.user_message}"
                )

            except Exception as e:
                health.record_error()
                self.logger.error(
                    f"Unexpected keyword generation error with {provider_type}/{model_name}: {e}"
                )

        # All provider-model combinations failed - create simple fallback keywords
        self.logger.warning(
            "All provider-model combinations failed for keyword generation, creating fallback keywords"
        )
        return self._create_fallback_keywords(topic_name, max_keywords)

    def _get_provider_priority_order(self) -> List[AIProviderType]:
        """Get provider priority order based on user configuration.

        Returns:
            List of provider types in priority order based on settings
        """
        # Get configured provider priority order
        config_providers = self.settings.ai.get_provider_priority_order()

        # Convert config providers to provider types and filter available
        priority_order = []
        for config_provider in config_providers:
            provider_type = self._config_to_provider_type(config_provider)
            if provider_type:
                priority_order.append(provider_type)

        # Log the configured priority for debugging
        profile = self.settings.ai.provider_priority_profile
        if profile == ProviderPriority.CUSTOM:
            self.logger.info(
                f"Using custom provider priority: {[p.value for p in config_providers]}"
            )
        else:
            self.logger.info(f"Using {profile.value} provider priority profile")

        # Filter available and healthy providers
        available_providers = []

        # Add providers in configured priority order if they're available and healthy
        for provider_type in priority_order:
            if provider_type in self.providers:
                health = self.provider_health.get(provider_type)
                if health and health.is_healthy:
                    available_providers.append(provider_type)

        # Add any remaining healthy providers not in configured order
        for provider_type, health in self.provider_health.items():
            if provider_type not in available_providers and health.is_healthy:
                available_providers.append(provider_type)

        # Add unhealthy providers as last resort (if not rate limited)
        for provider_type in priority_order:
            if (
                provider_type in self.providers
                and provider_type not in available_providers
            ):
                health = self.provider_health.get(provider_type)
                if health and not health.rate_limited:
                    available_providers.append(provider_type)

        # Log final provider order for debugging
        self.logger.debug(
            f"Final provider priority order: {[p.value for p in available_providers]}"
        )

        return available_providers

    def _config_to_provider_type(
        self, config_provider: ConfigAIProvider
    ) -> Optional[AIProviderType]:
        """Convert configuration provider to provider type.

        Args:
            config_provider: Provider from configuration

        Returns:
            Corresponding AIProviderType or None
        """
        # Direct mapping from config AIProvider enum values to AIProviderType
        if config_provider == ConfigAIProvider.GROQ:
            return AIProviderType.GROQ
        elif config_provider == ConfigAIProvider.GEMINI:
            return AIProviderType.GEMINI
        elif config_provider == ConfigAIProvider.OPENAI:
            return AIProviderType.OPENAI
        elif config_provider == ConfigAIProvider.DEEPSEEK:
            return AIProviderType.DEEPSEEK
        else:
            return None

    def _keyword_fallback_analysis(self, article: Article, topic: Topic) -> AIResult:
        """Fallback relevance analysis using keyword matching.

        Args:
            article: Article to analyze
            topic: Topic to match against

        Returns:
            AIResult with keyword-based analysis
        """
        if not topic.keywords:
            return AIResult(
                success=False,
                relevance_score=0.0,
                confidence=0.0,
                error_message="No keywords available for fallback analysis",
            )

        # Simple keyword matching
        article_text = f"{article.title} {article.content}".lower()
        matched_keywords = []
        keyword_matches = 0

        for keyword in topic.keywords:
            if keyword.lower() in article_text:
                matched_keywords.append(keyword)
                keyword_matches += 1

        # Check exclude keywords
        excluded = False
        if topic.exclude_keywords:
            for exclude_keyword in topic.exclude_keywords:
                if exclude_keyword.lower() in article_text:
                    excluded = True
                    break

        # Calculate simple relevance score
        if excluded:
            relevance_score = max(
                0.0, (keyword_matches / len(topic.keywords)) * 0.3
            )  # Penalize excluded
        else:
            relevance_score = min(
                0.8, (keyword_matches / len(topic.keywords)) * 0.7
            )  # Cap at 0.8 for keyword-only

        confidence = min(
            0.6, keyword_matches / len(topic.keywords)
        )  # Lower confidence for keyword-only

        return AIResult(
            success=True,
            relevance_score=relevance_score,
            confidence=confidence,
            matched_keywords=matched_keywords,
            reasoning=f"Keyword-based analysis: {keyword_matches}/{len(topic.keywords)} keywords matched",
            provider="keyword_fallback",
        )

    def _create_fallback_summary(
        self, article: Article, max_sentences: int
    ) -> AIResult:
        """Create simple fallback summary from article content.

        Args:
            article: Article to summarize
            max_sentences: Maximum sentences in summary

        Returns:
            AIResult with simple summary
        """
        # Simple extractive summarization - take first few sentences
        content = article.content or ""
        sentences = [s.strip() for s in content.split(".") if s.strip()]

        if not sentences:
            summary = article.title or "No content available for summary"
        else:
            # Take first N sentences up to max_sentences
            selected_sentences = sentences[:max_sentences]
            summary = ". ".join(selected_sentences)
            if not summary.endswith("."):
                summary += "."

        return AIResult(
            success=True,
            relevance_score=1.0,
            confidence=0.3,  # Low confidence for fallback summary
            summary=summary,
            provider="fallback_summary",
        )

    def _create_fallback_keywords(
        self, topic_name: str, max_keywords: int = 7
    ) -> AIResult:
        """Create fallback keywords when AI generation fails.

        Args:
            topic_name: Topic name to generate keywords for
            max_keywords: Maximum number of keywords to generate

        Returns:
            AIResult with fallback keywords
        """
        try:
            # Simple fallback strategy
            keywords = [topic_name.lower()]

            # Add some basic variations
            if " " in topic_name:
                # Multi-word topic - add individual words and technology variant
                words = topic_name.lower().split()
                keywords.extend(words[: max_keywords - 2])  # Add individual words
                keywords.append(f"{topic_name.lower()} technology")
            else:
                # Single word topic - add technology and related variants
                keywords.extend(
                    [
                        f"{topic_name.lower()} technology",
                        f"{topic_name.lower()} development",
                        f"{topic_name.lower()} tools",
                    ]
                )

            # Trim to max_keywords
            keywords = keywords[:max_keywords]

            return AIResult(
                success=True,
                relevance_score=0.5,  # Neutral score for fallback
                confidence=0.3,  # Low confidence for fallback
                content=keywords,
                processing_time_ms=1,
                provider="fallback",
            )

        except Exception as e:
            self.logger.error(f"Error creating fallback keywords: {e}")
            return AIResult(
                success=False,
                relevance_score=0.0,
                confidence=0.0,
                error_message="Failed to create fallback keywords",
            )

    async def test_all_providers(self) -> Dict[AIProviderType, bool]:
        """Test connection for all configured providers.

        Returns:
            Dictionary mapping provider types to connection status
        """
        results = {}

        for provider_type, provider in self.providers.items():
            try:
                self.logger.info(f"Testing {provider_type.value} connection...")
                success = await provider.test_connection()
                results[provider_type] = success

                if success:
                    self.provider_health[provider_type].record_success()
                    self.logger.info(f"{provider_type.value} connection test passed")
                else:
                    self.provider_health[provider_type].record_error()
                    self.logger.warning(f"{provider_type.value} connection test failed")

            except Exception as e:
                results[provider_type] = False
                self.provider_health[provider_type].record_error()
                self.logger.error(f"{provider_type.value} connection test error: {e}")

        return results

    def get_provider_status(self) -> Dict[str, Dict]:
        """Get detailed status of all providers.

        Returns:
            Dictionary with provider status information
        """
        status = {}

        for provider_type, health in self.provider_health.items():
            provider = self.providers.get(provider_type)
            rate_limits = provider.get_rate_limits() if provider else None

            status[provider_type.value] = {
                "available": health.available,
                "healthy": health.is_healthy,
                "error_count": health.error_count,
                "consecutive_errors": health.consecutive_errors,
                "rate_limited": health.rate_limited,
                "last_success": health.last_success,
                "last_error": health.last_error,
                "rate_limits": (
                    {
                        "requests_per_minute": (
                            rate_limits.requests_per_minute if rate_limits else None
                        ),
                        "current_usage": (
                            rate_limits.current_usage if rate_limits else None
                        ),
                    }
                    if rate_limits
                    else None
                ),
            }

        return status

    def reset_provider_health(self, provider_type: AIProviderType) -> None:
        """Reset health status for a specific provider.

        Args:
            provider_type: Provider to reset
        """
        if provider_type in self.provider_health:
            health = self.provider_health[provider_type]
            health.consecutive_errors = 0
            health.rate_limited = False
            health.rate_limit_reset = None
            self.logger.info(f"Reset health status for {provider_type.value}")

    def get_quality_metrics(self) -> Dict[str, Any]:
        """Get current quality metrics and trust validation status.

        Returns:
            Dictionary containing quality metrics and monitoring data
        """
        metrics = self.quality_monitor.get_current_metrics()
        recent_alerts = self.quality_monitor.get_recent_alerts(hours=24)

        return {
            "quality_metrics": {
                "validation_success_rate": metrics.validation_success_rate,
                "ai_processing_success_rate": metrics.ai_processing_success_rate,
                "silent_failure_rate": metrics.silent_failure_rate,
                "keyword_fallback_rate": metrics.keyword_fallback_rate,
                "overall_quality_score": metrics.overall_quality_score,
                "avg_score_difference": metrics.avg_score_difference,
                "avg_processing_time_ms": metrics.avg_processing_time_ms,
            },
            "provider_metrics": {
                "success_rates": dict(metrics.provider_success_rate),
                "avg_confidence": dict(metrics.provider_avg_confidence),
                "consistency": dict(metrics.provider_consistency),
            },
            "alerts": {
                "total_count": len(recent_alerts),
                "recent_alerts": [
                    {
                        "level": alert.level.value,
                        "message": alert.message,
                        "component": alert.component,
                        "timestamp": alert.timestamp.isoformat(),
                    }
                    for alert in recent_alerts[-5:]  # Last 5 alerts
                ],
            },
        }

    def generate_quality_report(self) -> Dict[str, Any]:
        """Generate comprehensive quality and trust report.

        Returns:
            Comprehensive quality report
        """
        return self.quality_monitor.generate_quality_report()

    def _validate_and_log_provider_configuration(self) -> None:
        """Validate and log provider priority configuration."""
        try:
            # Validate priority configuration
            validation_errors = self.settings.ai.validate_priority_configuration()
            if validation_errors:
                for error in validation_errors:
                    self.logger.error(f"Provider priority configuration error: {error}")
                raise ValueError(
                    f"Invalid provider priority configuration: {'; '.join(validation_errors)}"
                )

            # Log provider priority configuration
            profile = self.settings.ai.provider_priority_profile
            priority_order = self.settings.ai.get_provider_priority_order()

            self.logger.info(f"Provider priority profile: {profile.value}")
            self.logger.info(
                f"Configured provider order: {[p.value for p in priority_order]}"
            )

            # Log available vs configured providers
            available_providers = set(self.providers.keys())
            configured_provider_types = set()

            for config_provider in priority_order:
                provider_type = self._config_to_provider_type(config_provider)
                if provider_type:
                    configured_provider_types.add(provider_type)

            # Warn about configured but unavailable providers
            unavailable_configured = configured_provider_types - available_providers
            if unavailable_configured:
                self.logger.warning(
                    f"Configured providers not available (missing API keys): "
                    f"{[p.value for p in unavailable_configured]}"
                )

            # Info about available but not configured providers
            available_not_configured = available_providers - configured_provider_types
            if available_not_configured:
                self.logger.info(
                    f"Available providers not in priority order (will be added as fallback): "
                    f"{[p.value for p in available_not_configured]}"
                )

            # Log final effective priority order
            effective_order = self._get_provider_priority_order()
            self.logger.info(
                f"Effective provider priority order: {[p.value for p in effective_order]}"
            )

        except Exception as e:
            self.logger.error(f"Error validating provider configuration: {e}")
            raise

    def log_provider_selection_decision(
        self,
        selected_provider: AIProviderType,
        attempt_number: int,
        total_attempts: int,
    ) -> None:
        """Log provider selection decision for debugging.

        Args:
            selected_provider: Provider type that was selected
            attempt_number: Current attempt number (1-based)
            total_attempts: Total number of attempts available
        """
        health = self.provider_health.get(selected_provider)
        health_status = "healthy" if health and health.is_healthy else "unhealthy"

        self.logger.debug(
            f"Provider selection: attempt {attempt_number}/{total_attempts}, "
            f"selected {selected_provider.value} ({health_status})"
        )

        if health:
            self.logger.debug(
                f"Provider {selected_provider.value} stats: "
                f"errors={health.error_count}, consecutive_errors={health.consecutive_errors}, "
                f"rate_limited={health.rate_limited}"
            )

    def generate_provider_priority_report(self) -> Dict[str, Any]:
        """Generate comprehensive provider priority report for monitoring.

        Returns:
            Dictionary with provider priority analysis
        """
        profile = self.settings.ai.provider_priority_profile
        configured_order = self.settings.ai.get_provider_priority_order()
        effective_order = self._get_provider_priority_order()

        # Analyze provider availability
        provider_analysis = {}
        for config_provider in configured_order:
            provider_type = self._config_to_provider_type(config_provider)
            if provider_type:
                health = self.provider_health.get(provider_type)
                provider_analysis[config_provider.value] = {
                    "available": provider_type in self.providers,
                    "healthy": health.is_healthy if health else False,
                    "position_configured": configured_order.index(config_provider) + 1,
                    "position_effective": (
                        effective_order.index(provider_type) + 1
                        if provider_type in effective_order
                        else None
                    ),
                    "error_count": health.error_count if health else 0,
                    "rate_limited": health.rate_limited if health else False,
                }

        return {
            "priority_profile": profile.value,
            "configured_order": [p.value for p in configured_order],
            "effective_order": [p.value for p in effective_order],
            "provider_analysis": provider_analysis,
            "validation_errors": self.settings.ai.validate_priority_configuration(),
            "total_available_providers": len(self.providers),
            "total_healthy_providers": sum(
                1 for h in self.provider_health.values() if h.is_healthy
            ),
        }

    async def shutdown(self) -> None:
        """Cleanup resources and close provider connections."""
        self.logger.info("Shutting down AI Manager...")

        # Close async clients if needed
        for provider in self.providers.values():
            if hasattr(provider, "async_client") and hasattr(
                provider.async_client, "close"
            ):
                try:
                    await provider.async_client.aclose()
                except Exception as e:
                    self.logger.warning(f"Error closing provider client: {e}")

        self.logger.info("AI Manager shutdown complete")

    def __str__(self) -> str:
        """String representation."""
        return f"AIManager(primary={self.primary_provider.value}, providers={len(self.providers)})"

    def __repr__(self) -> str:
        """Detailed representation."""
        healthy_count = sum(1 for h in self.provider_health.values() if h.is_healthy)
        return (
            f"AIManager(primary={self.primary_provider.value}, "
            f"providers={len(self.providers)}, healthy={healthy_count})"
        )
