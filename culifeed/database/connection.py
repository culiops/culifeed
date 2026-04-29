"""
CuliFeed Database Connection Management
======================================

Database connection pool and transaction management for SQLite with
proper error handling, connection pooling, and performance optimization.
"""

import sqlite3
import threading
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Generator, Any, List, Dict
from queue import Queue, Empty

import sqlite_vec

from culifeed.utils.exceptions import CuliFeedError, ErrorCode

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Thread-safe SQLite database connection manager with pooling."""

    def __init__(self, db_path: str = "data/culifeed.db", pool_size: int = 5):
        """Initialize database connection manager.

        Args:
            db_path: Path to SQLite database file
            pool_size: Maximum number of connections in pool
        """
        self.db_path = Path(db_path)
        self.pool_size = pool_size
        self.pool: Queue = Queue(maxsize=pool_size)
        self.lock = threading.Lock()
        self._total_connections = 0

        # Ensure database directory exists
        self.db_path.parent.mkdir(exist_ok=True)

        # Initialize connection pool
        self._initialize_pool()

    def _initialize_pool(self) -> None:
        """Initialize the connection pool with optimized connections."""
        for _ in range(self.pool_size):
            conn = self._create_connection()
            self.pool.put(conn)

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new optimized SQLite connection."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,  # Allow connection sharing across threads
            timeout=30.0,  # 30 second timeout for database locks
        )

        # Load sqlite-vec extension for vector similarity search
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except (sqlite3.Error, OSError, AttributeError) as e:
            logger.error(
                "sqlite-vec extension failed to load",
                exc_info=True,
                extra={"db_path": str(self.db_path)},
            )
            # TODO(A2): replace with ErrorCode.VECTOR_STORE_UNAVAILABLE once added
            raise CuliFeedError(
                "sqlite-vec extension failed to load",
                error_code=ErrorCode.DATABASE_CONNECTION,
            ) from e

        # Enable optimizations and features
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "PRAGMA journal_mode = WAL"
        )  # Write-Ahead Logging for better concurrency
        conn.execute(
            "PRAGMA synchronous = NORMAL"
        )  # Balance between safety and performance
        conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
        conn.execute("PRAGMA temp_store = MEMORY")  # Keep temp tables in memory
        conn.execute("PRAGMA mmap_size = 268435456")  # 256MB memory-mapped I/O

        # Enable row factory for dict-like access
        conn.row_factory = sqlite3.Row

        with self.lock:
            self._total_connections += 1

        logger.debug(f"Created database connection #{self._total_connections}")
        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a connection from the pool with automatic return.

        Usage:
            with db_manager.get_connection() as conn:
                cursor = conn.execute("SELECT * FROM channels")
                results = cursor.fetchall()
        """
        start_time = time.time()
        conn = None

        try:
            # Try to get connection from pool with timeout
            try:
                conn = self.pool.get(timeout=10.0)
            except Empty:
                # Pool exhausted, create new connection
                logger.warning("Connection pool exhausted, creating new connection")
                conn = self._create_connection()

            # Test connection validity
            conn.execute("SELECT 1").fetchone()

            acquisition_time = time.time() - start_time
            if acquisition_time > 1.0:
                logger.warning(
                    f"Database connection acquisition took {acquisition_time:.2f}s"
                )

            yield conn

        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            if conn:
                try:
                    # Return connection to pool if possible
                    if self.pool.qsize() < self.pool_size:
                        self.pool.put(conn)
                    else:
                        # Pool full, close connection
                        conn.close()
                        with self.lock:
                            self._total_connections -= 1
                except:
                    logger.error("Error returning connection to pool")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Execute operations within a database transaction.

        Usage:
            with db_manager.transaction() as conn:
                conn.execute("INSERT INTO channels ...")
                conn.execute("INSERT INTO topics ...")
                # Automatic commit on success, rollback on exception
        """
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Transaction rolled back due to error: {e}")
                raise

    def execute_query(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Execute a SELECT query and return results.

        Args:
            query: SQL SELECT statement
            params: Query parameters

        Returns:
            List of result rows
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall()

    def execute_one(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute a query and return single result.

        Args:
            query: SQL statement
            params: Query parameters

        Returns:
            Single result row or None
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()

    def execute_update(self, query: str, params: tuple = ()) -> int:
        """Execute an INSERT/UPDATE/DELETE query.

        Args:
            query: SQL statement
            params: Query parameters

        Returns:
            Number of affected rows
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.rowcount

    def execute_many(self, query: str, param_list: List[tuple]) -> int:
        """Execute a query with multiple parameter sets.

        Args:
            query: SQL statement
            param_list: List of parameter tuples

        Returns:
            Number of affected rows
        """
        with self.get_connection() as conn:
            cursor = conn.executemany(query, param_list)
            conn.commit()
            return cursor.rowcount

    def vacuum_database(self) -> None:
        """Perform database maintenance (VACUUM)."""
        logger.info("Starting database VACUUM operation")
        start_time = time.time()

        with self.get_connection() as conn:
            # VACUUM cannot be run within a transaction
            conn.isolation_level = None  # Autocommit mode
            try:
                conn.execute("VACUUM")
                duration = time.time() - start_time
                logger.info(f"Database VACUUM completed in {duration:.2f}s")
            finally:
                conn.isolation_level = ""  # Restore transaction mode

    def analyze_database(self) -> None:
        """Update database statistics for query optimization."""
        logger.info("Updating database statistics")

        with self.get_connection() as conn:
            conn.execute("ANALYZE")
            conn.commit()

    def get_database_info(self) -> Dict[str, Any]:
        """Get database information and statistics."""
        with self.get_connection() as conn:
            # Get database size
            cursor = conn.execute("PRAGMA page_count")
            page_count = cursor.fetchone()[0]

            cursor = conn.execute("PRAGMA page_size")
            page_size = cursor.fetchone()[0]

            db_size_bytes = page_count * page_size

            # Get table row counts
            table_counts = {}
            tables = ["channels", "articles", "topics", "feeds", "processing_results"]

            for table in tables:
                try:
                    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    table_counts[table] = cursor.fetchone()[0]
                except sqlite3.Error:
                    table_counts[table] = 0

            return {
                "database_size_mb": db_size_bytes / (1024 * 1024),
                "page_count": page_count,
                "page_size": page_size,
                "table_counts": table_counts,
                "connection_pool_size": self.pool.qsize(),
                "total_connections": self._total_connections,
            }

    def cleanup_old_data(self, days_to_keep: int = 7) -> int:
        """Clean up old articles and processing results.

        Args:
            days_to_keep: Number of days of data to retain

        Returns:
            Number of records deleted
        """
        logger.info(f"Cleaning up data older than {days_to_keep} days")

        with self.transaction() as conn:
            # Delete old processing results
            cursor = conn.execute(
                """
                DELETE FROM processing_results 
                WHERE processed_at < datetime('now', '-{} days')
            """.format(
                    days_to_keep
                )
            )

            processing_deleted = cursor.rowcount

            # Delete orphaned articles (no processing results)
            cursor = conn.execute(
                """
                DELETE FROM articles 
                WHERE created_at < datetime('now', '-{} days')
                AND id NOT IN (SELECT DISTINCT article_id FROM processing_results)
            """.format(
                    days_to_keep
                )
            )

            articles_deleted = cursor.rowcount

            total_deleted = processing_deleted + articles_deleted
            logger.info(
                f"Deleted {total_deleted} old records ({processing_deleted} processing results, {articles_deleted} articles)"
            )

            return total_deleted

    def close_all_connections(self) -> None:
        """Close all connections in the pool."""
        logger.info("Closing all database connections")

        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                conn.close()
            except (Empty, sqlite3.Error):
                break

        with self.lock:
            self._total_connections = 0


# Global database manager instance
_db_manager: Optional[DatabaseConnection] = None


def get_db_manager(db_path: str = "data/culifeed.db") -> DatabaseConnection:
    """Get global database manager instance (singleton pattern).

    Args:
        db_path: Path to database file

    Returns:
        Database connection manager instance
    """
    global _db_manager

    if _db_manager is None:
        _db_manager = DatabaseConnection(db_path)

    return _db_manager


# Convenience functions for common operations
def execute_query(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Execute a SELECT query using the global database manager."""
    return get_db_manager().execute_query(query, params)


def execute_one(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Execute a query and return single result using the global database manager."""
    return get_db_manager().execute_one(query, params)


def execute_update(query: str, params: tuple = ()) -> int:
    """Execute an UPDATE/INSERT/DELETE query using the global database manager."""
    return get_db_manager().execute_update(query, params)
