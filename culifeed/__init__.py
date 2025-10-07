"""
CuliFeed - Smart Content Curation System
========================================

AI-powered RSS content curation with intelligent filtering and Telegram delivery.

Main Components:
- Database: SQLite with connection pooling and schema management
- Configuration: YAML + environment variables with Pydantic validation
- Content Processing: RSS parsing, pre-filtering, AI analysis
- Telegram Bot: Multi-channel support with auto-registration
- AI Integration: Gemini/Groq/OpenAI with fallback chains
"""

__version__ = "1.4.2"
__author__ = "CuliFeed Development Team"
__description__ = "AI-powered RSS content curation system"

# Core imports for easy access
from .config.settings import get_settings
from .database.connection import get_db_manager
from .database.schema import DatabaseSchema
from .utils.logging import configure_application_logging, get_logger_for_component
from .utils.exceptions import CuliFeedError

__all__ = [
    "get_settings",
    "get_db_manager",
    "DatabaseSchema",
    "configure_application_logging",
    "get_logger_for_component",
    "CuliFeedError",
]
