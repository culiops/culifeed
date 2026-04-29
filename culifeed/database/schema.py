"""
CuliFeed Database Schema
========================

SQLite database schema implementation with proper foreign key constraints
and indexes for optimal performance.

Based on the architecture documentation, this creates the core tables:
- channels: Auto-registered Telegram channels/groups
- articles: RSS article storage with deduplication
- topics: User-defined topics per channel
- feeds: RSS feed sources per channel
- processing_results: AI analysis results and delivery tracking
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional

import sqlite_vec

logger = logging.getLogger(__name__)


class DatabaseSchema:
    """Database schema manager for CuliFeed SQLite database."""

    def __init__(self, db_path: str = "data/culifeed.db"):
        """Initialize database schema manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)

    def create_tables(self) -> None:
        """Create all database tables with proper schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            conn.execute("PRAGMA foreign_keys = ON")

            # Create tables in dependency order
            self._create_channels_table(conn)
            self._create_articles_table(conn)
            self._create_user_subscriptions_table(conn)
            self._create_topics_table(conn)
            self._create_feeds_table(conn)
            self._create_processing_results_table(conn)
            self._create_vector_tables(conn)

            # Run migrations for existing databases
            self._run_migrations(conn)

            # Create indexes for performance
            self._create_indexes(conn)

            conn.commit()
            logger.info("Database schema created successfully")

    def _create_channels_table(self, conn: sqlite3.Connection) -> None:
        """Create channels table for auto-registered Telegram groups."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channels (
                chat_id TEXT PRIMARY KEY,
                chat_title TEXT NOT NULL,
                chat_type TEXT NOT NULL CHECK (chat_type IN ('private', 'group', 'supergroup', 'channel')),
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE,
                last_delivery_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

    def _create_articles_table(self, conn: sqlite3.Connection) -> None:
        """Create articles table for RSS content storage."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                content TEXT,
                published_at TIMESTAMP,
                source_feed TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                summary TEXT,
                ai_relevance_score REAL CHECK (ai_relevance_score BETWEEN 0.0 AND 1.0),
                ai_confidence REAL CHECK (ai_confidence BETWEEN 0.0 AND 1.0),
                ai_provider TEXT,
                ai_reasoning TEXT
            )
        """
        )

    def _create_user_subscriptions_table(self, conn: sqlite3.Connection) -> None:
        """Create user_subscriptions table for SaaS billing and limits."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                telegram_user_id INTEGER PRIMARY KEY,
                subscription_tier TEXT DEFAULT 'free' CHECK (subscription_tier IN ('free', 'pro')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

    def _create_topics_table(self, conn: sqlite3.Connection) -> None:
        """Create topics table for user-defined content topics per channel with user ownership."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                name TEXT NOT NULL,
                keywords TEXT NOT NULL,  -- JSON array of keywords
                exclude_keywords TEXT DEFAULT '[]',  -- JSON array of exclusion keywords
                confidence_threshold REAL DEFAULT 0.6 CHECK (confidence_threshold BETWEEN 0.0 AND 1.0),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_match_at TIMESTAMP,
                active BOOLEAN DEFAULT TRUE,
                telegram_user_id INTEGER,  -- NEW: User ownership for SaaS pricing
                description TEXT,
                embedding_signature TEXT,
                embedding_updated_at TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES channels(chat_id) ON DELETE CASCADE,
                FOREIGN KEY (telegram_user_id) REFERENCES user_subscriptions(telegram_user_id) ON DELETE SET NULL,
                UNIQUE(chat_id, name)
            )
        """
        )

    def _create_feeds_table(self, conn: sqlite3.Connection) -> None:
        """Create feeds table for RSS feed sources per channel."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                description TEXT,
                last_fetched_at TIMESTAMP,
                last_success_at TIMESTAMP,
                error_count INTEGER DEFAULT 0,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES channels(chat_id) ON DELETE CASCADE,
                UNIQUE(chat_id, url)
            )
        """
        )

    def _create_processing_results_table(self, conn: sqlite3.Connection) -> None:
        """Create processing results table for AI analysis and delivery tracking."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                pre_filter_score REAL,
                ai_relevance_score REAL,
                confidence_score REAL,
                summary TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delivered BOOLEAN DEFAULT FALSE,
                delivery_error TEXT,
                embedding_score REAL,
                embedding_top_topics TEXT,
                llm_decision TEXT,
                llm_reasoning TEXT,
                pipeline_version TEXT DEFAULT 'v1',
                FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES channels(chat_id) ON DELETE CASCADE
            )
        """
        )

    def _create_vector_tables(self, conn: sqlite3.Connection) -> None:
        """Create sqlite-vec virtual tables for v2 embedding pipeline."""
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS topic_embeddings USING vec0(
                topic_id INTEGER PRIMARY KEY,
                embedding FLOAT[1536]
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS article_embeddings USING vec0(
                article_id TEXT PRIMARY KEY,
                embedding FLOAT[1536]
            )
        """)

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        """Create database indexes for optimal query performance."""
        indexes = [
            # Channel indexes
            "CREATE INDEX IF NOT EXISTS idx_channels_active ON channels(active)",
            "CREATE INDEX IF NOT EXISTS idx_channels_type ON channels(chat_type)",
            # Article indexes
            "CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at)",
            "CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_feed)",
            "CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash)",
            "CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at)",
            # Topic indexes
            "CREATE INDEX IF NOT EXISTS idx_topics_chat_active ON topics(chat_id, active)",
            "CREATE INDEX IF NOT EXISTS idx_topics_last_match ON topics(last_match_at)",
            "CREATE INDEX IF NOT EXISTS idx_topics_user_id ON topics(telegram_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_topics_user_active ON topics(telegram_user_id, active)",
            # User subscription indexes
            "CREATE INDEX IF NOT EXISTS idx_user_subscriptions_tier ON user_subscriptions(subscription_tier)",
            # Feed indexes
            "CREATE INDEX IF NOT EXISTS idx_feeds_chat_active ON feeds(chat_id, active)",
            "CREATE INDEX IF NOT EXISTS idx_feeds_last_success ON feeds(last_success_at)",
            "CREATE INDEX IF NOT EXISTS idx_feeds_error_count ON feeds(error_count)",
            # Processing result indexes
            "CREATE INDEX IF NOT EXISTS idx_processing_chat_delivered ON processing_results(chat_id, delivered)",
            "CREATE INDEX IF NOT EXISTS idx_processing_processed_at ON processing_results(processed_at)",
            "CREATE INDEX IF NOT EXISTS idx_processing_confidence ON processing_results(confidence_score)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_unique_v2 ON processing_results(article_id, chat_id, topic_name, pipeline_version)",
        ]

        for index_sql in indexes:
            conn.execute(index_sql)

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Run database migrations for existing databases."""
        # Check if validation columns exist and add them if not
        cursor = conn.execute("PRAGMA table_info(articles)")
        columns = [column[1] for column in cursor.fetchall()]

        # Migration 1: Add telegram_user_id to topics table for SaaS pricing
        cursor = conn.execute("PRAGMA table_info(topics)")
        topic_columns = [column[1] for column in cursor.fetchall()]

        if "telegram_user_id" not in topic_columns:
            logger.info("Adding telegram_user_id column to topics table")
            conn.execute("ALTER TABLE topics ADD COLUMN telegram_user_id INTEGER")
            # Note: Foreign key constraint will be added in next migration if needed

        # Migration 2: Add description + embedding metadata to topics (v2 pipeline)
        cursor = conn.execute("PRAGMA table_info(topics)")
        topic_columns = [column[1] for column in cursor.fetchall()]
        if "description" not in topic_columns:
            logger.info("Adding description column to topics table")
            conn.execute("ALTER TABLE topics ADD COLUMN description TEXT")
        if "embedding_signature" not in topic_columns:
            logger.info("Adding embedding_signature column to topics table")
            conn.execute("ALTER TABLE topics ADD COLUMN embedding_signature TEXT")
        if "embedding_updated_at" not in topic_columns:
            logger.info("Adding embedding_updated_at column to topics table")
            conn.execute("ALTER TABLE topics ADD COLUMN embedding_updated_at TIMESTAMP")

        # Migration 3: processing_results v2 columns + widen UNIQUE to include pipeline_version
        cursor = conn.execute("PRAGMA table_info(processing_results)")
        pr_columns = [column[1] for column in cursor.fetchall()]
        if "pipeline_version" not in pr_columns:
            logger.info("Migrating processing_results to v2 schema (rebuild required)")
            # The original table has an inline UNIQUE(article_id, chat_id, topic_name) auto-index
            # which we cannot DROP directly; rebuild the table to widen the constraint.
            conn.executescript("""
                CREATE TABLE processing_results_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    pre_filter_score REAL,
                    ai_relevance_score REAL,
                    confidence_score REAL,
                    summary TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    delivered BOOLEAN DEFAULT FALSE,
                    delivery_error TEXT,
                    embedding_score REAL,
                    embedding_top_topics TEXT,
                    llm_decision TEXT,
                    llm_reasoning TEXT,
                    pipeline_version TEXT DEFAULT 'v1',
                    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
                    FOREIGN KEY (chat_id) REFERENCES channels(chat_id) ON DELETE CASCADE
                );

                INSERT INTO processing_results_new (
                    id, article_id, chat_id, topic_name, pre_filter_score,
                    ai_relevance_score, confidence_score, summary, processed_at,
                    delivered, delivery_error
                )
                SELECT id, article_id, chat_id, topic_name, pre_filter_score,
                    ai_relevance_score, confidence_score, summary, processed_at,
                    delivered, delivery_error
                FROM processing_results;

                DROP TABLE processing_results;
                ALTER TABLE processing_results_new RENAME TO processing_results;
            """)

    def drop_tables(self) -> None:
        """Drop all tables (for testing/reset purposes)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            # Drop in reverse dependency order; virtual tables first
            tables = [
                "topic_embeddings",
                "article_embeddings",
                "processing_results",
                "feeds",
                "topics",
                "user_subscriptions",
                "articles",
                "channels",
            ]

            for table in tables:
                conn.execute(f"DROP TABLE IF EXISTS {table}")

            conn.commit()
            logger.info("All database tables dropped")

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection with foreign keys enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row  # Enable dict-like access to rows
        return conn

    def verify_schema(self) -> bool:
        """Verify database schema is correctly created."""
        try:
            with self.get_connection() as conn:
                # Check all tables exist
                cursor = conn.execute(
                    """
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """
                )

                tables = [row[0] for row in cursor.fetchall()]
                expected_tables = {
                    "articles",
                    "channels",
                    "feeds",
                    "processing_results",
                    "topics",
                    "user_subscriptions",
                    "topic_embeddings",
                    "article_embeddings",
                }

                if not expected_tables.issubset(set(tables)):
                    missing = expected_tables - set(tables)
                    logger.error(
                        f"Missing tables. Expected (subset): {expected_tables}, Missing: {missing}, Found: {set(tables)}"
                    )
                    return False

                # Verify foreign key constraints
                conn.execute("PRAGMA foreign_key_check")

                logger.info("Database schema verification passed")
                return True

        except Exception as e:
            logger.error(f"Schema verification failed: {e}")
            return False


def create_tables(db_path: str = "data/culifeed.db") -> None:
    """Convenience function to create database tables.

    Args:
        db_path: Path to SQLite database file
    """
    schema = DatabaseSchema(db_path)
    schema.create_tables()


if __name__ == "__main__":
    # Allow running as script for database initialization
    logging.basicConfig(level=logging.INFO)
    create_tables()

    # Verify the schema was created correctly
    schema = DatabaseSchema()
    if schema.verify_schema():
        print("✅ Database schema created and verified successfully")
    else:
        print("❌ Database schema verification failed")
