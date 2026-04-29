"""
Foundation Tests for CuliFeed
============================

Test suite for core foundation components including database,
configuration, logging, and validation systems.
"""

import pytest
import sqlite3
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from culifeed.database.schema import DatabaseSchema
from culifeed.database.connection import DatabaseConnection
from culifeed.database.models import (
    Channel,
    Article,
    Topic,
    Feed,
    ProcessingResult,
    ChatType,
)
from culifeed.config.settings import CuliFeedSettings, load_settings, AIProvider
from culifeed.utils.logging import (
    setup_logger,
    get_logger_for_component,
    PerformanceLogger,
)
from culifeed.utils.exceptions import (
    CuliFeedError,
    ConfigurationError,
    DatabaseError,
    ValidationError,
    ErrorCode,
    handle_exception,
    is_retryable_error,
)
from culifeed.utils.validators import URLValidator, ContentValidator, ConfigValidator


class TestDatabaseSchema:
    """Test database schema creation and validation."""

    def test_create_tables(self, tmp_path):
        """Test database table creation."""
        db_path = tmp_path / "test.db"
        schema = DatabaseSchema(str(db_path))

        # Create tables
        schema.create_tables()

        # Verify tables exist
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                """
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """
            )
            tables = [row[0] for row in cursor.fetchall()]

        expected_tables = [
            "articles",
            "channels",
            "feeds",
            "processing_results",
            "topics",
            "user_subscriptions",  # Added for SaaS pricing feature
        ]
        assert set(tables) == set(expected_tables)

    def test_verify_schema(self, tmp_path):
        """Test schema verification."""
        db_path = tmp_path / "test.db"
        schema = DatabaseSchema(str(db_path))

        # Schema should fail before tables are created
        assert not schema.verify_schema()

        # Create tables and verify
        schema.create_tables()
        assert schema.verify_schema()

    def test_foreign_key_constraints(self, tmp_path):
        """Test foreign key constraints are properly set."""
        db_path = tmp_path / "test.db"
        schema = DatabaseSchema(str(db_path))
        schema.create_tables()

        with schema.get_connection() as conn:
            # Insert test channel
            conn.execute(
                """
                INSERT INTO channels (chat_id, chat_title, chat_type)
                VALUES ('123456', 'Test Channel', 'group')
            """
            )

            # Try to insert topic with invalid chat_id (should fail)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO topics (chat_id, name, keywords)
                    VALUES ('invalid_chat', 'Test Topic', '["keyword"]')
                """
                )


class TestDatabaseConnection:
    """Test database connection management and pooling."""

    def test_connection_creation(self, tmp_path):
        """Test database connection creation."""
        db_path = tmp_path / "test.db"
        db_manager = DatabaseConnection(str(db_path), pool_size=2)

        with db_manager.get_connection() as conn:
            # Test basic functionality
            cursor = conn.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1

    def test_connection_pooling(self, tmp_path):
        """Test connection pool management."""
        db_path = tmp_path / "test.db"
        db_manager = DatabaseConnection(str(db_path), pool_size=2)

        # Get multiple connections
        connections = []
        for _ in range(3):  # More than pool size
            conn_context = db_manager.get_connection()
            connections.append(conn_context)

        # All connections should work
        for conn_context in connections:
            with conn_context as conn:
                result = conn.execute("SELECT 1").fetchone()
                assert result[0] == 1

    def test_transaction_management(self, tmp_path):
        """Test transaction rollback on errors."""
        db_path = tmp_path / "test.db"
        schema = DatabaseSchema(str(db_path))
        schema.create_tables()

        db_manager = DatabaseConnection(str(db_path))

        # Test successful transaction
        with db_manager.transaction() as conn:
            conn.execute(
                """
                INSERT INTO channels (chat_id, chat_title, chat_type)
                VALUES ('123456', 'Test Channel', 'group')
            """
            )

        # Verify data was committed
        result = db_manager.execute_one(
            """
            SELECT chat_title FROM channels WHERE chat_id = ?
        """,
            ("123456",),
        )
        assert result["chat_title"] == "Test Channel"

        # Test rollback on error
        try:
            with db_manager.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO channels (chat_id, chat_title, chat_type)
                    VALUES ('789012', 'Another Channel', 'group')
                """
                )
                # Force an error
                raise ValueError("Test error")
        except ValueError:
            pass

        # Verify rollback worked
        result = db_manager.execute_one(
            """
            SELECT chat_title FROM channels WHERE chat_id = ?
        """,
            ("789012",),
        )
        assert result is None


