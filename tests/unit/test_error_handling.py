#!/usr/bin/env python3
"""
Error Handling Tests for CuliFeed
================================

Tests for error classification, retry logic, circuit breakers,
and recovery mechanisms.
"""

import asyncio
import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import time

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from culifeed.recovery.error_handler import (
    ErrorHandler,
    ErrorContext,
    ErrorEvent,
    ErrorClassifier,
    ErrorCategory,
    ErrorSeverity,
)
from culifeed.recovery.retry_logic import (
    RetryManager,
    RetryConfig,
    RetryStrategy,
    CircuitBreaker,
    CircuitState,
    with_retry,
)
from culifeed.utils.exceptions import CuliFeedError


class TestErrorClassifier:
    """Test error classification functionality."""

    def test_classify_network_errors(self):
        """Test classification of network-related errors."""
        context = ErrorContext(component="test_component", operation="test_op")

        # Connection error
        conn_error = ConnectionError("Connection failed")
        category, severity = ErrorClassifier.classify(conn_error, context)
        assert category == ErrorCategory.NETWORK
        assert severity == ErrorSeverity.MEDIUM

        # Timeout error
        timeout_error = TimeoutError("Request timed out")
        category, severity = ErrorClassifier.classify(timeout_error, context)
        assert category == ErrorCategory.NETWORK
        assert severity == ErrorSeverity.LOW

    def test_classify_database_errors(self):
        """Test classification of database-related errors."""
        context = ErrorContext(component="database", operation="query")

        # Generic database error
        class DatabaseError(Exception):
            pass

        db_error = DatabaseError("Database connection failed")
        category, severity = ErrorClassifier.classify(db_error, context)
        assert category == ErrorCategory.DATABASE
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_by_message_patterns(self):
        """Test classification based on error message patterns."""
        context = ErrorContext(component="feed_processor", operation="parse")

        # RSS feed parsing error
        feed_error = ValueError("Invalid RSS feed format")
        category, severity = ErrorClassifier.classify(feed_error, context)
        assert category == ErrorCategory.FEED

        # Telegram bot error
        bot_error = Exception("Telegram bot token invalid")
        category, severity = ErrorClassifier.classify(bot_error, context)
        assert category == ErrorCategory.TELEGRAM

    def test_severity_classification(self):
        """Test severity level classification."""
        context = ErrorContext(component="test", operation="test")

        # Memory error should be critical
        memory_error = MemoryError("Out of memory")
        category, severity = ErrorClassifier.classify(memory_error, context)
        assert severity == ErrorSeverity.CRITICAL

        # Permission error should be critical
        perm_error = PermissionError("Permission denied")
        category, severity = ErrorClassifier.classify(perm_error, context)
        assert severity == ErrorSeverity.CRITICAL


class TestErrorHandler:
    """Test error handler functionality."""

    @pytest.fixture
    def error_handler(self):
        """Create error handler for testing."""
        return ErrorHandler()

    def test_error_event_creation(self, error_handler):
        """Test error event creation and handling."""
        context = ErrorContext(
            component="test_component",
            operation="test_operation",
            channel_id="test_channel",
        )

        exception = ValueError("Test error")
        error_event = error_handler.handle_error(
            exception, context, attempt_recovery=False
        )

        assert error_event.message == "Test error"
        assert error_event.category == ErrorCategory.PROCESSING
        assert error_event.context.component == "test_component"
        assert error_event.context.channel_id == "test_channel"
        assert not error_event.recovery_attempted

    def test_error_storage_and_retrieval(self, error_handler):
        """Test error storage and statistics retrieval."""
        context = ErrorContext(component="test", operation="test")

        # Create several errors
        for i in range(5):
            exception = ValueError(f"Test error {i}")
            error_handler.handle_error(exception, context, attempt_recovery=False)

        # Check statistics
        stats = error_handler.get_error_statistics(hours=1)
        assert stats["total_errors"] == 5
        assert "processing" in stats["by_category"]
        assert stats["by_category"]["processing"] == 5

    def test_recovery_handler_registration(self, error_handler):
        """Test recovery handler registration and execution."""
        recovery_called = False

        def test_recovery_handler(error_event):
            nonlocal recovery_called
            recovery_called = True
            return True

        # Register recovery handler
        error_handler.register_recovery_handler(
            ErrorCategory.NETWORK, test_recovery_handler
        )

        # Create network error
        context = ErrorContext(component="test", operation="test")
        network_error = ConnectionError("Network error")

        error_event = error_handler.handle_error(
            network_error, context, attempt_recovery=True
        )

        assert recovery_called
        assert error_event.recovery_attempted
        assert error_event.recovery_successful

    def test_error_rate_limiting(self, error_handler):
        """Test error rate limiting for alerts."""
        context = ErrorContext(component="test", operation="test")

        # Create multiple high-severity errors quickly
        for i in range(3):
            critical_error = MemoryError(f"Critical error {i}")
            error_handler.handle_error(critical_error, context, attempt_recovery=False)
            time.sleep(0.1)  # Small delay to avoid identical timestamps

        # Check that errors are recorded but alerts are rate-limited
        stats = error_handler.get_error_statistics(hours=1)
        assert stats["total_errors"] == 3

    def test_error_cleanup(self, error_handler):
        """Test cleanup of resolved errors."""
        context = ErrorContext(component="test", operation="test")

        # Create an error and mark it as resolved
        exception = ValueError("Test error")
        error_event = error_handler.handle_error(
            exception, context, attempt_recovery=False
        )
        error_event.resolved_at = datetime.now() - timedelta(
            hours=25
        )  # Old resolved error

        # Clean up old resolved errors
        cleaned = error_handler.clear_resolved_errors(older_than_hours=24)

        # Should have cleaned the old resolved error
        assert cleaned == 1


