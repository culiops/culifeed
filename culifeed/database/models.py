"""
CuliFeed Data Models
===================

Pydantic data models for type safety and validation throughout the application.
These models correspond to the database schema and provide validation,
serialization, and type hints.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator, AnyHttpUrl
import json
import hashlib
import uuid


class ChatType(str, Enum):
    """Telegram chat types."""

    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class Channel(BaseModel):
    """Telegram channel/group model."""

    chat_id: str = Field(..., description="Telegram chat ID")
    chat_title: str = Field(
        ..., min_length=1, max_length=255, description="Chat display name"
    )
    chat_type: ChatType = Field(..., description="Type of Telegram chat")
    registered_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    active: bool = Field(
        default=True, description="Whether channel is active for processing"
    )
    last_delivery_at: Optional[datetime] = Field(
        default=None, description="Last successful delivery"
    )
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() if v else None}}

    def __str__(self) -> str:
        return f"Channel({self.chat_title}:{self.chat_id})"


class Article(BaseModel):
    """RSS article model with content validation."""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique article ID"
    )
    title: str = Field(..., min_length=1, max_length=1000, description="Article title")
    url: AnyHttpUrl = Field(..., description="Article URL")
    content: Optional[str] = Field(default=None, description="Cleaned article content")
    published_at: Optional[datetime] = Field(
        default=None, description="Article publication date"
    )
    source_feed: str = Field(..., min_length=1, description="Source RSS feed URL")
    content_hash: str = Field(default="", description="Content hash for deduplication")
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # AI Analysis Fields
    summary: Optional[str] = Field(default=None, description="AI-generated summary")
    ai_relevance_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="AI relevance score"
    )
    ai_confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="AI confidence score"
    )
    ai_provider: Optional[str] = Field(
        default=None, description="AI provider used for analysis"
    )
    ai_reasoning: Optional[str] = Field(
        default=None, description="AI reasoning for relevance score"
    )

    def __init__(self, **data):
        """Initialize article with auto-generated content hash."""
        if not data.get("content_hash"):
            title = data.get("title", "")
            url = str(data.get("url", ""))
            content_for_hash = f"{title}|{url}".encode("utf-8")
            data["content_hash"] = hashlib.sha256(content_for_hash).hexdigest()
        super().__init__(**data)

    @field_validator("content")
    @classmethod
    def validate_content_length(cls, v):
        """Validate content length to prevent excessive storage."""
        if v and len(v) > 50000:  # 50KB limit
            return v[:50000] + "... [truncated]"
        return v

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat() if v else None,
            AnyHttpUrl: lambda v: str(v),
        }
    }

    def __str__(self) -> str:
        return f"Article({self.title[:50]}...)"


class Topic(BaseModel):
    """User-defined topic model with keyword validation and user ownership."""

    id: Optional[int] = Field(default=None, description="Database primary key")
    chat_id: str = Field(..., description="Associated channel chat ID")
    name: str = Field(
        ..., min_length=1, max_length=200, description="Topic display name"
    )
    keywords: List[str] = Field(..., min_length=1, description="Matching keywords")
    exclude_keywords: List[str] = Field(
        default_factory=list, description="Exclusion keywords"
    )
    confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="AI confidence threshold (lowered for Phase 1)",
    )
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_match_at: Optional[datetime] = Field(
        default=None, description="Last successful match"
    )
    active: bool = Field(default=True, description="Whether topic is active")

    # NEW: User ownership tracking for SaaS pricing model
    telegram_user_id: Optional[int] = Field(
        default=None, description="Topic owner's Telegram user ID"
    )

    # v2 embedding pipeline fields
    description: Optional[str] = Field(
        default=None, description="Natural-language topic description for embedding"
    )
    embedding_signature: Optional[str] = Field(
        default=None, description="SHA256 of name|description|sorted(keywords); detects staleness"
    )
    embedding_updated_at: Optional[datetime] = Field(
        default=None, description="When the embedding was last computed"
    )

    @field_validator("keywords", "exclude_keywords")
    @classmethod
    def validate_keywords(cls, v):
        """Ensure keywords are non-empty strings and normalized."""
        if not v:
            return v

        # Clean and normalize keywords
        cleaned = []
        for keyword in v:
            if isinstance(keyword, str) and keyword.strip():
                cleaned.append(keyword.strip().lower())

        return list(set(cleaned))  # Remove duplicates

    @field_validator("name")
    @classmethod
    def validate_topic_name(cls, v):
        """Validate topic name format."""
        v = v.strip()
        if not v:
            raise ValueError("Topic name cannot be empty")
        return v

    def keywords_json(self) -> str:
        """Get keywords as JSON string for database storage."""
        return json.dumps(self.keywords)

    def exclude_keywords_json(self) -> str:
        """Get exclude keywords as JSON string for database storage."""
        return json.dumps(self.exclude_keywords)

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "Topic":
        """Create Topic from database row with JSON parsing."""
        data = dict(row)

        # Parse JSON fields
        if isinstance(data.get("keywords"), str):
            data["keywords"] = json.loads(data["keywords"])
        if isinstance(data.get("exclude_keywords"), str):
            data["exclude_keywords"] = json.loads(data["exclude_keywords"])

        return cls(**data)

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() if v else None}}

    def __str__(self) -> str:
        return f"Topic({self.name}:{len(self.keywords)} keywords)"


class Feed(BaseModel):
    """RSS feed source model."""

    id: Optional[int] = Field(default=None, description="Database primary key")
    chat_id: str = Field(..., description="Associated channel chat ID")
    url: AnyHttpUrl = Field(..., description="RSS feed URL")
    title: Optional[str] = Field(default=None, max_length=255, description="Feed title")
    description: Optional[str] = Field(
        default=None, max_length=1000, description="Feed description"
    )
    last_fetched_at: Optional[datetime] = Field(
        default=None, description="Last fetch attempt"
    )
    last_success_at: Optional[datetime] = Field(
        default=None, description="Last successful fetch"
    )
    error_count: int = Field(default=0, ge=0, description="Consecutive error count")
    active: bool = Field(default=True, description="Whether feed is active")
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("error_count")
    @classmethod
    def validate_error_count(cls, v):
        """Ensure error count doesn't exceed reasonable limits."""
        return min(v, 100)  # Cap at 100 errors

    def is_healthy(self) -> bool:
        """Check if feed is considered healthy."""
        return self.active and self.error_count < 5

    def should_disable(self) -> bool:
        """Check if feed should be automatically disabled."""
        return self.error_count >= 10

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat() if v else None,
            AnyHttpUrl: lambda v: str(v),
        }
    }

    def __str__(self) -> str:
        return f"Feed({self.title or str(self.url)})"


