"""
CuliFeed Custom Exceptions
=========================

Custom exception hierarchy for CuliFeed application with proper error codes,
context information, and user-friendly error messages.
"""

from typing import Optional, Dict, Any
from enum import Enum


class ErrorCode(str, Enum):
    """Error codes for categorizing exceptions."""

    # Configuration errors (C001-C099)
    CONFIG_INVALID = "C001"
    CONFIG_MISSING = "C002"
    CONFIG_PARSE_ERROR = "C003"

    # Database errors (D001-D099)
    DATABASE_CONNECTION = "D001"
    DATABASE_SCHEMA = "D002"
    DATABASE_CONSTRAINT = "D003"
    DATABASE_TRANSACTION = "D004"
    DATABASE_CORRUPTION = "D005"
    DATABASE_ERROR = "D006"
    VECTOR_STORE_UNAVAILABLE = "D007"

    # Feed ingestion errors (F001-F099)
    FEED_INVALID_URL = "F001"
    FEED_FETCH_TIMEOUT = "F002"
    FEED_PARSE_ERROR = "F003"
    FEED_NETWORK_ERROR = "F004"
    FEED_ACCESS_DENIED = "F005"
    FEED_NOT_FOUND = "F006"

    # Content processing errors (P001-P099)
    CONTENT_INVALID = "P001"
    CONTENT_TOO_LARGE = "P002"
    CONTENT_EXTRACTION_FAILED = "P003"
    PRE_FILTER_ERROR = "P004"
    CONTENT_EMPTY = "P005"

    # AI processing errors (A001-A099)
    AI_API_ERROR = "A001"
    AI_QUOTA_EXCEEDED = "A002"
    AI_INVALID_RESPONSE = "A003"
    AI_TIMEOUT = "A004"
    AI_AUTHENTICATION = "A005"
    AI_RATE_LIMIT = "A006"
    AI_PROCESSING_ERROR = "A007"
    AI_PROVIDER_UNAVAILABLE = "A008"
    AI_INVALID_CREDENTIALS = "A009"
    AI_CONNECTION_ERROR = "A010"
    AI_EMBEDDING_ERROR = "A011"

    # Telegram bot errors (T001-T099)
    TELEGRAM_API_ERROR = "T001"
    TELEGRAM_INVALID_CHAT = "T002"
    TELEGRAM_MESSAGE_TOO_LONG = "T003"
    TELEGRAM_PERMISSION_DENIED = "T004"
    TELEGRAM_NETWORK_ERROR = "T005"

    # Delivery errors (L001-L099)
    DELIVERY_FAILED = "L001"
    DELIVERY_CHANNEL_INACTIVE = "L002"
    DELIVERY_MESSAGE_REJECTED = "L003"
    DELIVERY_TIMEOUT = "L004"

    # Validation errors (V001-V099)
    VALIDATION_REQUIRED_FIELD = "V001"
    VALIDATION_INVALID_FORMAT = "V002"
    VALIDATION_OUT_OF_RANGE = "V003"
    VALIDATION_DUPLICATE = "V004"

    # Resource management errors (R001-R099)
    DUPLICATE_RESOURCE = "R001"
    RESOURCE_NOT_FOUND = "R002"
    RESOURCE_EXHAUSTED = "R003"

    # External service errors (E001-E099)
    EXTERNAL_SERVICE_ERROR = "E001"
    EXTERNAL_SERVICE_UNAVAILABLE = "E002"
    EXTERNAL_SERVICE_TIMEOUT = "E003"

    # System errors (S001-S099)
    SYSTEM_RESOURCE_EXHAUSTED = "S001"
    SYSTEM_PERMISSION_DENIED = "S002"
    SYSTEM_DISK_FULL = "S003"
    SYSTEM_MEMORY_ERROR = "S004"


class CuliFeedError(Exception):
    """Base exception for all CuliFeed errors."""

    def __init__(
        self,
        message: str,
        error_code: Optional[ErrorCode] = None,
        context: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None,
        recoverable: bool = False,
    ):
        """Initialize CuliFeed error.

        Args:
            message: Technical error message for logging
            error_code: Categorized error code
            context: Additional context information
            user_message: User-friendly error message
            recoverable: Whether the error is recoverable
        """
        super().__init__(message)
        self.error_code = error_code
        self.context = context or {}
        self.user_message = user_message or message
        self.recoverable = recoverable

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/serialization."""
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": str(self),
            "user_message": self.user_message,
            "context": self.context,
            "recoverable": self.recoverable,
        }

    def __str__(self) -> str:
        """String representation with error code."""
        if self.error_code:
            return f"[{self.error_code.value}] {super().__str__()}"
        return super().__str__()


class ConfigurationError(CuliFeedError):
    """Configuration-related errors."""

    def __init__(self, message: str, config_key: Optional[str] = None, **kwargs):
        """Initialize configuration error.

        Args:
            message: Error message
            config_key: Configuration key that caused the error
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if config_key:
            context["config_key"] = config_key

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.CONFIG_INVALID),
            context=context,
            user_message=kwargs.get("user_message", f"Configuration error: {message}"),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message"]
            },
        )