class TestRetryLogic:
    """Test retry logic and circuit breaker functionality."""

    @pytest.fixture
    def retry_manager(self):
        """Create retry manager for testing."""
        config = RetryConfig(
            max_attempts=3,
            base_delay=0.1,  # Short delay for tests
            strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
        )
        return RetryManager(config)

    def test_successful_retry(self, retry_manager):
        """Test successful function execution with retries."""
        call_count = 0

        def test_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"

        result = retry_manager.retry_sync(test_function)

        assert result == "success"
        assert call_count == 3  # Should have retried twice

    @pytest.mark.asyncio
    async def test_async_retry(self, retry_manager):
        """Test async function retries."""
        call_count = 0

        async def async_test_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("Timeout")
            return "async_success"

        result = await retry_manager.retry_async(async_test_function)

        assert result == "async_success"
        assert call_count == 2

    def test_retry_exhaustion(self, retry_manager):
        """Test behavior when all retries are exhausted."""

        def failing_function():
            raise ConnectionError("Always fails")

        with pytest.raises(ConnectionError, match="Always fails"):
            retry_manager.retry_sync(failing_function)

    def test_non_retryable_error(self, retry_manager):
        """Test that non-retryable errors are not retried."""
        call_count = 0

        def test_function():
            nonlocal call_count
            call_count += 1
            raise KeyError("Non-retryable error")

        with pytest.raises(KeyError):
            retry_manager.retry_sync(test_function)

        # Should only be called once (no retries)
        assert call_count == 1

    def test_circuit_breaker_opening(self, retry_manager):
        """Test circuit breaker opening after failures."""
        config = RetryConfig(
            max_attempts=1, circuit_failure_threshold=2  # Don't retry, fail fast
        )
        retry_manager.config = config

        def failing_function():
            raise ConnectionError("Circuit breaker test")

        # First two calls should fail and open the circuit
        with pytest.raises(ConnectionError):
            retry_manager.retry_sync(
                failing_function, circuit_breaker_key="test_circuit"
            )

        with pytest.raises(ConnectionError):
            retry_manager.retry_sync(
                failing_function, circuit_breaker_key="test_circuit"
            )

        # Third call should be blocked by circuit breaker
        with pytest.raises(Exception, match="Circuit breaker is OPEN"):
            retry_manager.retry_sync(
                failing_function, circuit_breaker_key="test_circuit"
            )

    def test_circuit_breaker_recovery(self, retry_manager):
        """Test circuit breaker recovery after timeout."""
        config = RetryConfig(
            max_attempts=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout=0.1,  # Short timeout for test
        )
        retry_manager.config = config

        call_count = 0

        def test_function():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise ConnectionError("Initial failure")
            return "recovered"

        # First call fails and opens circuit
        with pytest.raises(ConnectionError):
            retry_manager.retry_sync(test_function, circuit_breaker_key="recovery_test")

        # Wait for recovery timeout (0.11s to ensure 0.1s timeout passes)
        time.sleep(0.11)

        # Should allow execution and succeed
        result = retry_manager.retry_sync(
            test_function, circuit_breaker_key="recovery_test"
        )
        assert result == "recovered"

    def test_retry_statistics(self, retry_manager):
        """Test retry statistics tracking."""

        def test_function():
            return "success"

        # Execute function multiple times
        for _ in range(5):
            retry_manager.retry_sync(test_function)

        stats = retry_manager.get_retry_statistics()

        assert stats["total_attempts"] == 5
        assert stats["total_successes"] == 5
        assert stats["total_failures"] == 0
        assert stats["success_rate"] == 100.0

    def test_delay_strategies(self):
        """Test different retry delay strategies."""
        config = RetryConfig(base_delay=1.0)
        retry_manager = RetryManager(config)

        # Test fixed delay
        config.strategy = RetryStrategy.FIXED_DELAY
        delay1 = retry_manager._calculate_delay(1, config)
        delay2 = retry_manager._calculate_delay(2, config)
        assert delay1 == delay2 == 1.0

        # Test exponential backoff
        config.strategy = RetryStrategy.EXPONENTIAL_BACKOFF
        delay1 = retry_manager._calculate_delay(1, config)
        delay2 = retry_manager._calculate_delay(2, config)
        assert delay2 > delay1

        # Test linear backoff
        config.strategy = RetryStrategy.LINEAR_BACKOFF
        delay1 = retry_manager._calculate_delay(1, config)
        delay2 = retry_manager._calculate_delay(2, config)
        assert delay2 == delay1 * 2


