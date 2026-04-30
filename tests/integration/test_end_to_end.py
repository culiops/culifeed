#!/usr/bin/env python3
"""
End-to-End Integration Tests for CuliFeed
========================================

Tests complete system workflow from RSS feeds to Telegram delivery.
These tests verify the integration between all major components.
"""

import asyncio
import pytest
import pytest_asyncio
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import sqlite3

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from culifeed.config.settings import get_settings
from culifeed.database.connection import get_db_manager
from culifeed.database.schema import DatabaseSchema
from culifeed.scheduler.daily_scheduler import DailyScheduler
from culifeed.storage.channel_repository import ChannelRepository
from culifeed.storage.feed_repository import FeedRepository


class TestEndToEndIntegration:
    """
    End-to-end integration tests for the complete CuliFeed system.
    Tests the full workflow from configuration to content delivery.
    """

    @pytest_asyncio.fixture
    async def test_database(self):
        """Create a temporary test database."""
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_culifeed.db"

        try:
            # Create database schema
            schema = DatabaseSchema(str(db_path))
            schema.create_tables()

            # Verify schema
            assert schema.verify_schema(), "Database schema verification failed"

            yield str(db_path)
        finally:
            # Cleanup
            shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def test_settings(self, test_database):
        """Create test settings with temporary database."""
        with patch("culifeed.config.settings.get_settings") as mock_settings:
            settings = MagicMock()
            settings.database.path = test_database
            settings.database.cleanup_days = 30
            settings.database.max_size_mb = 100

            settings.processing.processing_interval_hours = 1
            settings.processing.max_articles_per_topic = 10
            settings.processing.ai_provider = "gemini"

            settings.logging.level = "INFO"
            settings.logging.console_logging = True
            settings.logging.structured_logging = False

            # Mock AI provider configuration
            settings.get_ai_fallback_providers.return_value = ["gemini", "groq"]
            settings.get_effective_log_level.return_value = "INFO"

            mock_settings.return_value = settings
            yield settings

    @pytest_asyncio.fixture
    async def populated_database(self, test_database, test_settings):
        """Create a database populated with test data."""
        import json

        db_manager = get_db_manager(test_database)

        # Create test channels
        channel_repo = ChannelRepository(db_manager)
        feed_repo = FeedRepository(db_manager)

        # Test channel 1
        test_channel_1 = {
            "chat_id": "test_channel_1",
            "chat_title": "Test Tech Channel",
            "chat_type": "group",
            "active": True,
        }

        # Test channel 2
        test_channel_2 = {
            "chat_id": "test_channel_2",
            "chat_title": "Test News Channel",
            "chat_type": "group",
            "active": True,
        }

        # Clear existing data first to ensure clean state
        with db_manager.get_connection() as conn:
            conn.execute("DELETE FROM channels")
            conn.execute("DELETE FROM feeds")
            conn.execute("DELETE FROM topics")
            conn.commit()

        # Add channels to database
        with db_manager.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO channels (chat_id, chat_title, chat_type, active, created_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    test_channel_1["chat_id"],
                    test_channel_1["chat_title"],
                    test_channel_1["chat_type"],
                    test_channel_1["active"],
                    datetime.now(),
                ),
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO channels (chat_id, chat_title, chat_type, active, created_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    test_channel_2["chat_id"],
                    test_channel_2["chat_title"],
                    test_channel_2["chat_type"],
                    test_channel_2["active"],
                    datetime.now(),
                ),
            )

            # Add test feeds
            test_feeds = [
                {
                    "chat_id": "test_channel_1",
                    "url": "https://aws.amazon.com/blogs/compute/feed/",
                    "title": "AWS Compute Blog",
                    "active": True,
                },
                {
                    "chat_id": "test_channel_1",
                    "url": "https://kubernetes.io/feed.xml",
                    "title": "Kubernetes Blog",
                    "active": True,
                },
                {
                    "chat_id": "test_channel_2",
                    "url": "https://techcrunch.com/feed/",
                    "title": "TechCrunch",
                    "active": True,
                },
            ]

            for feed in test_feeds:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feeds (chat_id, url, title, active, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        feed["chat_id"],
                        feed["url"],
                        feed["title"],
                        feed["active"],
                        datetime.now(),
                    ),
                )

            # Add test topics
            test_topics = [
                {
                    "chat_id": "test_channel_1",
                    "name": "Cloud Computing",
                    "keywords": ["cloud", "aws", "kubernetes", "container"],
                    "active": True,
                },
                {
                    "chat_id": "test_channel_2",
                    "name": "Tech News",
                    "keywords": ["startup", "funding", "technology", "innovation"],
                    "active": True,
                },
            ]

            for topic in test_topics:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO topics (chat_id, name, keywords, active, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        topic["chat_id"],
                        topic["name"],
                        json.dumps(topic["keywords"]),
                        topic["active"],
                        datetime.now(),
                    ),
                )

            # Commit all the inserts
            conn.commit()

        yield db_manager

    @pytest.mark.asyncio
    async def test_database_initialization(self, test_database):
        """Test that database initializes correctly with proper schema."""
        # Test database connection
        db_manager = get_db_manager(test_database)
        db_info = db_manager.get_database_info()

        assert db_info["database_size_mb"] >= 0
        assert db_info["total_connections"] >= 0

        # Test schema verification
        schema = DatabaseSchema(test_database)
        assert schema.verify_schema()

        # Test table creation
        with db_manager.get_connection() as conn:
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
            ]
            assert all(
                table in tables for table in expected_tables
            ), f"Missing tables. Found: {tables}"

    @pytest.mark.asyncio
    async def test_scheduler_health_check(self, test_settings, populated_database):
        """Test scheduler health monitoring functionality."""
        scheduler = DailyScheduler(test_settings)

        # Test health status check
        status = await scheduler.check_processing_status()

        assert "health_status" in status
        assert "current_time" in status
        assert "processed_today" in status
        assert "recent_success_rate" in status

        # Health status should be one of the expected values
        assert status["health_status"] in ["healthy", "warning", "error"]

    @pytest.mark.asyncio
    async def test_daily_processing_dry_run(self, test_settings, populated_database):
        """Test complete daily processing workflow in dry-run mode."""

        # Run scheduler in dry run mode to avoid external dependencies
        scheduler = DailyScheduler(test_settings)

        # Mock the pipeline.process_channel method to avoid AI API calls
        from unittest.mock import AsyncMock, MagicMock

        mock_pipeline_result = MagicMock()

        # Set up proper numeric attributes matching what scheduler expects
        mock_pipeline_result.total_feeds_processed = 1
        mock_pipeline_result.total_articles_fetched = 5
        mock_pipeline_result.unique_articles_after_dedup = 3
        mock_pipeline_result.articles_passed_prefilter = 2
        mock_pipeline_result.ai_requests_sent = 2
        mock_pipeline_result.ai_requests_successful = 2
        mock_pipeline_result.articles_ai_relevant = 1
        mock_pipeline_result.articles_sent_to_telegram = 1
        mock_pipeline_result.telegram_messages_sent = 1
        mock_pipeline_result.ai_provider_breakdown = {}
        mock_pipeline_result.errors = []

        scheduler.pipeline.process_channel = AsyncMock(
            return_value=mock_pipeline_result
        )

        result = await scheduler.run_daily_processing(dry_run=True)

        # Verify results structure
        assert "success" in result
        assert "channels_processed" in result
        assert "duration_seconds" in result
        assert "execution_id" in result
        assert "pipeline_metrics" in result
        assert "ai_metrics" in result
        assert "delivery_metrics" in result

        # In dry run mode, should process channels but not send messages
        if result["channels_processed"] > 0:
            scheduler.pipeline.process_channel.assert_called()

    @pytest.mark.asyncio
    async def test_database_operations(self, populated_database):
        """Test basic database operations work correctly."""

        # Test channel repository operations
        channel_repo = ChannelRepository(populated_database)

        channels = channel_repo.get_all_active_channels()
        assert len(channels) == 2

        # Test that channels have expected properties
        channel_ids = [ch["chat_id"] for ch in channels]
        assert "test_channel_1" in channel_ids
        assert "test_channel_2" in channel_ids

        # Test feed repository operations
        feed_repo = FeedRepository(populated_database)

        all_feeds = feed_repo.get_all_active_feeds()
        assert len(all_feeds) >= 3

        channel_1_feeds = feed_repo.get_feeds_for_chat("test_channel_1")
        assert len(channel_1_feeds) == 2

        channel_2_feeds = feed_repo.get_feeds_for_chat("test_channel_2")
        assert len(channel_2_feeds) == 1

    @pytest.mark.asyncio
    async def test_error_handling_integration(self, test_settings, populated_database):
        """Test error handling across the integrated system."""

        scheduler = DailyScheduler(test_settings)

        # Test with database error simulation
        with patch.object(
            populated_database,
            "get_database_info",
            side_effect=Exception("Database error"),
        ):
            with pytest.raises(Exception):
                await scheduler._perform_health_checks()

        # Test graceful handling of processing errors
        from unittest.mock import AsyncMock, MagicMock

        # Mock pipeline to simulate processing failure
        mock_pipeline_result = MagicMock()
        mock_pipeline_result.successful_feed_fetches = 0
        mock_pipeline_result.articles_ready_for_ai = 0
        mock_pipeline_result.errors = ["Test processing error"]

        scheduler.pipeline.process_channel = AsyncMock(
            return_value=mock_pipeline_result
        )

        result = await scheduler.run_daily_processing(dry_run=True)

        # Should handle errors gracefully
        assert "success" in result
        assert "errors_count" in result

    @pytest.mark.asyncio
    async def test_performance_monitoring(self, test_settings, populated_database):
        """Test performance monitoring integration."""

        scheduler = DailyScheduler(test_settings)

        # Mock successful processing
        from unittest.mock import AsyncMock, MagicMock

        mock_pipeline_result = MagicMock()
        # Set up proper numeric attributes matching what scheduler expects
        mock_pipeline_result.total_feeds_processed = 1
        mock_pipeline_result.total_articles_fetched = 0
        mock_pipeline_result.unique_articles_after_dedup = 0
        mock_pipeline_result.articles_passed_prefilter = 0
        mock_pipeline_result.ai_requests_sent = 0
        mock_pipeline_result.ai_requests_successful = 0
        mock_pipeline_result.articles_ai_relevant = 0
        mock_pipeline_result.articles_sent_to_telegram = 0
        mock_pipeline_result.telegram_messages_sent = 0
        mock_pipeline_result.ai_provider_breakdown = {}
        mock_pipeline_result.errors = []

        scheduler.pipeline.process_channel = AsyncMock(
            return_value=mock_pipeline_result
        )

        result = await scheduler.run_daily_processing(dry_run=True)

        # Should include pipeline, AI, and delivery metrics
        assert "pipeline_metrics" in result
        assert "ai_metrics" in result
        assert "delivery_metrics" in result
        assert "duration_seconds" in result
        assert result["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_configuration_integration(self, test_settings):
        """Test configuration system integration."""

        # Test settings loading
        assert test_settings.database.path is not None
        assert test_settings.processing.processing_interval_hours == 1
        assert test_settings.processing.max_articles_per_topic == 10

        # Test AI provider configuration
        providers = test_settings.get_ai_fallback_providers()
        assert len(providers) >= 1
        assert "gemini" in providers

    @pytest.mark.asyncio
    async def test_multi_channel_processing(self, test_settings, populated_database):
        """Test processing multiple channels simultaneously."""

        # Run processing
        scheduler = DailyScheduler(test_settings)

        # Mock pipeline to simulate successful processing
        from unittest.mock import AsyncMock, MagicMock

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.successful_feed_fetches = 1
        mock_pipeline_result.articles_ready_for_ai = 1
        mock_pipeline_result.errors = []

        scheduler.pipeline.process_channel = AsyncMock(
            return_value=mock_pipeline_result
        )

        result = await scheduler.run_daily_processing(dry_run=True)

        # Should process multiple channels if they exist
        assert "success" in result
        assert "channels_processed" in result

        if result["channels_processed"] > 1:
            # Should have been called multiple times for multiple channels
            assert scheduler.pipeline.process_channel.call_count >= 1

    @pytest.mark.asyncio
    async def test_cleanup_operations(self, test_settings, populated_database):
        """Test database cleanup operations integration."""

        # Add some old test data
        with populated_database.get_connection() as conn:
            old_date = datetime.now() - timedelta(days=40)
            conn.execute(
                """
                INSERT INTO articles (id, title, url, content, published_at, source_feed, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    "old_test_article_1",
                    "Old Test Article",
                    "https://example.com/old",
                    "Old content",
                    old_date,
                    "https://example.com/feed.xml",
                    "hash123",
                    old_date,
                ),
            )

        scheduler = DailyScheduler(test_settings)

        # Test cleanup operations
        await scheduler._post_processing_cleanup()

        # Verify cleanup worked (this is a basic test, real implementation would check actual cleanup)
        db_info = populated_database.get_database_info()
        assert db_info is not None


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_complete_workflow_simulation():
    """
    Simulate a complete end-to-end workflow without external dependencies.
    This test runs the entire system in isolation.
    """

    # Small delay to ensure any previous database connections are closed
    import time

    time.sleep(0.1)

    # Create temporary database
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "workflow_test.db"

    try:
        # Initialize database
        schema = DatabaseSchema(str(db_path))
        schema.create_tables()

        # Mock settings
        with patch("culifeed.config.settings.get_settings") as mock_settings:
            from unittest.mock import MagicMock

            settings = MagicMock()
            settings.database.path = str(db_path)
            settings.database.cleanup_days = 30
            settings.database.max_size_mb = 500  # Add the missing attribute
            settings.processing.processing_interval_hours = 1
            settings.processing.max_articles_per_topic = 5
            settings.telegram.bot_token = None  # No bot for test
            settings.get_ai_fallback_providers.return_value = ["gemini"]
            mock_settings.return_value = settings

            # Create minimal test data using direct sqlite connection
            # to avoid any global state conflicts with db_manager
            import sqlite3

            try:
                conn = sqlite3.connect(str(db_path))
                conn.execute(
                    """
                    INSERT INTO channels (chat_id, chat_title, chat_type, active, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        "workflow_test",
                        "Workflow Test Channel",
                        "group",
                        True,
                        datetime.now(),
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"Database error: {e}")
                if "conn" in locals():
                    conn.close()
                raise

            # Now use db_manager for the scheduler
            db_manager = get_db_manager(str(db_path))

            # Create scheduler and mock external dependencies
            scheduler = DailyScheduler(settings)

            # Mock the pipeline to avoid external API calls
            from unittest.mock import AsyncMock, MagicMock

            mock_result = MagicMock()
            # Set up proper numeric attributes matching what scheduler expects
            mock_result.total_feeds_processed = 1
            mock_result.total_articles_fetched = 0
            mock_result.unique_articles_after_dedup = 0
            mock_result.articles_passed_prefilter = 0
            mock_result.ai_requests_sent = 0
            mock_result.ai_requests_successful = 0
            mock_result.articles_ai_relevant = 0
            mock_result.articles_sent_to_telegram = 0
            mock_result.telegram_messages_sent = 0
            mock_result.ai_provider_breakdown = {}
            mock_result.errors = []

            scheduler.pipeline.process_channel = AsyncMock(return_value=mock_result)

            # Run complete workflow
            result = await scheduler.run_daily_processing(dry_run=True)

            # Verify workflow completed
            assert result["success"] == True
            assert "execution_id" in result
            assert "duration_seconds" in result
            assert result["channels_processed"] >= 0

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