class DatabaseError(CuliFeedError):
    """Database-related errors."""

    def __init__(self, message: str, query: Optional[str] = None, **kwargs):
        """Initialize database error.

        Args:
            message: Error message
            query: SQL query that caused the error
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if query:
            context["query"] = query

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.DATABASE_CONNECTION),
            context=context,
            user_message=kwargs.get("user_message", "Database operation failed"),
            recoverable=kwargs.get("recoverable", True),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


class FeedError(CuliFeedError):
    """Feed ingestion and parsing errors."""

    def __init__(self, message: str, feed_url: Optional[str] = None, **kwargs):
        """Initialize feed error.

        Args:
            message: Error message
            feed_url: Feed URL that caused the error
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if feed_url:
            context["feed_url"] = feed_url

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.FEED_FETCH_TIMEOUT),
            context=context,
            user_message=kwargs.get(
                "user_message", f"Feed processing failed: {message}"
            ),
            recoverable=kwargs.get("recoverable", True),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


class ProcessingError(CuliFeedError):
    """Content processing errors."""

    def __init__(self, message: str, article_id: Optional[str] = None, **kwargs):
        """Initialize processing error.

        Args:
            message: Error message
            article_id: Article ID that caused the error
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if article_id:
            context["article_id"] = article_id

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.CONTENT_INVALID),
            context=context,
            user_message=kwargs.get("user_message", "Article processing failed"),
            recoverable=kwargs.get("recoverable", True),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


class AIError(CuliFeedError):
    """AI processing and API errors."""

    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        api_call_cost: Optional[float] = None,
        **kwargs,
    ):
        """Initialize AI error.

        Args:
            message: Error message
            provider: AI provider name (e.g., 'gemini', 'groq')
            api_call_cost: Cost of the failed API call
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if provider:
            context["ai_provider"] = provider
        if api_call_cost:
            context["api_call_cost"] = api_call_cost

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.AI_API_ERROR),
            context=context,
            user_message=kwargs.get(
                "user_message", "AI processing temporarily unavailable"
            ),
            recoverable=kwargs.get("recoverable", True),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


class TelegramError(CuliFeedError):
    """Telegram bot API errors."""

    def __init__(self, message: str, chat_id: Optional[str] = None, **kwargs):
        """Initialize Telegram error.

        Args:
            message: Error message
            chat_id: Chat ID that caused the error
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if chat_id:
            context["chat_id"] = chat_id

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.TELEGRAM_API_ERROR),
            context=context,
            user_message=kwargs.get("user_message", "Telegram operation failed"),
            recoverable=kwargs.get("recoverable", True),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


class DeliveryError(CuliFeedError):
    """Content delivery errors."""

    def __init__(
        self,
        message: str,
        chat_id: Optional[str] = None,
        article_count: Optional[int] = None,
        **kwargs,
    ):
        """Initialize delivery error.

        Args:
            message: Error message
            chat_id: Chat ID where delivery failed
            article_count: Number of articles that failed to deliver
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if chat_id:
            context["chat_id"] = chat_id
        if article_count:
            context["article_count"] = article_count

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.DELIVERY_FAILED),
            context=context,
            user_message=kwargs.get("user_message", "Content delivery failed"),
            recoverable=kwargs.get("recoverable", True),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