class ProcessingResult(BaseModel):
    """AI processing result model."""

    id: Optional[int] = Field(default=None, description="Database primary key")
    article_id: str = Field(..., description="Associated article ID")
    chat_id: str = Field(..., description="Associated channel chat ID")
    topic_name: str = Field(..., description="Matched topic name")
    pre_filter_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Pre-filtering relevance score"
    )
    ai_relevance_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="AI relevance assessment"
    )
    confidence_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="AI confidence level"
    )
    summary: Optional[str] = Field(default=None, description="AI-generated summary")
    processed_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    delivered: bool = Field(default=False, description="Whether content was delivered")
    delivery_error: Optional[str] = Field(
        default=None, description="Delivery error message"
    )

    @field_validator("summary")
    @classmethod
    def validate_summary_length(cls, v):
        """Ensure summary is appropriately sized."""
        if v and len(v) > 1000:
            return v[:1000] + "..."
        return v

    def meets_confidence_threshold(self, threshold: float) -> bool:
        """Check if result meets confidence threshold for delivery."""
        return (self.confidence_score or 0.0) >= threshold

    def is_high_quality(self) -> bool:
        """Check if result is considered high quality."""
        return (
            (self.ai_relevance_score or 0.0) >= 0.7
            and (self.confidence_score or 0.0) >= 0.8
            and self.summary is not None
        )

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() if v else None}}

    def __str__(self) -> str:
        return f"ProcessingResult({self.topic_name}:{self.confidence_score:.2f})"


class UserTier(str, Enum):
    """User subscription tiers for SaaS pricing model."""

    FREE = "free"
    PRO = "pro"


class UserSubscription(BaseModel):
    """User subscription model for SaaS billing and limits."""

    telegram_user_id: int = Field(..., description="Telegram user ID")
    subscription_tier: UserTier = Field(
        default=UserTier.FREE, description="User's subscription tier"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Subscription creation date",
    )

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() if v else None}}

    def __str__(self) -> str:
        return f"UserSubscription({self.telegram_user_id}:{self.subscription_tier})"


@dataclass
class ProcessingStats:
    """Processing statistics for monitoring."""

    total_articles: int = 0
    pre_filtered_articles: int = 0
    ai_processed_articles: int = 0
    delivered_articles: int = 0
    processing_time_seconds: float = 0.0
    api_calls_used: int = 0
    estimated_cost: float = 0.0
    channels_processed: int = 0
    topics_matched: int = 0

    @property
    def pre_filter_reduction_percent(self) -> float:
        """Calculate pre-filtering reduction percentage."""
        if self.total_articles == 0:
            return 0.0
        return (1 - self.pre_filtered_articles / self.total_articles) * 100

    @property
    def delivery_success_rate(self) -> float:
        """Calculate delivery success rate."""
        if self.ai_processed_articles == 0:
            return 0.0
        return (self.delivered_articles / self.ai_processed_articles) * 100


# Type aliases for common data structures
ChannelDict = Dict[str, Any]
ArticleDict = Dict[str, Any]
TopicDict = Dict[str, Any]
FeedDict = Dict[str, Any]
ProcessingResultDict = Dict[str, Any]
