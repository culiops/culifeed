"""
CuliFeed Configuration System
============================

Simple configuration management with environment variables and Pydantic models.
Environment variables override Field defaults with clear precedence.
"""

import os
from pathlib import Path
from typing import List, Dict, Optional, Any
from enum import Enum

from pydantic import BaseModel, Field, field_validator, AnyHttpUrl
from pydantic_settings import BaseSettings

from ..utils.exceptions import ConfigurationError, ErrorCode


class AIProvider(str, Enum):
    """Available AI providers."""

    GEMINI = "gemini"
    GROQ = "groq"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"


class ProviderPriority(str, Enum):
    """Predefined provider priority profiles."""

    COST_OPTIMIZED = "cost_optimized"  # Free tiers first
    QUALITY_FIRST = "quality_first"  # Premium models first
    BALANCED = "balanced"  # Mix of cost and quality
    CUSTOM = "custom"  # User-defined order


class LogLevel(str, Enum):
    """Available log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ProcessingSettings(BaseModel):
    """Processing pipeline configuration."""

    daily_run_hour: int = Field(
        default=8, ge=0, le=23, description="Hour of day to run processing (0-23)"
    )
    max_articles_per_topic: int = Field(
        default=5, ge=1, le=20, description="Maximum articles to deliver per topic"
    )
    ai_provider: AIProvider = Field(
        default=AIProvider.GROQ, description="Primary AI provider"
    )
    batch_size: int = Field(
        default=10, ge=1, le=50, description="Articles to process in one batch"
    )
    parallel_feeds: int = Field(
        default=5, ge=1, le=20, description="Concurrent feed fetches"
    )
    cache_embeddings: bool = Field(default=True, description="Cache article embeddings")
    max_content_length: int = Field(
        default=2000,
        ge=500,
        le=10000,
        description="Max content length for AI processing",
    )
    ai_relevance_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum AI relevance score to include article",
    )
    ai_summary_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum AI relevance score to generate summary",
    )

    @field_validator("daily_run_hour")
    @classmethod
    def validate_hour(cls, v):
        """Ensure hour is valid."""
        if not (0 <= v <= 23):
            raise ValueError("daily_run_hour must be between 0 and 23")
        return v


# Trust validation settings removed for simplification


class ProviderQualitySettings(BaseModel):
    """AI provider quality factors for confidence adjustment."""

    groq: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Premium quality provider"
    )
    gemini: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Premium quality provider"
    )
    openai: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Premium quality provider"
    )
    deepseek: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Premium quality provider with advanced reasoning",
    )
    keyword_backup: float = Field(
        default=0.45, ge=0.0, le=1.0, description="Basic keyword matching"
    )
    keyword_fallback: float = Field(
        default=0.45, ge=0.0, le=1.0, description="Basic keyword matching"
    )


# Quality monitoring settings removed for simplification


class FilteringSettings(BaseModel):
    """Pre-filtering and processing threshold configuration."""

    # Pre-filter thresholds
    min_relevance_threshold: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score to pass pre-filtering",
    )

    # Phrase matching weights
    exact_phrase_weight: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Weight for exact phrase matches in keyword scoring",
    )

    partial_word_weight: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Weight for partial word matches in multi-word keywords",
    )

    single_word_tf_cap: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Maximum score cap for single word TF (term frequency) scores",
    )

    keyword_match_bonus: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Bonus multiplier for multiple keyword matches",
    )

    # Processing thresholds

    fallback_relevance_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score for keyword fallback processing",
    )

    fallback_confidence_cap: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Maximum confidence score cap for hybrid fallback results",
    )

    # Quality scoring weights for articles
    title_quality_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight of title quality in overall article quality score",
    )

    content_quality_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Weight of content quality in overall article quality score",
    )

    recency_weight: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Weight of publication recency in overall article quality score",
    )

    url_quality_weight: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Weight of URL quality in overall article quality score",
    )

    # Embedding pipeline (v2)
    embedding_provider: str = Field(
        default="openai",
        description="Provider for article and topic embeddings",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model name",
    )
    embedding_min_score: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for embedding stage to assign a topic",
    )
    embedding_fallback_threshold: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Threshold for delivering on embedding score alone if LLM gate fails",
    )
    embedding_retention_days: int = Field(
        default=90,
        ge=1,
        description="Days to retain article embeddings before pruning",
    )
    use_embedding_pipeline: bool = Field(
        default=False,
        description="Feature flag: use the v2 embedding pipeline",
    )


class SmartProcessingSettings(BaseModel):
    """Smart processing configuration for confidence-based routing."""

    enabled: bool = Field(
        default=True, description="Enable smart pre-filtering with confidence scoring"
    )

    # Confidence-based routing thresholds
    high_confidence_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Route directly without AI if confidence >= this value",
    )

    low_confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Route to keyword fallback if confidence >= this and score low",
    )

    # Routing decision thresholds
    definitely_relevant_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Score threshold for 'definitely relevant' routing",
    )

    definitely_irrelevant_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Score threshold for 'definitely irrelevant' routing",
    )

    # Performance and caching
    similarity_cache_enabled: bool = Field(
        default=True, description="Enable basic content similarity caching"
    )

    max_cache_entries: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Maximum entries in similarity cache",
    )

    # Cost optimization settings
    ai_skip_rate_target: float = Field(
        default=0.4,
        ge=0.0,
        le=0.8,
        description="Target AI request reduction through smart routing (40%)",
    )

    quality_assurance_sample_rate: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Sample rate for quality validation of skipped articles (10%)",
    )

    # Generic pattern classification settings
    generic_patterns_enabled: bool = Field(
        default=True,
        description="Enable generic pattern classification for semantic penalties",
    )

    generic_patterns: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "update_feature": [
                "new feature",
                "new features",
                "latest feature",
                "latest features",
                "new update",
                "new updates",
                "latest update",
                "latest updates",
                "recent update",
                "recent updates",
                "update",
                "updates",
                "feature update",
                "feature updates",
                "new release",
                "new releases",
                "latest release",
                "recent release",
                "version update",
                "upgrade",
                "enhancement",
                "enhancements",
                "improvement",
                "improvements",
            ],
            "guide_tutorial": [
                "best practices",
                "best practice",
                "practices",
                "tutorial",
                "guide",
                "documentation",
                "announcement",
                "announcements",
                "how to",
                "getting started",
                "quick start",
                "overview",
                "introduction",
                "tips",
                "tips and tricks",
                "tutorial guide",
                "step by step",
                "walkthrough",
                "handbook",
                "reference",
                "cheat sheet",
            ],
            "general_tech": [
                "development",
                "coding",
                "programming",
                "algorithm",
                "software",
                "application",
                "mobile app",
                "web app",
                "app development",
                "technology",
                "tech",
                "digital",
                "innovation",
                "solution",
                "solutions",
                "framework",
                "library",
                "tool",
                "tools",
                "methodology",
                "approach",
                "strategy",
                "implementation",
                "architecture",
                "design",
                "pattern",
                "patterns",
            ],
            "cloud_aws": [
                "aws",
                "amazon",
                "cloud computing",
                "cloud",
                "cloud service",
                "service",
                "platform",
                "infrastructure",
                "deployment",
                "hosting",
                "server",
                "serverless",
                "microservices",
                "devops",
                "ci cd",
                "automation",
                "monitoring",
                "logging",
                "security",
                "performance",
                "scalability",
                "reliability",
            ],
            "business_industry": [
                "enterprise",
                "business",
                "industry",
                "market",
                "trends",
                "analysis",
                "report",
                "survey",
                "study",
                "research",
                "insights",
                "data",
                "analytics",
                "metrics",
                "kpi",
                "roi",
                "cost",
                "pricing",
                "budget",
                "optimization",
            ],
            "time_frequency": [
                "daily",
                "weekly",
                "monthly",
                "quarterly",
                "annual",
                "regular",
                "periodic",
                "scheduled",
                "routine",
                "ongoing",
                "continuous",
                "real time",
                "instant",
                "immediate",
            ],
            "quality_status": [
                "quality",
                "testing",
                "bug",
                "bugs",
                "issue",
                "issues",
                "problem",
                "problems",
                "fix",
                "fixes",
                "patch",
                "patches",
                "stable",
                "beta",
                "alpha",
                "production",
                "staging",
                "maintenance",
                "support",
                "help",
                "troubleshooting",
            ],
            "descriptors": [
                "new",
                "latest",
                "recent",
                "modern",
                "advanced",
                "simple",
                "easy",
                "quick",
                "fast",
                "efficient",
                "powerful",
                "flexible",
                "comprehensive",
                "complete",
                "full",
                "basic",
                "essential",
                "popular",
                "trending",
                "top",
                "best",
                "recommended",
            ],
            "actions": [
                "learn",
                "build",
                "create",
                "develop",
                "deploy",
                "manage",
                "configure",
                "setup",
                "install",
                "migrate",
                "integrate",
                "optimize",
                "scale",
                "monitor",
                "secure",
                "backup",
                "restore",
                "troubleshoot",
                "debug",
                "test",
                "validate",
            ],
        },
        description="Categorized generic patterns for semantic penalty classification",
    )


class DeliveryQualitySettings(BaseModel):
    """Message delivery quality thresholds and formatting configuration."""

    # Quality indicator thresholds
    high_quality_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for high quality articles",
    )

    good_quality_threshold: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for good quality articles",
    )

    moderate_quality_threshold: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for moderate quality articles",
    )

    low_quality_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for low quality articles (anything above)",
    )

    # Content length limits - SINGLE SOURCE OF TRUTH
    max_summary_length: int = Field(
        default=700,
        ge=100,
        le=2000,
        description="Maximum length for both AI summaries and content previews (prevents message overflow)",
    )

    # Reading time estimation
    reading_time_per_article: float = Field(
        default=0.5,
        ge=0.1,
        le=5.0,
        description="Estimated reading time per article in minutes",
    )

    min_reading_time: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Minimum reading time to display in minutes",
    )

    # Message formatting
    message_delay_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=5.0,
        description="Delay between sending multiple messages to avoid rate limiting",
    )

    content_break_threshold: float = Field(
        default=0.7,
        ge=0.1,
        le=1.0,
        description="Ratio threshold for smart content breaking at sentence boundaries",
    )


class LimitsSettings(BaseModel):
    """Cost control and rate limiting settings."""

    max_daily_api_calls: int = Field(
        default=950, ge=10, description="Maximum AI API calls per day"
    )
    fallback_to_groq: bool = Field(
        default=True, description="Use Groq when primary API exhausted"
    )
    fallback_to_keywords: bool = Field(
        default=True, description="Use keyword-only when all APIs exhausted"
    )
    enable_usage_alerts: bool = Field(
        default=True, description="Enable usage monitoring alerts"
    )
    alert_threshold: float = Field(
        default=0.8, ge=0.1, le=1.0, description="Alert when usage exceeds threshold"
    )
    max_feed_errors: int = Field(
        default=10, ge=1, le=100, description="Max errors before disabling feed"
    )
    request_timeout: int = Field(
        default=30, ge=5, le=300, description="Request timeout in seconds"
    )


class DatabaseSettings(BaseModel):
    """Database configuration."""

    path: str = Field(
        default="data/culifeed.db", description="SQLite database file path"
    )
    pool_size: int = Field(default=5, ge=1, le=20, description="Connection pool size")
    cleanup_days: int = Field(
        default=7, ge=1, le=365, description="Days to keep old articles"
    )
    auto_vacuum: bool = Field(
        default=True, description="Enable automatic database maintenance"
    )
    backup_enabled: bool = Field(default=True, description="Enable automatic backups")
    backup_interval_hours: int = Field(
        default=24, ge=1, le=168, description="Hours between backups"
    )


class LoggingSettings(BaseModel):
    """Logging configuration."""

    level: LogLevel = Field(default=LogLevel.INFO, description="Global log level")
    file_path: Optional[str] = Field(
        default="logs/culifeed.log", description="Log file path"
    )
    max_file_size_mb: int = Field(
        default=10, ge=1, le=100, description="Max log file size in MB"
    )
    backup_count: int = Field(
        default=5, ge=1, le=20, description="Number of log backup files"
    )
    structured_logging: bool = Field(
        default=False, description="Use structured JSON logging"
    )
    console_logging: bool = Field(default=True, description="Enable console logging")


class TelegramSettings(BaseModel):
    """Telegram bot configuration."""

    bot_token: str = Field(..., description="Telegram bot token")
    admin_user_id: Optional[str] = Field(
        default=None, description="Admin user ID for management commands"
    )
    webhook_url: Optional[AnyHttpUrl] = Field(
        default=None, description="Webhook URL for updates"
    )
    webhook_secret: Optional[str] = Field(
        default=None, description="Webhook secret token"
    )
    max_retries: int = Field(
        default=3, ge=1, le=10, description="Max retries for failed messages"
    )

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, v):
        """Validate bot token format."""
        if not v or not isinstance(v, str):
            raise ValueError("Bot token is required")

        # Basic format check for Telegram bot tokens
        # Allow test tokens for development
        if v.endswith("_test"):
            return v

        if not v.count(":") == 1 or len(v) < 20:
            raise ValueError("Invalid bot token format")

        return v


class AISettings(BaseModel):
    """AI providers configuration."""

    gemini_api_key: Optional[str] = Field(
        default=None, description="Google Gemini API key"
    )
    groq_api_key: Optional[str] = Field(default=None, description="Groq API key")
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key")
    deepseek_api_key: Optional[str] = Field(
        default=None, description="DeepSeek API key"
    )

    # Multi-model configuration for fallback
    gemini_models: List[str] = Field(
        default=["gemini-1.5-flash"], description="Gemini models in priority order"
    )
    groq_models: List[str] = Field(
        default=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        description="Groq models in priority order",
    )
    openai_models: List[str] = Field(
        default=["gpt-4o-mini"], description="OpenAI models in priority order"
    )
    deepseek_models: List[str] = Field(
        default=["deepseek-chat", "deepseek-reasoner"],
        description="DeepSeek models in priority order",
    )

    # Legacy single model fields (for backward compatibility)
    gemini_model: str = Field(
        default="gemini-1.5-flash", description="Primary Gemini model"
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile", description="Primary Groq model"
    )
    openai_model: str = Field(default="gpt-4o-mini", description="Primary OpenAI model")
    deepseek_model: str = Field(
        default="deepseek-chat", description="Primary DeepSeek model"
    )

    temperature: float = Field(
        default=0.1, ge=0.0, le=2.0, description="AI temperature setting"
    )
    max_tokens: int = Field(
        default=500, ge=50, le=2000, description="Maximum tokens per response"
    )

    # Provider Priority Configuration
    provider_priority_profile: ProviderPriority = Field(
        default=ProviderPriority.COST_OPTIMIZED,
        description="Provider priority strategy: cost_optimized, quality_first, balanced, or custom",
    )
    custom_provider_order: List[AIProvider] = Field(
        default_factory=list,
        description="Custom provider priority order (used when profile is 'custom')",
    )

    def get_primary_api_key(self, provider: AIProvider) -> Optional[str]:
        """Get API key for specified provider."""
        if provider == AIProvider.GEMINI:
            return self.gemini_api_key
        elif provider == AIProvider.GROQ:
            return self.groq_api_key
        elif provider == AIProvider.OPENAI:
            return self.openai_api_key
        elif provider == AIProvider.DEEPSEEK:
            return self.deepseek_api_key
        return None

    def get_models_for_provider(self, provider: AIProvider) -> List[str]:
        """Get model list for specified provider in priority order."""
        if provider == AIProvider.GEMINI:
            return self.gemini_models
        elif provider == AIProvider.GROQ:
            return self.groq_models
        elif provider == AIProvider.OPENAI:
            return self.openai_models
        elif provider == AIProvider.DEEPSEEK:
            return self.deepseek_models
        return []

    def validate_provider_key(self, provider: AIProvider) -> bool:
        """Check if API key is available for provider."""
        return bool(self.get_primary_api_key(provider))

    def get_provider_priority_order(self) -> List[AIProvider]:
        """Get provider priority order based on configuration.

        Returns:
            List of providers in priority order based on profile
        """
        if self.provider_priority_profile == ProviderPriority.CUSTOM:
            if self.custom_provider_order:
                return list(self.custom_provider_order)
            else:
                # Fallback to cost optimized if custom is empty
                return [
                    AIProvider.GROQ,
                    AIProvider.DEEPSEEK,
                    AIProvider.GEMINI,
                    AIProvider.OPENAI,
                ]

        elif self.provider_priority_profile == ProviderPriority.QUALITY_FIRST:
            return [
                AIProvider.DEEPSEEK,
                AIProvider.OPENAI,
                AIProvider.GEMINI,
                AIProvider.GROQ,
            ]

        elif self.provider_priority_profile == ProviderPriority.BALANCED:
            return [
                AIProvider.DEEPSEEK,
                AIProvider.GEMINI,
                AIProvider.GROQ,
                AIProvider.OPENAI,
            ]

        else:  # COST_OPTIMIZED (default)
            return [
                AIProvider.GROQ,
                AIProvider.DEEPSEEK,
                AIProvider.GEMINI,
                AIProvider.OPENAI,
            ]

    def validate_priority_configuration(self) -> List[str]:
        """Validate provider priority configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if self.provider_priority_profile == ProviderPriority.CUSTOM:
            if not self.custom_provider_order:
                errors.append(
                    "Custom provider order is empty when using custom profile"
                )
            else:
                # Check for duplicates
                if len(self.custom_provider_order) != len(
                    set(self.custom_provider_order)
                ):
                    errors.append("Duplicate providers found in custom_provider_order")

                # Check for invalid providers
                for provider in self.custom_provider_order:
                    if provider not in AIProvider:
                        errors.append(f"Invalid provider in custom order: {provider}")

        return errors