class ValidationError(CuliFeedError):
    """Data validation errors."""

    def __init__(self, message: str, field_name: Optional[str] = None, **kwargs):
        """Initialize validation error.

        Args:
            message: Error message
            field_name: Field name that failed validation
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if field_name:
            context["field_name"] = field_name

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.VALIDATION_INVALID_FORMAT),
            context=context,
            user_message=kwargs.get(
                "user_message", f"Invalid {field_name or 'input'}: {message}"
            ),
            recoverable=kwargs.get("recoverable", False),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message", "recoverable"]
            },
        )


# Specific processing exception types


class FeedFetchError(FeedError):
    """RSS feed fetching errors."""

    pass


class FeedManagementError(CuliFeedError):
    """Feed management and lifecycle errors."""

    def __init__(self, message: str, feed_id: Optional[int] = None, **kwargs):
        """Initialize feed management error.

        Args:
            message: Error message
            feed_id: Feed ID that caused the error
            **kwargs: Additional arguments for CuliFeedError
        """
        context = kwargs.get("context", {})
        if feed_id:
            context["feed_id"] = feed_id

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.FEED_INVALID_URL),
            context=context,
            user_message=kwargs.get("user_message", "Feed management operation failed"),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message"]
            },
        )


class ContentValidationError(ProcessingError):
    """Content validation and sanitization errors."""

    def __init__(self, message: str, content_type: Optional[str] = None, **kwargs):
        """Initialize content validation error.

        Args:
            message: Error message
            content_type: Type of content that failed validation
            **kwargs: Additional arguments for ProcessingError
        """
        context = kwargs.get("context", {})
        if content_type:
            context["content_type"] = content_type

        super().__init__(
            message=message,
            error_code=kwargs.get("error_code", ErrorCode.CONTENT_INVALID),
            context=context,
            user_message=kwargs.get(
                "user_message", f"Content validation failed: {message}"
            ),
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["context", "error_code", "user_message"]
            },
        )


# Exception handling utilities


def handle_exception(
    exception: Exception,
    logger,
    operation: str,
    context: Optional[Dict[str, Any]] = None,
) -> CuliFeedError:
    """Convert generic exceptions to CuliFeed exceptions with proper logging.

    Args:
        exception: Original exception
        logger: Logger instance for error logging
        operation: Operation that was being performed
        context: Additional context information

    Returns:
        CuliFeed exception with proper categorization
    """
    context = context or {}
    context["operation"] = operation
    context["original_exception_type"] = type(exception).__name__

    # Map common exceptions to CuliFeed exceptions
    if isinstance(exception, CuliFeedError):
        # Already a CuliFeed exception
        logger.error(f"Operation '{operation}' failed", extra=exception.to_dict())
        return exception

    elif isinstance(exception, (ConnectionError, TimeoutError)):
        error = CuliFeedError(
            message=f"Network error during {operation}: {str(exception)}",
            error_code=ErrorCode.FEED_NETWORK_ERROR,
            context=context,
            user_message="Network connection failed",
            recoverable=True,
        )

    elif isinstance(exception, PermissionError):
        error = CuliFeedError(
            message=f"Permission denied during {operation}: {str(exception)}",
            error_code=ErrorCode.SYSTEM_PERMISSION_DENIED,
            context=context,
            user_message="Access denied",
            recoverable=False,
        )

    elif isinstance(exception, FileNotFoundError):
        error = ConfigurationError(
            message=f"Required file not found during {operation}: {str(exception)}",
            error_code=ErrorCode.CONFIG_MISSING,
            context=context,
            user_message="Configuration file missing",
        )

    elif isinstance(exception, MemoryError):
        error = CuliFeedError(
            message=f"Memory exhausted during {operation}: {str(exception)}",
            error_code=ErrorCode.SYSTEM_MEMORY_ERROR,
            context=context,
            user_message="System resources exhausted",
            recoverable=True,
        )

    else:
        # Generic exception
        error = CuliFeedError(
            message=f"Unexpected error during {operation}: {str(exception)}",
            context=context,
            user_message="An unexpected error occurred",
            recoverable=True,
        )

    # Log the converted exception
    logger.error(f"Operation '{operation}' failed", extra=error.to_dict())
    return error


def is_retryable_error(exception: CuliFeedError) -> bool:
    """Check if an error is worth retrying.

    Args:
        exception: CuliFeed exception to check

    Returns:
        True if the error is potentially retryable
    """
    if not exception.recoverable:
        return False

    # Network and temporary errors are retryable
    retryable_codes = {
        ErrorCode.FEED_NETWORK_ERROR,
        ErrorCode.FEED_FETCH_TIMEOUT,
        ErrorCode.AI_TIMEOUT,
        ErrorCode.AI_RATE_LIMIT,
        ErrorCode.TELEGRAM_NETWORK_ERROR,
        ErrorCode.DELIVERY_TIMEOUT,
        ErrorCode.DATABASE_CONNECTION,
        ErrorCode.SYSTEM_RESOURCE_EXHAUSTED,
    }

    return exception.error_code in retryable_codes


def get_user_friendly_message(exception: Exception) -> str:
    """Get user-friendly error message for any exception.

    Args:
        exception: Exception to get message for

    Returns:
        User-friendly error message
    """
    if isinstance(exception, CuliFeedError):
        return exception.user_message

    # Fallback for non-CuliFeed exceptions
    return "An unexpected error occurred. Please try again later."