class TestDataModels:
    """Test Pydantic data models."""

    def test_channel_model(self):
        """Test Channel model validation."""
        # Valid channel
        channel = Channel(
            chat_id="-1001234567890", chat_title="Test Group", chat_type=ChatType.GROUP
        )
        assert channel.chat_id == "-1001234567890"
        assert channel.chat_type == ChatType.GROUP
        assert channel.active is True

    def test_article_model(self):
        """Test Article model validation and hash generation."""
        article = Article(
            title="Test Article",
            url="https://example.com/article",
            source_feed="https://example.com/feed.xml",
        )

        assert article.title == "Test Article"
        assert str(article.url) == "https://example.com/article"
        assert article.content_hash is not None
        assert len(article.content_hash) == 64  # SHA256 hash length

    def test_topic_model(self):
        """Test Topic model validation."""
        topic = Topic(
            chat_id="-1001234567890",
            name="Test Topic",
            keywords=["python", "programming", "AI"],
            exclude_keywords=["beginner", "tutorial"],
        )

        assert topic.name == "Test Topic"
        assert len(topic.keywords) == 3
        assert "python" in topic.keywords
        assert topic.confidence_threshold == 0.6

        # Test keyword normalization
        topic_with_mixed_case = Topic(
            chat_id="-1001234567890",
            name="Mixed Case Topic",
            keywords=["Python", "PROGRAMMING", "  ai  "],
        )

        assert "python" in topic_with_mixed_case.keywords
        assert "programming" in topic_with_mixed_case.keywords
        assert "ai" in topic_with_mixed_case.keywords

    def test_processing_result_model(self):
        """Test ProcessingResult model."""
        result = ProcessingResult(
            article_id="test-article-id",
            chat_id="-1001234567890",
            topic_name="Test Topic",
            ai_relevance_score=0.85,
            confidence_score=0.92,
            summary="This is a test summary.",
        )

        assert result.meets_confidence_threshold(0.8)
        assert not result.meets_confidence_threshold(0.95)
        assert result.is_high_quality()


class TestConfiguration:
    """Test configuration system."""

    def test_settings_validation(self):
        """Test settings validation with environment variables."""
        with patch.dict(
            os.environ,
            {
                "CULIFEED_TELEGRAM__BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11_test",
                "CULIFEED_AI__GEMINI_API_KEY": "test-gemini-key",
                "CULIFEED_PROCESSING__AI_PROVIDER": "gemini",  # Override YAML setting
            },
        ):
            settings = CuliFeedSettings()

            # Should not raise validation error
            settings.validate_configuration()

            assert settings.processing.ai_provider == AIProvider.GEMINI
            assert len(settings.get_ai_fallback_providers()) >= 1

    def test_invalid_configuration(self):
        """Test configuration validation with missing required fields."""
        with pytest.raises(Exception):  # Should raise validation error
            CuliFeedSettings(telegram={"bot_token": "invalid-token"})

    def test_ai_provider_fallback(self):
        """Test AI provider fallback logic."""
        with patch.dict(
            os.environ,
            {
                "CULIFEED_TELEGRAM__BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11_test",
                "CULIFEED_AI__GEMINI_API_KEY": "test-gemini-key",
                "CULIFEED_AI__GROQ_API_KEY": "test-groq-key",
            },
        ):
            settings = CuliFeedSettings()

            fallbacks = settings.get_ai_fallback_providers()
            assert AIProvider.GEMINI in fallbacks
            assert AIProvider.GROQ in fallbacks


class TestLogging:
    """Test logging system."""

    def test_logger_setup(self, tmp_path):
        """Test logger configuration."""
        log_file = tmp_path / "test.log"
        logger = setup_logger(
            name="test_logger",
            level="INFO",
            log_file=str(log_file),
            console=False,
            structured=True,
        )

        logger.info("Test message")
        logger.error("Test error message")

        # Check log file was created and contains messages
        assert log_file.exists()
        log_content = log_file.read_text()
        assert "Test message" in log_content
        assert "Test error message" in log_content

    def test_component_logger(self):
        """Test component-specific logger."""
        logger = get_logger_for_component(
            "test_component", chat_id="123456", article_id="test-article"
        )

        # Should not raise any errors
        logger.info("Component test message")
        logger.debug("Debug message")

    def test_performance_logger(self, caplog):
        """Test performance logging context manager."""
        import logging

        # Set proper log level to capture INFO messages
        caplog.set_level(logging.INFO)
        logger = logging.getLogger("test")
        logger.setLevel(logging.INFO)

        with PerformanceLogger(logger, "test_operation", param1="value1"):
            # Simulate some work
            pass

        # Check that performance was logged
        assert "Completed test_operation" in caplog.text


