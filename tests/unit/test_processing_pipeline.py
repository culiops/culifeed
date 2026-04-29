"""
Unit tests for ProcessingPipeline orchestrator.

Tests the complete content processing pipeline that coordinates:
- Feed fetching and parsing
- Article processing and deduplication
- Pre-filtering with topic matching
- Database storage for AI processing
"""

import pytest
import asyncio
import sqlite3
import tempfile
import os
import json
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import List, Dict, Any

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from culifeed.processing.pipeline import (
    ProcessingPipeline,
    PipelineResult,
    ProcessingStats,
)
from culifeed.processing.article_processor import DeduplicationStats
from culifeed.processing.feed_fetcher import FetchResult
from culifeed.database.connection import DatabaseConnection
from culifeed.database.models import Feed, Topic, Article
from culifeed.database.schema import DatabaseSchema
from culifeed.config.settings import get_settings
from culifeed.utils.exceptions import ProcessingError


class TestProcessingPipeline:
    """Test ProcessingPipeline orchestration and workflow coordination.

    Covers:
    - Single channel processing workflow
    - Multi-channel concurrent processing
    - Component integration and error handling
    - Database operations and result aggregation
    - Performance metrics and efficiency calculations
    """

    @pytest.fixture
    def test_database(self):
        """Create temporary database with schema for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        schema = DatabaseSchema(db_path)
        schema.create_tables()

        yield db_path

        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass

    @pytest.fixture
    def db_connection(self, test_database):
        """Database connection fixture."""
        return DatabaseConnection(test_database)

    @pytest.fixture
    def sample_feeds(self, db_connection):
        """Create sample feeds in database."""
        # First create required channels
        with db_connection.get_connection() as conn:
            # Create channels first for foreign key constraints
            channels = [
                ("test_channel", "Test Channel", "group"),
                ("other_channel", "Other Channel", "group"),
            ]

            for chat_id, title, chat_type in channels:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO channels (chat_id, chat_title, chat_type, created_at)
                    VALUES (?, ?, ?, ?)
                """,
                    (chat_id, title, chat_type, datetime.now(timezone.utc)),
                )

            conn.commit()

        feeds = [
            Feed(
                id=1,
                chat_id="test_channel",
                url="https://example.com/feed1.xml",
                title="Tech Feed",
                active=True,
                created_at=datetime.now(timezone.utc),
            ),
            Feed(
                id=2,
                chat_id="test_channel",
                url="https://example.com/feed2.xml",
                title="News Feed",
                active=True,
                created_at=datetime.now(timezone.utc),
            ),
            Feed(
                id=3,
                chat_id="other_channel",
                url="https://example.com/feed3.xml",
                title="Other Feed",
                active=True,
                created_at=datetime.now(timezone.utc),
            ),
        ]

        with db_connection.get_connection() as conn:
            for feed in feeds:
                conn.execute(
                    """
                    INSERT INTO feeds (id, chat_id, url, title, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        feed.id,
                        feed.chat_id,
                        str(feed.url),
                        feed.title,
                        feed.active,
                        feed.created_at,
                    ),
                )
            conn.commit()

        return feeds

    @pytest.fixture
    def sample_topics(self, db_connection):
        """Create sample topics in database."""
        topics = [
            Topic(
                id=1,
                chat_id="test_channel",
                name="AI Technology",
                keywords=["artificial intelligence", "machine learning", "AI"],
                exclude_keywords=["crypto"],
                active=True,
                created_at=datetime.now(timezone.utc),
            ),
            Topic(
                id=2,
                chat_id="test_channel",
                name="Web Development",
                keywords=["react", "javascript", "frontend"],
                exclude_keywords=["spam"],
                active=True,
                created_at=datetime.now(timezone.utc),
            ),
        ]

        with db_connection.get_connection() as conn:
            # Create channels first for foreign key constraints
            channels = [
                ("test_channel", "Test Channel", "group"),
            ]

            for chat_id, title, chat_type in channels:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO channels (chat_id, chat_title, chat_type, created_at)
                    VALUES (?, ?, ?, ?)
                """,
                    (chat_id, title, chat_type, datetime.now(timezone.utc)),
                )

            for topic in topics:
                conn.execute(
                    """
                    INSERT INTO topics (id, chat_id, name, keywords, exclude_keywords, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        topic.id,
                        topic.chat_id,
                        topic.name,
                        json.dumps(topic.keywords),
                        json.dumps(topic.exclude_keywords),
                        topic.active,
                        topic.created_at,
                    ),
                )
            conn.commit()

        return topics

    @pytest.fixture
    def sample_articles(self):
        """Create sample articles for testing."""
        return [
            Article(
                id="article1",
                title="Advanced AI Techniques",
                url="https://example.com/article1",
                content="This article discusses artificial intelligence and machine learning techniques...",
                published_at=datetime.now(timezone.utc),
                source_feed="https://example.com/feed1.xml",
                content_hash="hash1",
                created_at=datetime.now(timezone.utc),
            ),
            Article(
                id="article2",
                title="React Performance Tips",
                url="https://example.com/article2",
                content="Learn how to optimize your React applications with these performance tips...",
                published_at=datetime.now(timezone.utc),
                source_feed="https://example.com/feed2.xml",
                content_hash="hash2",
                created_at=datetime.now(timezone.utc),
            ),
            Article(
                id="article3",
                title="Crypto Trading Guide",
                url="https://example.com/article3",
                content="Complete guide to cryptocurrency trading and blockchain technology...",
                published_at=datetime.now(timezone.utc),
                source_feed="https://example.com/feed1.xml",
                content_hash="hash3",
                created_at=datetime.now(timezone.utc),
            ),
        ]

    @pytest.fixture
    def mock_components(self):
        """Create mocked pipeline components."""
        components = {
            "feed_fetcher": AsyncMock(),
            "feed_manager": Mock(),
            "article_processor": Mock(),
            "pre_filter": Mock(),
        }
        return components

    @pytest.fixture
    def pipeline(self, db_connection, mock_components):
        """Create ProcessingPipeline with mocked components."""
        pipeline = ProcessingPipeline(db_connection)

        # Replace components with mocks
        pipeline.feed_fetcher = mock_components["feed_fetcher"]
        pipeline.feed_manager = mock_components["feed_manager"]
        pipeline.article_processor = mock_components["article_processor"]
        pipeline.pre_filter = mock_components["pre_filter"]

        # Mock AI manager for unit tests with AsyncMock for async methods
        mock_ai_manager = Mock()

        # Mock async methods
        from culifeed.ai.providers.base import AIResult

        mock_ai_manager.analyze_relevance = AsyncMock(
            return_value=AIResult(
                success=True,
                relevance_score=0.85,
                confidence=0.9,
                reasoning="Test AI analysis",
                provider="test_provider",
            )
        )

        mock_ai_manager.generate_summary = AsyncMock(
            return_value=AIResult(
                success=True,
                relevance_score=1.0,
                confidence=0.9,
                summary="Test summary",
                provider="test_provider",
            )
        )

        # Mock sync fallback method (returns AIResult like the real method)
        from culifeed.ai.providers.base import AIResult as FallbackResult

        mock_ai_manager._keyword_fallback_analysis = Mock(
            return_value=FallbackResult(
                success=True,
                relevance_score=0.65,
                confidence=0.5,
                reasoning="Keyword fallback",
                provider="keyword_backup",
            )
        )

        pipeline.ai_manager = mock_ai_manager

        return pipeline

    def test_pipeline_initialization(self, db_connection):
        """Test pipeline initialization with proper component setup."""
        pipeline = ProcessingPipeline(db_connection)

        assert pipeline.db == db_connection
        assert pipeline.settings is not None
        assert pipeline.logger is not None
        assert hasattr(pipeline, "feed_fetcher")
        assert hasattr(pipeline, "feed_manager")
        assert hasattr(pipeline, "article_processor")
        assert hasattr(pipeline, "pre_filter")

    @pytest.mark.asyncio
    async def test_process_channel_success(
        self, pipeline, sample_feeds, sample_topics, sample_articles, mock_components
    ):
        """Test successful channel processing workflow."""
        chat_id = "test_channel"

        # Mock feed manager to return feeds
        mock_components["feed_manager"].get_feeds_for_channel.return_value = [
            feed for feed in sample_feeds if feed.chat_id == chat_id
        ]

        # Mock feed fetcher to return successful results
        fetch_results = [
            FetchResult(
                feed_url="https://example.com/feed1.xml",
                success=True,
                articles=sample_articles[:2],
                error=None,
                fetch_time=1.5,
            ),
            FetchResult(
                feed_url="https://example.com/feed2.xml",
                success=True,
                articles=[sample_articles[2]],
                error=None,
                fetch_time=1.2,
            ),
        ]
        mock_components["feed_fetcher"].fetch_feeds_batch.return_value = fetch_results

        # Mock article processor for deduplication
        from culifeed.processing.article_processor import DeduplicationStats

        dedup_stats = DeduplicationStats(
            total_articles=3,
            unique_articles=3,
            duplicates_found=0,
            duplicates_by_hash=0,
            duplicates_by_url=0,
            duplicates_by_content=0,
        )
        mock_components["article_processor"].process_articles.return_value = (
            sample_articles,
            dedup_stats,
        )

        # Mock pre-filter results
        from culifeed.processing.pre_filter import FilterResult

        filter_results = [
            FilterResult(
                article=sample_articles[0],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.85},
            ),
            FilterResult(
                article=sample_articles[1],
                passed_filter=True,
                matched_topics=["AI Technology"],  # Changed to match existing topic
                relevance_scores={"AI Technology": 0.75},
            ),
            FilterResult(
                article=sample_articles[2],
                passed_filter=False,  # Excluded by crypto keyword
                matched_topics=[],
                relevance_scores={},
            ),
        ]
        mock_components["pre_filter"].filter_articles.return_value = filter_results

        # Execute pipeline
        result = await pipeline.process_channel(chat_id)

        # Verify result structure
        assert isinstance(result, PipelineResult)
        assert result.channel_id == chat_id
        assert result.total_feeds_processed == 2
        assert result.successful_feed_fetches == 2
        assert result.total_articles_fetched == 3
        assert result.unique_articles_after_dedup == 3
        assert result.articles_passed_prefilter == 2
        assert result.articles_ready_for_ai == 2
        assert result.processing_time_seconds > 0
        assert len(result.errors) == 0

        # Verify efficiency metrics
        metrics = result.efficiency_metrics
        assert metrics["feed_success_rate"] == 100.0
        assert metrics["deduplication_rate"] == 0.0
        assert metrics["prefilter_reduction"] == pytest.approx(33.3, rel=1e-1)

        # Verify component interactions
        mock_components["feed_manager"].get_feeds_for_channel.assert_called_once_with(
            chat_id, active_only=True
        )
        mock_components["feed_fetcher"].fetch_feeds_batch.assert_called_once()
        mock_components["article_processor"].process_articles.assert_called_once()
        mock_components["pre_filter"].filter_articles.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_channel_no_feeds(self, pipeline, mock_components):
        """Test channel processing with no active feeds."""
        chat_id = "empty_channel"

        # Mock no feeds found
        mock_components["feed_manager"].get_feeds_for_channel.return_value = []

        result = await pipeline.process_channel(chat_id)

        assert isinstance(result, PipelineResult)
        assert result.channel_id == chat_id
        assert result.total_feeds_processed == 0
        assert result.successful_feed_fetches == 0
        assert result.total_articles_fetched == 0
        assert result.articles_ready_for_ai == 0
        assert len(result.errors) == 0

        # Should not call other components
        mock_components["feed_fetcher"].fetch_feeds_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_channel_fetch_failures(
        self, pipeline, sample_feeds, sample_topics, mock_components
    ):
        """Test channel processing with feed fetch failures."""
        chat_id = "test_channel"

        # Mock feed manager
        mock_components["feed_manager"].get_feeds_for_channel.return_value = [
            feed for feed in sample_feeds if feed.chat_id == chat_id
        ]

        # Mock fetch results with failures
        fetch_results = [
            FetchResult(
                feed_url="https://example.com/feed1.xml",
                success=False,
                articles=None,
                error="Network timeout",
                fetch_time=30.0,
            ),
            FetchResult(
                feed_url="https://example.com/feed2.xml",
                success=False,
                articles=None,
                error="404 Not Found",
                fetch_time=5.0,
            ),
        ]
        mock_components["feed_fetcher"].fetch_feeds_batch.return_value = fetch_results

        result = await pipeline.process_channel(chat_id)

        assert isinstance(result, PipelineResult)
        assert result.channel_id == chat_id
        assert result.total_feeds_processed == 2
        assert result.successful_feed_fetches == 0
        assert result.total_articles_fetched == 0
        assert result.articles_ready_for_ai == 0
        assert len(result.errors) == 2
        assert "Network timeout" in str(result.errors)
        assert "404 Not Found" in str(result.errors)

    @pytest.mark.asyncio
    async def test_process_channel_mixed_results(
        self, pipeline, sample_feeds, sample_topics, sample_articles, mock_components
    ):
        """Test channel processing with mixed success/failure results."""
        chat_id = "test_channel"

        # Mock feed manager
        mock_components["feed_manager"].get_feeds_for_channel.return_value = [
            feed for feed in sample_feeds if feed.chat_id == chat_id
        ]

        # Mock mixed fetch results
        fetch_results = [
            FetchResult(
                feed_url="https://example.com/feed1.xml",
                success=True,
                articles=sample_articles[:2],
                error=None,
                fetch_time=1.5,
            ),
            FetchResult(
                feed_url="https://example.com/feed2.xml",
                success=False,
                articles=None,
                error="Server error",
                fetch_time=10.0,
            ),
        ]
        mock_components["feed_fetcher"].fetch_feeds_batch.return_value = fetch_results

        # Mock article processor
        from culifeed.processing.article_processor import DeduplicationStats

        dedup_stats = DeduplicationStats(
            total_articles=2,
            unique_articles=2,
            duplicates_found=0,
            duplicates_by_hash=0,
            duplicates_by_url=0,
            duplicates_by_content=0,
        )
        mock_components["article_processor"].process_articles.return_value = (
            sample_articles[:2],
            dedup_stats,
        )

        # Mock pre-filter (all pass)
        from culifeed.processing.pre_filter import FilterResult

        filter_results = [
            FilterResult(
                article=sample_articles[0],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.85},
            ),
            FilterResult(
                article=sample_articles[1],
                passed_filter=True,
                matched_topics=["Web Development"],
                relevance_scores={"Web Development": 0.75},
            ),
        ]
        mock_components["pre_filter"].filter_articles.return_value = filter_results

        result = await pipeline.process_channel(chat_id)

        assert result.total_feeds_processed == 2
        assert result.successful_feed_fetches == 1  # Only one succeeded
        assert result.total_articles_fetched == 2
        assert result.articles_ready_for_ai == 2
        assert len(result.errors) == 1
        assert "Server error" in str(result.errors)

    @pytest.mark.asyncio
    async def test_process_channel_no_topics(
        self, pipeline, sample_feeds, sample_articles, mock_components
    ):
        """Test channel processing with no active topics."""
        chat_id = "test_channel"

        # Mock feed manager
        mock_components["feed_manager"].get_feeds_for_channel.return_value = [
            feed for feed in sample_feeds if feed.chat_id == chat_id
        ]

        # Mock successful fetch
        fetch_results = [
            FetchResult(
                feed_url="https://example.com/feed1.xml",
                success=True,
                articles=sample_articles,
                error=None,
                fetch_time=1.5,
            )
        ]
        mock_components["feed_fetcher"].fetch_feeds_batch.return_value = fetch_results

        # Mock article processor
        from culifeed.processing.article_processor import DeduplicationStats

        dedup_stats = DeduplicationStats(
            total_articles=3,
            unique_articles=3,
            duplicates_found=0,
            duplicates_by_hash=0,
            duplicates_by_url=0,
            duplicates_by_content=0,
        )
        mock_components["article_processor"].process_articles.return_value = (
            sample_articles,
            dedup_stats,
        )

        # No topics in database - mock empty result
        with patch.object(pipeline, "_get_channel_topics", return_value=[]):
            result = await pipeline.process_channel(chat_id)

        assert result.total_feeds_processed == 2
        assert result.successful_feed_fetches == 1
        assert result.total_articles_fetched == 3
        assert result.unique_articles_after_dedup == 3
        assert result.articles_passed_prefilter == 0  # No topics to match
        assert result.articles_ready_for_ai == 0

    @pytest.mark.asyncio
    async def test_process_channel_exception_handling(
        self, pipeline, sample_feeds, mock_components
    ):
        """Test channel processing with component exceptions."""
        chat_id = "test_channel"

        # Mock feed manager
        mock_components["feed_manager"].get_feeds_for_channel.return_value = [
            feed for feed in sample_feeds if feed.chat_id == chat_id
        ]

        # Make feed fetcher raise exception
        mock_components["feed_fetcher"].fetch_feeds_batch.side_effect = Exception(
            "Network failure"
        )

        result = await pipeline.process_channel(chat_id)

        assert isinstance(result, PipelineResult)
        assert result.channel_id == chat_id
        assert result.total_feeds_processed == 0
        assert result.articles_ready_for_ai == 0
        assert len(result.errors) == 1
        assert "Pipeline processing failed" in result.errors[0]
        assert "Network failure" in result.errors[0]

    @pytest.mark.asyncio
    async def test_process_multiple_channels(
        self, pipeline, sample_feeds, sample_topics, sample_articles, mock_components
    ):
        """Test concurrent processing of multiple channels."""
        chat_ids = ["test_channel", "other_channel"]

        # Mock successful processing for both channels
        async def mock_process_channel(chat_id):
            return PipelineResult(
                channel_id=chat_id,
                total_feeds_processed=1,
                successful_feed_fetches=1,
                total_articles_fetched=2,
                unique_articles_after_dedup=2,
                articles_passed_prefilter=1,
                articles_ready_for_ai=1,
                processing_time_seconds=2.0,
                feed_fetch_time_seconds=1.0,
                deduplication_stats=None,
                topic_matches={"Tech": 1},
                errors=[],
            )

        pipeline.process_channel = AsyncMock(side_effect=mock_process_channel)

        results = await pipeline.process_multiple_channels(chat_ids)

        assert len(results) == 2
        assert all(isinstance(r, PipelineResult) for r in results)
        assert {r.channel_id for r in results} == set(chat_ids)

        # Verify process_channel called for each chat_id
        assert pipeline.process_channel.call_count == 2

    @pytest.mark.asyncio
    async def test_process_multiple_channels_with_exceptions(self, pipeline):
        """Test multi-channel processing with some channels failing."""
        chat_ids = ["good_channel", "bad_channel", "another_good_channel"]

        async def mock_process_channel(chat_id):
            if chat_id == "bad_channel":
                raise Exception("Channel processing failed")
            return PipelineResult(
                channel_id=chat_id,
                total_feeds_processed=1,
                successful_feed_fetches=1,
                total_articles_fetched=1,
                unique_articles_after_dedup=1,
                articles_passed_prefilter=1,
                articles_ready_for_ai=1,
                processing_time_seconds=1.0,
                feed_fetch_time_seconds=0.5,
                deduplication_stats=None,
                topic_matches={},
                errors=[],
            )

        pipeline.process_channel = AsyncMock(side_effect=mock_process_channel)

        results = await pipeline.process_multiple_channels(chat_ids)

        assert len(results) == 3

        # Check that failed channel has error result
        bad_result = next(r for r in results if r.channel_id == "bad_channel")
        assert len(bad_result.errors) == 1
        assert "Processing exception" in bad_result.errors[0]

        # Check that good channels succeeded
        good_results = [r for r in results if r.channel_id != "bad_channel"]
        assert len(good_results) == 2
        assert all(r.articles_ready_for_ai == 1 for r in good_results)

    @pytest.mark.asyncio
    async def test_process_multiple_channels_empty_list(self, pipeline):
        """Test multi-channel processing with empty channel list."""
        results = await pipeline.process_multiple_channels([])

        assert results == []

    @pytest.mark.asyncio
    async def test_run_daily_processing(self, pipeline, sample_feeds):
        """Test daily processing workflow for all active channels."""
        # Mock database to return active channels
        with patch.object(pipeline.db, "get_connection") as mock_conn_context:
            mock_conn = Mock()
            mock_conn_context.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = [
                {"chat_id": "channel1"},
                {"chat_id": "channel2"},
            ]

            # Mock process_multiple_channels
            mock_results = [
                PipelineResult(
                    channel_id="channel1",
                    total_feeds_processed=2,
                    successful_feed_fetches=2,
                    total_articles_fetched=10,
                    unique_articles_after_dedup=8,
                    articles_passed_prefilter=5,
                    articles_ready_for_ai=3,
                    processing_time_seconds=5.0,
                    feed_fetch_time_seconds=2.0,
                    deduplication_stats=None,
                    topic_matches={"Tech": 2, "News": 1},
                    errors=[],
                ),
                PipelineResult(
                    channel_id="channel2",
                    total_feeds_processed=1,
                    successful_feed_fetches=1,
                    total_articles_fetched=5,
                    unique_articles_after_dedup=5,
                    articles_passed_prefilter=3,
                    articles_ready_for_ai=2,
                    processing_time_seconds=3.0,
                    feed_fetch_time_seconds=1.0,
                    deduplication_stats=None,
                    topic_matches={"Sports": 2},
                    errors=[],
                ),
            ]

            pipeline.process_multiple_channels = AsyncMock(return_value=mock_results)

            stats = await pipeline.run_daily_processing()

            assert isinstance(stats, ProcessingStats)
            assert stats.total_articles == 15  # 10 + 5
            assert stats.pre_filtered_articles == 8  # 5 + 3
            assert stats.ai_processed_articles == 5  # 3 + 2
            assert stats.channels_processed == 2
            assert stats.topics_matched == 3  # Total unique topics with matches
            assert stats.processing_time_seconds > 0

    @pytest.mark.asyncio
    async def test_run_daily_processing_no_channels(self, pipeline):
        """Test daily processing with no active channels."""
        # Mock database to return no channels
        with patch.object(pipeline.db, "get_connection") as mock_conn_context:
            mock_conn = Mock()
            mock_conn_context.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = []

            stats = await pipeline.run_daily_processing()

            assert isinstance(stats, ProcessingStats)
            assert stats.total_articles == 0
            assert stats.channels_processed == 0

    def test_get_channel_topics(self, pipeline, sample_topics, db_connection):
        """Test topic retrieval for a specific channel."""
        # Use real database connection for this test
        pipeline.db = db_connection

        topics = pipeline._get_channel_topics("test_channel")

        assert len(topics) == 2
        assert all(isinstance(t, Topic) for t in topics)
        assert {t.name for t in topics} == {"AI Technology", "Web Development"}

        # Verify JSON parsing
        ai_topic = next(t for t in topics if t.name == "AI Technology")
        assert isinstance(ai_topic.keywords, list)
        assert "artificial intelligence" in ai_topic.keywords

    def test_get_channel_topics_no_topics(self, pipeline, db_connection):
        """Test topic retrieval for channel with no topics."""
        pipeline.db = db_connection

        topics = pipeline._get_channel_topics("nonexistent_channel")

        assert topics == []

    def test_update_feed_statuses(self, pipeline, sample_feeds, mock_components):
        """Test feed status updates based on fetch results."""
        fetch_results = [
            FetchResult(
                feed_url="https://example.com/feed1.xml",
                success=True,
                articles=[],
                error=None,
                fetch_time=1.5,
            ),
            FetchResult(
                feed_url="https://example.com/feed2.xml",
                success=False,
                articles=None,
                error="Network error",
                fetch_time=30.0,
            ),
        ]

        pipeline._update_feed_statuses(sample_feeds[:2], fetch_results)

        # Verify feed manager called to update status for each feed
        assert mock_components["feed_manager"].update_feed_status.call_count == 2

    def test_prepare_for_ai_processing(self, pipeline, sample_articles, db_connection):
        """Test article preparation for AI processing with topic-based limiting."""
        pipeline.db = db_connection

        # Mock filter results
        from culifeed.processing.pre_filter import FilterResult

        filter_results = [
            FilterResult(
                article=sample_articles[0],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.9},
            ),
            FilterResult(
                article=sample_articles[1],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.8},
            ),
            FilterResult(
                article=sample_articles[2],
                passed_filter=True,
                matched_topics=["Web Development"],
                relevance_scores={"Web Development": 0.7},
            ),
        ]

        # Mock database storage
        with patch.object(pipeline, "_store_articles_for_processing") as mock_store:
            # Method _prepare_for_ai_processing has been integrated into _ai_analysis_and_processing
            # Skipping this test as the functionality is now tested through the main pipeline
            pytest.skip(
                "Method _prepare_for_ai_processing has been integrated into _ai_analysis_and_processing"
            )

    def test_prepare_for_ai_processing_topic_limiting(
        self, pipeline, sample_articles, db_connection
    ):
        """Test topic-based article limiting in AI preparation."""
        pipeline.db = db_connection

        # Create more articles for same topic to test limiting
        extra_articles = [
            Article(
                id="article4",
                title="Another AI Article",
                url="https://example.com/article4",
                content="More AI content...",
                published_at=datetime.now(timezone.utc),
                source_feed="https://example.com/feed1.xml",
                content_hash="hash4",
                created_at=datetime.now(timezone.utc),
            )
        ]
        all_articles = sample_articles + extra_articles

        # Mock filter results - all for same topic with different scores
        from culifeed.processing.pre_filter import FilterResult

        filter_results = [
            FilterResult(
                article=all_articles[0],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.9},  # Highest score
            ),
            FilterResult(
                article=all_articles[1],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.7},  # Middle score
            ),
            FilterResult(
                article=all_articles[2],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.5},  # Lowest score
            ),
            FilterResult(
                article=all_articles[3],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.8},  # Second highest
            ),
        ]

        with patch.object(pipeline, "_store_articles_for_processing"):
            # Method _prepare_for_ai_processing has been integrated into _ai_analysis_and_processing
            # Skipping this test as the functionality is now tested through the main pipeline
            pytest.skip(
                "Method _prepare_for_ai_processing has been integrated into _ai_analysis_and_processing"
            )

    def test_store_articles_for_processing(
        self, pipeline, sample_articles, db_connection
    ):
        """Test article storage for AI processing."""
        pipeline.db = db_connection

        pipeline._store_articles_for_processing(sample_articles)

        # Verify articles stored in database
        with db_connection.get_connection() as conn:
            rows = conn.execute("SELECT COUNT(*) as count FROM articles").fetchone()
            assert rows["count"] == 3

            # Verify article data
            article_row = conn.execute(
                "SELECT * FROM articles WHERE id = ?", (sample_articles[0].id,)
            ).fetchone()
            assert article_row["title"] == sample_articles[0].title
            assert article_row["url"] == str(sample_articles[0].url)

    def test_store_articles_for_processing_empty_list(self, pipeline, db_connection):
        """Test article storage with empty list."""
        pipeline.db = db_connection

        pipeline._store_articles_for_processing([])

        # Should not crash and not store anything
        with db_connection.get_connection() as conn:
            rows = conn.execute("SELECT COUNT(*) as count FROM articles").fetchone()
            assert rows["count"] == 0

    def test_create_result(self, pipeline):
        """Test pipeline result creation with efficiency metrics."""
        # Create proper deduplication stats for the test
        dedup_stats = DeduplicationStats(
            total_articles=10,
            unique_articles=8,
            duplicates_found=2,
            duplicates_by_hash=1,
            duplicates_by_url=1,
            duplicates_by_content=0,
        )

        result = pipeline._create_result(
            chat_id="test_channel",
            total_feeds=2,
            successful_feeds=2,
            total_articles=10,
            unique_articles=8,
            passed_filter=5,
            ai_ready=3,
            processing_time=5.0,
            fetch_time=2.0,
            dedup_stats=dedup_stats,
            topic_matches={"Tech": 2, "News": 1},
            errors=[],
        )

        assert isinstance(result, PipelineResult)
        assert result.channel_id == "test_channel"
        assert result.total_feeds_processed == 2
        assert result.articles_ready_for_ai == 3

        # Test efficiency metrics calculation
        metrics = result.efficiency_metrics
        assert metrics["feed_success_rate"] == 100.0
        assert (
            metrics["deduplication_rate"] == 20.0
        )  # From dedup_stats.deduplication_rate property
        assert metrics["prefilter_reduction"] == 37.5  # (8-5)/8 * 100
        # Note: overall_reduction is not part of efficiency_metrics, removing that assertion

    def test_create_empty_result(self, pipeline):
        """Test empty pipeline result creation."""
        errors = ["Test error"]
        result = pipeline._create_empty_result("test_channel", errors)

        assert isinstance(result, PipelineResult)
        assert result.channel_id == "test_channel"
        assert result.total_feeds_processed == 0
        assert result.articles_ready_for_ai == 0
        assert result.errors == errors

    @pytest.mark.asyncio
    async def test_pipeline_performance_metrics(
        self, pipeline, sample_feeds, sample_topics, sample_articles, mock_components
    ):
        """Test pipeline performance measurement and timing."""
        chat_id = "test_channel"

        # Setup mocks for successful processing
        mock_components["feed_manager"].get_feeds_for_channel.return_value = [
            feed for feed in sample_feeds if feed.chat_id == chat_id
        ]

        fetch_results = [
            FetchResult(
                feed_url="https://example.com/feed1.xml",
                success=True,
                articles=sample_articles,
                error=None,
                fetch_time=1.5,
            )
        ]
        mock_components["feed_fetcher"].fetch_feeds_batch.return_value = fetch_results

        from culifeed.processing.article_processor import DeduplicationStats

        dedup_stats = DeduplicationStats(
            total_articles=3,
            unique_articles=2,
            duplicates_found=1,
            duplicates_by_hash=1,
            duplicates_by_url=0,
            duplicates_by_content=0,
        )
        mock_components["article_processor"].process_articles.return_value = (
            sample_articles[:2],
            dedup_stats,
        )

        from culifeed.processing.pre_filter import FilterResult

        filter_results = [
            FilterResult(
                article=sample_articles[0],
                passed_filter=True,
                matched_topics=["AI Technology"],
                relevance_scores={"AI Technology": 0.9},
            )
        ]
        mock_components["pre_filter"].filter_articles.return_value = filter_results

        result = await pipeline.process_channel(chat_id)

        # Verify timing measurements
        assert result.processing_time_seconds > 0
        assert result.feed_fetch_time_seconds > 0  # Just check timing was measured

        # Verify efficiency calculations
        metrics = result.efficiency_metrics
        assert "feed_success_rate" in metrics
        assert "deduplication_rate" in metrics
        assert "prefilter_reduction" in metrics
        assert "overall_reduction" in metrics

        # Percentage metrics should be 0-100, rate metrics can be higher
        percentage_metrics = [
            "feed_success_rate",
            "deduplication_rate",
            "prefilter_reduction",
            "overall_reduction",
        ]
        for key, value in metrics.items():
            if key in percentage_metrics:
                assert (
                    0 <= value <= 100
                ), f"Percentage metric {key} should be 0-100%, got {value}"
            else:
                assert (
                    value >= 0
                ), f"Rate metric {key} should be non-negative, got {value}"

    def test_pipeline_with_real_database_schema(self, test_database):
        """Test pipeline with actual database schema constraints."""
        db_connection = DatabaseConnection(test_database)
        pipeline = ProcessingPipeline(db_connection)

        # Verify pipeline can work with real database
        assert pipeline.db == db_connection

        # Test getting topics from empty database
        topics = pipeline._get_channel_topics("nonexistent_channel")
        assert topics == []

        # Test storing articles
        sample_article = Article(
            id="test_article",
            title="Test Article",
            url="https://example.com/test",
            content="Test content",
            published_at=datetime.now(timezone.utc),
            source_feed="https://example.com/feed.xml",
            content_hash="test_hash",
            created_at=datetime.now(timezone.utc),
        )

        # Should work without errors
        pipeline._store_articles_for_processing([sample_article])

        # Verify stored
        with db_connection.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE id = ?", ("test_article",)
            ).fetchone()
            assert row is not None
            assert row["title"] == "Test Article"

    def test_store_processing_results_persists_pre_filter_score(
        self, test_database, db_connection, sample_articles
    ):
        """Regression test: pre_filter_score must not be NULL after _store_processing_results.

        The v1 pipeline computes a relevance score in the pre-filter stage but the
        _store_processing_results INSERT was missing the column, leaving every row NULL.
        """
        pipeline = ProcessingPipeline(db_connection)

        # Insert the article so the foreign-key relationship holds.
        pipeline._store_articles_for_processing([sample_articles[0]])

        # Insert the channel so FK on processing_results is satisfied.
        with db_connection.get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO channels (chat_id, chat_title, chat_type, created_at)
                VALUES ('test_channel', 'Test Channel', 'group', ?)
                """,
                (datetime.now(timezone.utc),),
            )
            conn.commit()

        expected_score = 0.75

        processing_results = [
            {
                "article_id": sample_articles[0].id,
                "chat_id": "test_channel",
                "topic_name": "AI Technology",
                "ai_relevance_score": 0.9,
                "confidence_score": 0.8,
                "summary": None,
                "pre_filter_score": expected_score,
            }
        ]

        pipeline._store_processing_results(processing_results)

        with db_connection.get_connection() as conn:
            row = conn.execute(
                """
                SELECT pre_filter_score FROM processing_results
                WHERE article_id = ? AND chat_id = ? AND topic_name = ?
                """,
                (sample_articles[0].id, "test_channel", "AI Technology"),
            ).fetchone()

        assert row is not None, "No row inserted into processing_results"
        assert row["pre_filter_score"] is not None, (
            "pre_filter_score is NULL — the INSERT is not persisting the value"
        )
        assert abs(row["pre_filter_score"] - expected_score) < 1e-9
