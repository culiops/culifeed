"""
Database Models Test Suite
=========================

Comprehensive tests for Pydantic data models.
"""

import pytest
import json
from datetime import datetime, timezone
from pydantic import ValidationError

from culifeed.database.models import (
    Channel,
    ChatType,
    Article,
    Topic,
    Feed,
    ProcessingResult,
    ProcessingStats,
)


class TestChannel:
    """Test Channel model validation and functionality."""

    def test_channel_creation_valid(self):
        """Test creating a valid channel."""
        channel = Channel(
            chat_id="-1001234567890",
            chat_title="Test Group",
            chat_type=ChatType.SUPERGROUP,
        )

        assert channel.chat_id == "-1001234567890"
        assert channel.chat_title == "Test Group"
        assert channel.chat_type == ChatType.SUPERGROUP
        assert channel.active is True
        assert isinstance(channel.created_at, datetime)

    def test_channel_str_representation(self):
        """Test channel string representation."""
        channel = Channel(
            chat_id="-1001234567890",
            chat_title="Test Group",
            chat_type=ChatType.SUPERGROUP,
        )

        expected = "Channel(Test Group:-1001234567890)"
        assert str(channel) == expected

    def test_channel_title_validation(self):
        """Test channel title validation."""
        # Test empty title
        with pytest.raises(ValidationError):
            Channel(chat_id="-1001234567890", chat_title="", chat_type=ChatType.GROUP)

        # Test title too long
        long_title = "x" * 256
        with pytest.raises(ValidationError):
            Channel(
                chat_id="-1001234567890",
                chat_title=long_title,
                chat_type=ChatType.GROUP,
            )


class TestArticle:
    """Test Article model validation and functionality."""

    def test_article_creation_valid(self):
        """Test creating a valid article."""
        article = Article(
            title="Test Article",
            url="https://example.com/test",
            source_feed="https://example.com/feed.xml",
        )

        assert article.title == "Test Article"
        assert str(article.url) == "https://example.com/test"
        assert article.source_feed == "https://example.com/feed.xml"
        assert article.content_hash != ""
        assert isinstance(article.created_at, datetime)

    def test_article_content_hash_generation(self):
        """Test automatic content hash generation."""
        article1 = Article(
            title="Test Article",
            url="https://example.com/test",
            source_feed="https://example.com/feed.xml",
        )

        article2 = Article(
            title="Test Article",
            url="https://example.com/test",
            source_feed="https://example.com/feed.xml",
        )

        # Same title and URL should have same hash
        assert article1.content_hash == article2.content_hash

        article3 = Article(
            title="Different Article",
            url="https://example.com/test",
            source_feed="https://example.com/feed.xml",
        )

        # Different title should have different hash
        assert article1.content_hash != article3.content_hash

    def test_article_content_validation(self):
        """Test article content length validation."""
        # Create article with long content
        long_content = "x" * 60000  # Exceeds 50KB limit

        article = Article(
            title="Test Article",
            url="https://example.com/test",
            content=long_content,
            source_feed="https://example.com/feed.xml",
        )

        # Content should be truncated
        assert len(article.content) <= 50020  # 50000 + "... [truncated]"
        assert article.content.endswith("... [truncated]")

    def test_article_str_representation(self):
        """Test article string representation."""
        article = Article(
            title="This is a very long article title that should be truncated",
            url="https://example.com/test",
            source_feed="https://example.com/feed.xml",
        )

        str_repr = str(article)
        assert str_repr.startswith("Article(")
        assert len(str_repr) <= 65  # Should be truncated

    def test_article_url_validation(self):
        """Test article URL validation."""
        # Test invalid URL
        with pytest.raises(ValidationError):
            Article(
                title="Test Article",
                url="not-a-url",
                source_feed="https://example.com/feed.xml",
            )

        # Test valid URL
        article = Article(
            title="Test Article",
            url="https://example.com/valid-url",
            source_feed="https://example.com/feed.xml",
        )
        assert str(article.url) == "https://example.com/valid-url"