class TestExceptions:
    """Test exception handling system."""

    def test_culifeed_error(self):
        """Test CuliFeed error creation and serialization."""
        error = CuliFeedError(
            message="Test error",
            error_code=ErrorCode.CONFIG_INVALID,
            context={"key": "value"},
            user_message="User-friendly message",
            recoverable=True,
        )

        assert str(error) == "[C001] Test error"
        assert error.user_message == "User-friendly message"
        assert error.recoverable is True

        error_dict = error.to_dict()
        assert error_dict["error_code"] == "C001"
        assert error_dict["context"]["key"] == "value"

    def test_specific_errors(self):
        """Test specific error types."""
        # Database error
        db_error = DatabaseError(
            message="Connection failed",
            query="SELECT * FROM test",
            error_code=ErrorCode.DATABASE_CONNECTION,
        )
        assert db_error.context["query"] == "SELECT * FROM test"

        # Configuration error
        config_error = ConfigurationError(
            message="Invalid config", config_key="database.path"
        )
        assert config_error.context["config_key"] == "database.path"

    def test_exception_handling(self, caplog):
        """Test exception handling utility."""
        import logging

        logger = logging.getLogger("test")

        # Test handling of generic exception
        original_error = ValueError("Test value error")
        handled_error = handle_exception(
            original_error, logger, "test_operation", {"context_key": "context_value"}
        )

        assert isinstance(handled_error, CuliFeedError)
        assert "test_operation" in handled_error.context["operation"]
        assert handled_error.context["context_key"] == "context_value"

    def test_retryable_errors(self):
        """Test retryable error detection."""
        # Retryable error
        retryable_error = CuliFeedError(
            message="Network timeout",
            error_code=ErrorCode.FEED_NETWORK_ERROR,
            recoverable=True,
        )
        assert is_retryable_error(retryable_error)

        # Non-retryable error
        non_retryable_error = ValidationError(
            message="Invalid input", recoverable=False
        )
        assert not is_retryable_error(non_retryable_error)


class TestValidators:
    """Test validation utilities."""

    def test_url_validator(self):
        """Test URL validation."""
        # Valid URLs
        valid_url = URLValidator.validate_feed_url("https://example.com/feed.xml")
        assert valid_url == "https://example.com/feed.xml"

        valid_url2 = URLValidator.validate_feed_url("HTTP://EXAMPLE.COM/RSS")
        assert valid_url2 == "http://example.com/RSS"

        # Invalid URLs
        with pytest.raises(ValidationError):
            URLValidator.validate_feed_url("")

        with pytest.raises(ValidationError):
            URLValidator.validate_feed_url("javascript:alert(1)")

        with pytest.raises(ValidationError):
            URLValidator.validate_feed_url("ftp://example.com/feed")

        # Test feed detection
        assert URLValidator.is_likely_feed_url("https://example.com/feed.xml")
        assert URLValidator.is_likely_feed_url("https://example.com/rss")
        assert not URLValidator.is_likely_feed_url("https://example.com/about")

    def test_content_validator(self):
        """Test content validation."""
        # Valid title
        title = ContentValidator.validate_article_title("  Test Article Title  ")
        assert title == "Test Article Title"

        # Empty title
        with pytest.raises(ValidationError):
            ContentValidator.validate_article_title("")

        # Long title
        long_title = "x" * 2000
        with pytest.raises(ValidationError):
            ContentValidator.validate_article_title(long_title)

        # Valid content
        content = ContentValidator.validate_article_content("Test article content")
        assert content == "Test article content"

        # Very long content should be truncated
        long_content = "x" * 60000
        truncated = ContentValidator.validate_article_content(long_content)
        assert len(truncated) <= 50020  # Original limit + truncation message
        assert "truncated" in truncated

    def test_topic_validator(self):
        """Test topic and keyword validation."""
        # Valid topic name
        name = ContentValidator.validate_topic_name("  AWS Lambda Performance  ")
        assert name == "AWS Lambda Performance"

        # Invalid characters
        with pytest.raises(ValidationError):
            ContentValidator.validate_topic_name("Topic <with> bad chars")

        # Valid keywords
        keywords = ContentValidator.validate_keywords(
            ["Python", "  programming  ", "AI", "machine-learning"]
        )
        assert "python" in keywords
        assert "programming" in keywords
        assert "ai" in keywords
        assert "machine-learning" in keywords

        # Empty keywords
        with pytest.raises(ValidationError):
            ContentValidator.validate_keywords([])

    def test_config_validator(self):
        """Test configuration validators."""
        # Valid confidence threshold
        threshold = ConfigValidator.validate_confidence_threshold(0.8)
        assert threshold == 0.8

        # Invalid thresholds
        with pytest.raises(ValidationError):
            ConfigValidator.validate_confidence_threshold(-0.1)

        with pytest.raises(ValidationError):
            ConfigValidator.validate_confidence_threshold(1.5)

        # Valid chat ID
        chat_id = ConfigValidator.validate_chat_id("-1001234567890")
        assert chat_id == "-1001234567890"

        # Invalid chat ID
        with pytest.raises(ValidationError):
            ConfigValidator.validate_chat_id("invalid-chat-id")


@pytest.fixture
def tmp_path():
    """Create temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_embedding_settings_defaults():
    from culifeed.config.settings import get_settings
    s = get_settings()
    assert s.filtering.embedding_provider == "openai"
    assert s.filtering.embedding_model == "text-embedding-3-small"
    assert 0.0 <= s.filtering.embedding_min_score <= 1.0
    assert s.filtering.embedding_min_score == 0.45
    assert s.filtering.embedding_fallback_threshold == 0.65
    assert s.filtering.embedding_retention_days == 90
    assert s.filtering.use_embedding_pipeline is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