class TestRetryDecorator:
    """Test retry decorator functionality."""

    def test_sync_function_decorator(self):
        """Test decorator on synchronous functions."""
        config = RetryConfig(max_attempts=3, base_delay=0.01)

        call_count = 0

        @with_retry(config=config)
        def decorated_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Retry me")
            return "decorated_success"

        result = decorated_function()

        assert result == "decorated_success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_function_decorator(self):
        """Test decorator on async functions."""
        config = RetryConfig(max_attempts=3, base_delay=0.01)

        call_count = 0

        @with_retry(config=config)
        async def decorated_async_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("Async retry me")
            return "async_decorated_success"

        result = await decorated_async_function()

        assert result == "async_decorated_success"
        assert call_count == 3


class TestIntegrationScenarios:
    """Test error handling in integration scenarios."""

    @pytest.mark.asyncio
    async def test_feed_processing_error_recovery(self):
        """Test error recovery in feed processing scenario."""
        error_handler = ErrorHandler()
        retry_manager = RetryManager(error_handler=error_handler)

        feed_processing_attempts = 0

        async def simulate_feed_processing(feed_url):
            nonlocal feed_processing_attempts
            feed_processing_attempts += 1

            context = ErrorContext(
                component="feed_processor", operation="parse_feed", feed_url=feed_url
            )

            if feed_processing_attempts < 3:
                # Simulate temporary network error
                raise ConnectionError("RSS feed temporarily unavailable")

            return {"articles": [{"title": "Test Article", "url": "http://test.com"}]}

        # Process with retry
        result = await retry_manager.retry_async(
            simulate_feed_processing,
            "https://example.com/feed.xml",
            context=ErrorContext(component="feed_processor", operation="process_feed"),
        )

        assert result["articles"][0]["title"] == "Test Article"
        assert feed_processing_attempts == 3

        # Check error handler recorded the attempts
        stats = error_handler.get_error_statistics(hours=1)
        assert stats["total_errors"] >= 2  # Should have recorded the failures

    def test_database_connection_recovery(self):
        """Test database connection error recovery."""
        error_handler = ErrorHandler()

        # Register recovery handler for database errors
        def database_recovery_handler(error_event):
            # Simulate connection pool reset
            return True

        error_handler.register_recovery_handler(
            ErrorCategory.DATABASE, database_recovery_handler
        )

        # Simulate database connection error
        context = ErrorContext(component="database_manager", operation="get_connection")

        db_error = ConnectionError("Database connection pool exhausted")
        error_event = error_handler.handle_error(
            db_error, context, attempt_recovery=True
        )

        assert error_event.recovery_attempted
        assert error_event.recovery_successful

    def test_telegram_bot_rate_limit_handling(self):
        """Test Telegram bot rate limit error handling."""
        config = RetryConfig(
            max_attempts=5,
            base_delay=1.0,
            strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
            retry_on_status_codes=(429, 500, 502, 503, 504),
        )

        retry_manager = RetryManager(config)

        attempt_count = 0

        def simulate_telegram_api_call():
            nonlocal attempt_count
            attempt_count += 1

            if attempt_count < 4:
                # Simulate rate limit error
                error = Exception("Too Many Requests")
                error.response = Mock()
                error.response.status_code = 429
                raise error

            return {"ok": True, "result": {"message_id": 123}}

        result = retry_manager.retry_sync(simulate_telegram_api_call)

        assert result["ok"] is True
        assert attempt_count == 4


def test_new_error_codes_exist():
    from culifeed.utils.exceptions import ErrorCode
    assert ErrorCode.AI_EMBEDDING_ERROR.value == "A011"
    assert ErrorCode.VECTOR_STORE_UNAVAILABLE.value == "D007"
    assert ErrorCode.CONTENT_EMPTY.value == "P005"


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