class TestTopic:
    """Test Topic model validation and functionality."""

    def test_topic_creation_valid(self):
        """Test creating a valid topic."""
        topic = Topic(
            chat_id="-1001234567890",
            name="Programming",
            keywords=["python", "javascript", "programming"],
        )

        assert topic.chat_id == "-1001234567890"
        assert topic.name == "Programming"
        assert "python" in topic.keywords
        assert topic.active is True
        assert topic.confidence_threshold == 0.6

    def test_topic_keyword_validation(self):
        """Test topic keyword validation and normalization."""
        topic = Topic(
            chat_id="-1001234567890",
            name="Programming",
            keywords=[
                "  Python  ",
                "JAVASCRIPT",
                "programming",
                "python",
            ],  # Duplicates and formatting
        )

        # Keywords should be normalized and deduplicated
        assert "python" in topic.keywords
        assert "javascript" in topic.keywords
        assert "programming" in topic.keywords
        assert len(topic.keywords) == 3  # Duplicates removed

    def test_topic_exclude_keywords(self):
        """Test topic exclude keywords functionality."""
        topic = Topic(
            chat_id="-1001234567890",
            name="Programming",
            keywords=["python", "programming"],
            exclude_keywords=["beginner", "tutorial"],
        )

        assert "beginner" in topic.exclude_keywords
        assert "tutorial" in topic.exclude_keywords

    def test_topic_name_validation(self):
        """Test topic name validation."""
        # Test empty name
        with pytest.raises(ValidationError):
            Topic(chat_id="-1001234567890", name="   ", keywords=["python"])

        # Test name too long
        long_name = "x" * 201
        with pytest.raises(ValidationError):
            Topic(chat_id="-1001234567890", name=long_name, keywords=["python"])

    def test_topic_keywords_json_methods(self):
        """Test JSON serialization methods."""
        topic = Topic(
            chat_id="-1001234567890",
            name="Programming",
            keywords=["python", "javascript"],
            exclude_keywords=["beginner"],
        )

        # Test keywords JSON serialization
        keywords_json = topic.keywords_json()
        assert isinstance(keywords_json, str)
        parsed_keywords = json.loads(keywords_json)
        assert "python" in parsed_keywords

        # Test exclude keywords JSON serialization
        exclude_json = topic.exclude_keywords_json()
        assert isinstance(exclude_json, str)
        parsed_exclude = json.loads(exclude_json)
        assert "beginner" in parsed_exclude

    def test_topic_from_db_row(self):
        """Test creating topic from database row."""
        db_row = {
            "id": 1,
            "chat_id": "-1001234567890",
            "name": "Programming",
            "keywords": '["python", "javascript"]',
            "exclude_keywords": '["beginner"]',
            "confidence_threshold": 0.8,
            "active": True,
            "created_at": datetime.now(timezone.utc),
            "last_match_at": None,
        }

        topic = Topic.from_db_row(db_row)

        assert topic.id == 1
        assert topic.name == "Programming"
        assert "python" in topic.keywords
        assert "beginner" in topic.exclude_keywords

    def test_topic_str_representation(self):
        """Test topic string representation."""
        topic = Topic(
            chat_id="-1001234567890",
            name="Programming",
            keywords=["python", "javascript", "go"],
        )

        str_repr = str(topic)
        assert "Programming:3 keywords" in str_repr


class TestFeed:
    """Test Feed model validation and functionality."""

    def test_feed_creation_valid(self):
        """Test creating a valid feed."""
        feed = Feed(
            chat_id="-1001234567890",
            url="https://example.com/feed.xml",
            title="Test Feed",
        )

        assert feed.chat_id == "-1001234567890"
        assert str(feed.url) == "https://example.com/feed.xml"
        assert feed.title == "Test Feed"
        assert feed.active is True
        assert feed.error_count == 0

    def test_feed_health_methods(self):
        """Test feed health checking methods."""
        # Healthy feed
        healthy_feed = Feed(
            chat_id="-1001234567890",
            url="https://example.com/feed.xml",
            title="Healthy Feed",
            error_count=2,
        )

        assert healthy_feed.is_healthy() is True
        assert healthy_feed.should_disable() is False

        # Unhealthy feed
        unhealthy_feed = Feed(
            chat_id="-1001234567890",
            url="https://example.com/feed.xml",
            title="Unhealthy Feed",
            error_count=7,
        )

        assert unhealthy_feed.is_healthy() is False
        assert unhealthy_feed.should_disable() is False

        # Feed that should be disabled
        failed_feed = Feed(
            chat_id="-1001234567890",
            url="https://example.com/feed.xml",
            title="Failed Feed",
            error_count=15,
        )

        assert failed_feed.is_healthy() is False
        assert failed_feed.should_disable() is True

    def test_feed_error_count_validation(self):
        """Test feed error count validation."""
        feed = Feed(
            chat_id="-1001234567890",
            url="https://example.com/feed.xml",
            title="Test Feed",
            error_count=150,  # Exceeds limit
        )

        # Error count should be capped at 100
        assert feed.error_count == 100

    def test_feed_str_representation(self):
        """Test feed string representation."""
        # Feed with title
        feed_with_title = Feed(
            chat_id="-1001234567890",
            url="https://example.com/feed.xml",
            title="Tech News",
        )

        assert str(feed_with_title) == "Feed(Tech News)"

        # Feed without title
        feed_without_title = Feed(
            chat_id="-1001234567890", url="https://example.com/feed.xml"
        )

        assert "https://example.com/feed.xml" in str(feed_without_title)


