"""
Topic Repository
================

Repository pattern implementation for Topic CRUD operations with proper
error handling and data access abstraction.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from ..database.models import Topic
from ..database.connection import DatabaseConnection
from ..config.settings import get_settings
from ..utils.logging import get_logger_for_component
from ..utils.exceptions import DatabaseError, ErrorCode
from ..utils.validators import ContentValidator


class TopicRepository:
    """Repository for Topic CRUD operations with database abstraction."""

    def __init__(self, db_connection: DatabaseConnection, vector_store=None):
        """Initialize topic repository.

        Args:
            db_connection: Database connection manager
            vector_store: Optional VectorStore for cleaning up topic
                embeddings on delete. Default None keeps backward
                compatibility for callers that don't use the v2
                embedding pipeline.
        """
        self.db = db_connection
        self.vector_store = vector_store
        self.logger = get_logger_for_component("topic_repository")
        self.settings = get_settings()

    def create_topic(self, topic: Topic) -> int:
        """Create a new topic.

        Args:
            topic: Topic model to create

        Returns:
            Created topic ID

        Raises:
            DatabaseError: If creation fails
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO topics (chat_id, name, keywords, exclude_keywords,
                                      confidence_threshold, created_at, last_match_at, active,
                                      telegram_user_id, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        topic.chat_id,
                        topic.name,
                        json.dumps(topic.keywords),
                        json.dumps(topic.exclude_keywords),
                        topic.confidence_threshold,
                        topic.created_at,
                        topic.last_match_at,
                        topic.active,
                        topic.telegram_user_id,
                        topic.description,
                    ),
                )
                conn.commit()
                topic_id = cursor.lastrowid

            self.logger.debug(f"Created topic: {topic.name} (ID: {topic_id})")
            return topic_id

        except Exception as e:
            raise DatabaseError(
                f"Failed to create topic {topic.name}: {e}",
                error_code=ErrorCode.DATABASE_ERROR,
            ) from e

    def get_topic(self, topic_id: int) -> Optional[Topic]:
        """Get topic by ID.

        Args:
            topic_id: Topic ID to retrieve

        Returns:
            Topic model or None if not found
        """
        try:
            with self.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()

                if row:
                    return self._row_to_topic(row)
                return None

        except Exception as e:
            self.logger.error(f"Failed to get topic {topic_id}: {e}")
            return None

    def get_topic_by_name(self, chat_id: str, name: str) -> Optional[Topic]:
        """Get topic by name within a chat.

        Args:
            chat_id: Chat ID to search within
            name: Topic name to find

        Returns:
            Topic model or None if not found
        """
        try:
            with self.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM topics WHERE chat_id = ? AND name = ?",
                    (chat_id, name),
                ).fetchone()

                if row:
                    return self._row_to_topic(row)
                return None

        except Exception as e:
            self.logger.error(f"Failed to get topic {name} for chat {chat_id}: {e}")
            return None

    def get_topics_for_chat(
        self, chat_id: str, active_only: bool = True
    ) -> List[Topic]:
        """Get all topics for a specific chat.

        Args:
            chat_id: Chat ID to get topics for
            active_only: Whether to return only active topics

        Returns:
            List of Topic models
        """
        try:
            query = "SELECT * FROM topics WHERE chat_id = ?"
            params = [chat_id]

            if active_only:
                query += " AND active = ?"
                params.append(True)

            query += " ORDER BY created_at ASC"

            with self.db.get_connection() as conn:
                rows = conn.execute(query, params).fetchall()

                return [self._row_to_topic(row) for row in rows]

        except Exception as e:
            self.logger.error(f"Failed to get topics for chat {chat_id}: {e}")
            return []

    def get_topics_for_channel(
        self, chat_id: str, active_only: bool = True
    ) -> List[Topic]:
        """Get all topics for a specific channel (alias for get_topics_for_chat).

        Args:
            chat_id: Chat ID to get topics for
            active_only: Whether to return only active topics

        Returns:
            List of Topic models
        """
        return self.get_topics_for_chat(chat_id, active_only)

    def get_all_active_topics(self) -> List[Topic]:
        """Get all active topics across all chats.

        Returns:
            List of all active Topic models
        """
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM topics WHERE active = ? ORDER BY chat_id, created_at ASC",
                    (True,),
                ).fetchall()

                return [self._row_to_topic(row) for row in rows]

        except Exception as e:
            self.logger.error(f"Failed to get all active topics: {e}")
            return []

    def update_topic(self, topic_id: int, updates: Dict[str, Any]) -> bool:
        """Update topic fields.

        Args:
            topic_id: Topic ID to update
            updates: Dictionary of fields to update

        Returns:
            True if update successful, False otherwise
        """
        if not updates:
            return True

        try:
            # Build dynamic update query
            set_clauses = []
            values = []

            for field, value in updates.items():
                if field == "name":
                    # Validate topic name
                    validated_name = ContentValidator.validate_topic_name(value)
                    set_clauses.append("name = ?")
                    values.append(validated_name)

                elif field == "keywords":
                    # Validate keywords
                    validated_keywords = ContentValidator.validate_keywords(value)
                    set_clauses.append("keywords = ?")
                    values.append(json.dumps(validated_keywords))

                elif field == "exclude_keywords":
                    # Validate exclude keywords
                    if isinstance(value, list):
                        # Filter out empty keywords
                        validated_keywords = [
                            kw.strip().lower() for kw in value if kw.strip()
                        ]
                        set_clauses.append("exclude_keywords = ?")
                        values.append(json.dumps(validated_keywords))

                elif field == "confidence_threshold":
                    # Validate confidence threshold
                    if 0.0 <= value <= 1.0:
                        set_clauses.append("confidence_threshold = ?")
                        values.append(value)

                elif field == "active":
                    set_clauses.append("active = ?")
                    values.append(bool(value))

                elif field == "last_match_at":
                    set_clauses.append("last_match_at = ?")
                    values.append(value)

            if not set_clauses:
                return True

            values.append(topic_id)  # For WHERE clause

            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    f"UPDATE topics SET {', '.join(set_clauses)} WHERE id = ?", values
                )
                conn.commit()

                success = cursor.rowcount > 0
                if success:
                    self.logger.debug(f"Updated topic: {topic_id}")

                return success

        except Exception as e:
            self.logger.error(f"Failed to update topic {topic_id}: {e}")
            return False

    def update_topic_object(self, topic: Topic) -> bool:
        """Update topic using Topic object.

        Args:
            topic: Topic model with updated fields

        Returns:
            True if update successful, False otherwise
        """
        updates = {
            "name": topic.name,
            "keywords": topic.keywords,
            "exclude_keywords": topic.exclude_keywords,
            "confidence_threshold": topic.confidence_threshold,
            "active": topic.active,
            "last_match_at": topic.last_match_at,
        }
        return self.update_topic(topic.id, updates)

    def update_last_match(self, topic_id: int) -> bool:
        """Update the last match timestamp for a topic.

        Args:
            topic_id: Topic ID to update

        Returns:
            True if update successful, False otherwise
        """
        return self.update_topic(
            topic_id, {"last_match_at": datetime.now(timezone.utc)}
        )

    def activate_topic(self, topic_id: int) -> bool:
        """Activate a topic.

        Args:
            topic_id: Topic ID to activate

        Returns:
            True if activation successful, False otherwise
        """
        return self.update_topic(topic_id, {"active": True})

    def deactivate_topic(self, topic_id: int) -> bool:
        """Deactivate a topic.

        Args:
            topic_id: Topic ID to deactivate

        Returns:
            True if deactivation successful, False otherwise
        """
        return self.update_topic(topic_id, {"active": False})

    def delete_topic(self, topic_id: int) -> bool:
        """Delete topic by ID.

        Args:
            topic_id: Topic ID to delete

        Returns:
            True if deletion successful, False otherwise
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
                conn.commit()

                success = cursor.rowcount > 0
                if success:
                    self.logger.debug(f"Deleted topic: {topic_id}")
                    self._cleanup_topic_vector(topic_id)

                return success

        except Exception as e:
            self.logger.error(f"Failed to delete topic {topic_id}: {e}")
            return False

    def _cleanup_topic_vector(self, topic_id: int) -> None:
        """Remove the topic's embedding row from the vector store, if wired."""
        if self.vector_store is None:
            self.logger.debug(
                f"No vector_store wired; skipping embedding cleanup for topic {topic_id}"
            )
            return
        try:
            self.vector_store.delete_topic_embedding(topic_id)
        except Exception as e:
            # Don't let vector cleanup failure mask the successful row delete.
            self.logger.warning(
                f"Failed to delete topic_embeddings row for topic {topic_id}: {e}"
            )

    def delete_topics_for_chat(self, chat_id: str) -> int:
        """Delete all topics for a specific chat.

        Args:
            chat_id: Chat ID to delete topics for

        Returns:
            Number of topics deleted
        """
        try:
            with self.db.get_connection() as conn:
                # Capture topic IDs first so we can clean up their embeddings.
                ids_cur = conn.execute(
                    "SELECT id FROM topics WHERE chat_id = ?", (chat_id,)
                )
                topic_ids = [row[0] for row in ids_cur.fetchall()]

                cursor = conn.execute(
                    "DELETE FROM topics WHERE chat_id = ?", (chat_id,)
                )
                conn.commit()

                deleted_count = cursor.rowcount
                self.logger.info(f"Deleted {deleted_count} topics for chat {chat_id}")

                for tid in topic_ids:
                    self._cleanup_topic_vector(tid)

                return deleted_count

        except Exception as e:
            self.logger.error(f"Failed to delete topics for chat {chat_id}: {e}")
            return 0

    def search_topics(self, query: str, chat_id: str = None) -> List[Topic]:
        """Search topics by name or keywords.

        Args:
            query: Search query
            chat_id: Optional chat ID to limit search scope

        Returns:
            List of matching Topic models
        """
        try:
            query_pattern = f"%{query.lower()}%"

            if chat_id:
                sql = """
                    SELECT * FROM topics 
                    WHERE chat_id = ? AND (
                        LOWER(name) LIKE ? OR 
                        LOWER(keywords) LIKE ?
                    ) AND active = ?
                    ORDER BY name ASC
                """
                params = (chat_id, query_pattern, query_pattern, True)
            else:
                sql = """
                    SELECT * FROM topics 
                    WHERE (
                        LOWER(name) LIKE ? OR 
                        LOWER(keywords) LIKE ?
                    ) AND active = ?
                    ORDER BY chat_id, name ASC
                """
                params = (query_pattern, query_pattern, True)

            with self.db.get_connection() as conn:
                rows = conn.execute(sql, params).fetchall()

                return [self._row_to_topic(row) for row in rows]

        except Exception as e:
            self.logger.error(f"Failed to search topics with query '{query}': {e}")
            return []

    def get_topic_statistics(self, chat_id: str = None) -> Dict[str, Any]:
        """Get topic statistics.

        Args:
            chat_id: Optional chat ID to limit statistics scope

        Returns:
            Dictionary with topic statistics
        """
        try:
            where_clause = "WHERE chat_id = ?" if chat_id else ""
            params = [chat_id] if chat_id else []

            with self.db.get_connection() as conn:
                # Get basic counts
                basic_stats = conn.execute(
                    f"""
                    SELECT 
                        COUNT(*) as total_topics,
                        SUM(CASE WHEN active THEN 1 ELSE 0 END) as active_topics,
                        AVG(confidence_threshold) as avg_confidence_threshold
                    FROM topics
                    {where_clause}
                """,
                    params,
                ).fetchone()

                # Get keyword statistics
                keyword_stats = conn.execute(
                    f"""
                    SELECT 
                        AVG(json_array_length(keywords)) as avg_keywords_per_topic,
                        MAX(json_array_length(keywords)) as max_keywords_per_topic
                    FROM topics
                    {where_clause}
                """,
                    params,
                ).fetchone()

                # Get recent activity
                recent_matches = conn.execute(
                    f"""
                    SELECT COUNT(*) as topics_with_recent_matches
                    FROM topics
                    {where_clause}
                    {"AND" if where_clause else "WHERE"} last_match_at >= datetime('now', '-7 days')
                """,
                    params,
                ).fetchone()

                return {
                    "total_topics": basic_stats["total_topics"],
                    "active_topics": basic_stats["active_topics"],
                    "inactive_topics": basic_stats["total_topics"]
                    - basic_stats["active_topics"],
                    "avg_confidence_threshold": round(
                        basic_stats["avg_confidence_threshold"] or 0, 2
                    ),
                    "avg_keywords_per_topic": round(
                        keyword_stats["avg_keywords_per_topic"] or 0, 1
                    ),
                    "max_keywords_per_topic": keyword_stats["max_keywords_per_topic"]
                    or 0,
                    "topics_with_recent_matches": recent_matches[
                        "topics_with_recent_matches"
                    ],
                }

        except Exception as e:
            self.logger.error(f"Failed to get topic statistics: {e}")
            return {}

    def update_description(self, topic_id: int, description: str) -> None:
        """Update topic description.

        Args:
            topic_id: Topic ID to update
            description: New description text

        Raises:
            DatabaseError: If update fails
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE topics SET description = ? WHERE id = ?",
                    (description, topic_id),
                )
                conn.commit()

            self.logger.debug(f"Updated description for topic {topic_id}")

        except Exception as e:
            raise DatabaseError(
                f"Failed to update description for topic {topic_id}: {e}",
                error_code=ErrorCode.DATABASE_ERROR,
            ) from e

    def clear_embedding_signature(self, topic_id: int) -> None:
        """Clear embedding signature so the topic is re-embedded on next pipeline run.

        Args:
            topic_id: Topic ID to clear embedding signature for

        Raises:
            DatabaseError: If update fails
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE topics SET embedding_signature = NULL, embedding_updated_at = NULL WHERE id = ?",
                    (topic_id,),
                )
                conn.commit()

            self.logger.debug(f"Cleared embedding signature for topic {topic_id}")

        except Exception as e:
            raise DatabaseError(
                f"Failed to clear embedding signature for topic {topic_id}: {e}",
                error_code=ErrorCode.DATABASE_ERROR,
            ) from e

    def _row_to_topic(self, row: Dict[str, Any]) -> Topic:
        """Convert database row to Topic model.

        Args:
            row: Database row dictionary

        Returns:
            Topic model instance
        """
        topic_data = dict(row)

        # Parse JSON fields
        if isinstance(topic_data.get("keywords"), str):
            topic_data["keywords"] = json.loads(topic_data["keywords"])
        if isinstance(topic_data.get("exclude_keywords"), str):
            topic_data["exclude_keywords"] = json.loads(topic_data["exclude_keywords"])

        return Topic(**topic_data)