class SaaSSettings(BaseModel):
    """SaaS mode configuration settings."""

    saas_mode: bool = Field(default=False, description="Enable SaaS business logic")
    free_tier_topic_limit_per_user: int = Field(
        default=5, ge=1, le=100, description="Free tier topic limit per user"
    )


class UserSettings(BaseModel):
    """User preferences."""

    timezone: str = Field(default="UTC", description="User timezone")
    admin_user_id: Optional[str] = Field(default=None, description="Admin user ID")


class CuliFeedSettings(BaseSettings):
    """Main application settings."""

    # Core settings sections
    user: UserSettings = Field(default_factory=UserSettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    telegram: TelegramSettings
    ai: AISettings = Field(default_factory=AISettings)

    # NEW: SaaS pricing and billing settings
    saas: SaaSSettings = Field(default_factory=SaaSSettings)

    # Advanced configuration sections
    provider_quality: ProviderQualitySettings = Field(
        default_factory=ProviderQualitySettings
    )
    filtering: FilteringSettings = Field(default_factory=FilteringSettings)
    smart_processing: SmartProcessingSettings = Field(
        default_factory=SmartProcessingSettings
    )
    delivery_quality: DeliveryQualitySettings = Field(
        default_factory=DeliveryQualitySettings
    )

    # Application metadata
    app_name: str = Field(default="CuliFeed", description="Application name")
    version: str = Field(default="1.0.0", description="Application version")
    debug: bool = Field(default=False, description="Enable debug mode")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "env_nested_delimiter": "__",
        "env_prefix": "CULIFEED_",
    }

    def validate_configuration(self) -> None:
        """Validate complete configuration."""
        errors = []

        # Validate AI provider setup
        primary_provider = self.processing.ai_provider
        if not self.ai.validate_provider_key(primary_provider):
            errors.append(
                f"Missing API key for primary AI provider: {primary_provider}"
            )

        # Validate database path
        try:
            db_path = Path(self.database.path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Invalid database path: {e}")

        # Validate log path if specified
        if self.logging.file_path:
            try:
                log_path = Path(self.logging.file_path)
                log_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Invalid log file path: {e}")

        if errors:
            raise ConfigurationError(
                f"Configuration validation failed: {'; '.join(errors)}",
                error_code=ErrorCode.CONFIG_INVALID,
            )

    def get_ai_fallback_providers(self) -> List[AIProvider]:
        """Get list of available fallback AI providers."""
        providers = []

        # Always try primary provider first
        if self.ai.validate_provider_key(self.processing.ai_provider):
            providers.append(self.processing.ai_provider)

        # Add other available providers as fallbacks
        for provider in AIProvider:
            if (
                provider != self.processing.ai_provider
                and self.ai.validate_provider_key(provider)
            ):
                providers.append(provider)

        return providers

    def is_production_mode(self) -> bool:
        """Check if running in production mode."""
        return (
            not self.debug and os.getenv("ENV", "development").lower() == "production"
        )

    def get_effective_log_level(self) -> str:
        """Get effective log level considering debug mode."""
        if self.debug:
            return "DEBUG"
        return self.logging.level.value


def load_settings(config_path: Optional[str] = None) -> CuliFeedSettings:
    """Load settings from environment variables and defaults.

    Simple approach: Environment variables override Pydantic Field defaults.

    Returns:
        Loaded and validated settings

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Load environment variables from .env file
    from dotenv import load_dotenv

    load_dotenv()

    try:
        # Create settings - Pydantic automatically handles:
        # 1. Environment variables (highest precedence)
        # 2. .env file values
        # 3. Field defaults (lowest precedence)
        settings = CuliFeedSettings()

        # Validate the complete configuration
        settings.validate_configuration()

        return settings

    except Exception as e:
        if isinstance(e, ConfigurationError):
            raise
        raise ConfigurationError(
            f"Failed to initialize settings: {e}", error_code=ErrorCode.CONFIG_INVALID
        )


# Removed unused YAML processing functions to keep it simple


# Removed create_example_config() - not needed for environment-variable-only approach


# Global settings instance
_settings: Optional[CuliFeedSettings] = None


def get_settings(reload: bool = False) -> CuliFeedSettings:
    """Get global settings instance (singleton pattern).

    Args:
        reload: Force reload of settings

    Returns:
        Global settings instance
    """
    global _settings

    if _settings is None or reload:
        _settings = load_settings()

    return _settings