class TestProcessingResult:
    """Test ProcessingResult model validation and functionality."""

    def test_processing_result_creation(self):
        """Test creating a valid processing result."""
        result = ProcessingResult(
            article_id="test-article-id",
            chat_id="-1001234567890",
            topic_name="Programming",
            ai_relevance_score=0.8,
            confidence_score=0.9,
            summary="Test summary",
        )

        assert result.article_id == "test-article-id"
        assert result.topic_name == "Programming"
        assert result.ai_relevance_score == 0.8
        assert result.confidence_score == 0.9
        assert result.delivered is False

    def test_processing_result_confidence_threshold(self):
        """Test confidence threshold checking."""
        result = ProcessingResult(
            article_id="test-article-id",
            chat_id="-1001234567890",
            topic_name="Programming",
            confidence_score=0.85,
        )

        assert result.meets_confidence_threshold(0.8) is True
        assert result.meets_confidence_threshold(0.9) is False

    def test_processing_result_quality_check(self):
        """Test high quality checking."""
        # High quality result
        high_quality = ProcessingResult(
            article_id="test-article-id",
            chat_id="-1001234567890",
            topic_name="Programming",
            ai_relevance_score=0.8,
            confidence_score=0.9,
            summary="Detailed summary",
        )

        assert high_quality.is_high_quality() is True

        # Low quality result
        low_quality = ProcessingResult(
            article_id="test-article-id",
            chat_id="-1001234567890",
            topic_name="Programming",
            ai_relevance_score=0.5,
            confidence_score=0.6,
        )

        assert low_quality.is_high_quality() is False

    def test_processing_result_summary_validation(self):
        """Test summary length validation."""
        long_summary = "x" * 1200  # Exceeds 1000 char limit

        result = ProcessingResult(
            article_id="test-article-id",
            chat_id="-1001234567890",
            topic_name="Programming",
            summary=long_summary,
        )

        # Summary should be truncated
        assert len(result.summary) == 1003  # 1000 + "..."
        assert result.summary.endswith("...")


class TestProcessingStats:
    """Test ProcessingStats dataclass functionality."""

    def test_processing_stats_creation(self):
        """Test creating processing stats."""
        stats = ProcessingStats(
            total_articles=100,
            pre_filtered_articles=20,
            ai_processed_articles=18,
            delivered_articles=15,
        )

        assert stats.total_articles == 100
        assert stats.pre_filtered_articles == 20
        assert stats.ai_processed_articles == 18
        assert stats.delivered_articles == 15

    def test_processing_stats_calculations(self):
        """Test processing stats calculations."""
        stats = ProcessingStats(
            total_articles=100,
            pre_filtered_articles=20,
            ai_processed_articles=18,
            delivered_articles=15,
        )

        # Test pre-filter reduction percentage
        expected_reduction = (1 - 20 / 100) * 100  # 80%
        assert stats.pre_filter_reduction_percent == expected_reduction

        # Test delivery success rate
        expected_success_rate = (15 / 18) * 100  # ~83.33%
        assert abs(stats.delivery_success_rate - expected_success_rate) < 0.01

    def test_processing_stats_edge_cases(self):
        """Test processing stats edge cases."""
        # Zero articles
        zero_stats = ProcessingStats()
        assert zero_stats.pre_filter_reduction_percent == 0.0
        assert zero_stats.delivery_success_rate == 0.0

        # No AI processed articles
        no_ai_stats = ProcessingStats(
            total_articles=100,
            pre_filtered_articles=20,
            ai_processed_articles=0,
            delivered_articles=0,
        )
        assert no_ai_stats.delivery_success_rate == 0.0


def test_topics_table_has_description_columns(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    assert "description" in cols
    assert "embedding_signature" in cols
    assert "embedding_updated_at" in cols


def test_topics_migration_idempotent(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    schema.create_tables()  # second run must not raise


def test_vector_tables_created(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    import sqlite3, sqlite_vec
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.enable_load_extension(True); sqlite_vec.load(conn)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
    assert "topic_embeddings" in tables
    assert "article_embeddings" in tables


def test_processing_results_v2_columns(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(processing_results)").fetchall()}
    for c in ("embedding_score", "embedding_top_topics", "llm_decision",
              "llm_reasoning", "pipeline_version"):
        assert c in cols, f"missing {c}"


def test_migration_against_prod_snapshot(tmp_path):
    """Regression: applying schema to existing prod-shape DB must not lose rows."""
    import shutil, os, pytest, sqlite3
    src = "/tmp/culifeed_snapshot.db"
    if not os.path.exists(src):
        pytest.skip("snapshot not present")
    dst = str(tmp_path / "snap.db")
    shutil.copy(src, dst)
    pre_count = sqlite3.connect(dst).execute(
        "SELECT COUNT(*) FROM processing_results").fetchone()[0]

    from culifeed.database.schema import DatabaseSchema
    DatabaseSchema(dst).create_tables()  # idempotent migration

    post_count = sqlite3.connect(dst).execute(
        "SELECT COUNT(*) FROM processing_results").fetchone()[0]
    assert post_count == pre_count, f"row loss: {pre_count} → {post_count}"
    cols = {row[1] for row in sqlite3.connect(dst).execute(
        "PRAGMA table_info(processing_results)").fetchall()}
    assert "pipeline_version" in cols
    # Existing rows should default to 'v1'
    versions = sqlite3.connect(dst).execute(
        "SELECT DISTINCT pipeline_version FROM processing_results").fetchall()
    assert ("v1",) in versions or all(v[0] == "v1" for v in versions)
