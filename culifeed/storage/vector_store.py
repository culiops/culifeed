"""Vector storage abstraction backed by sqlite-vec virtual tables."""

import struct
from typing import List, Tuple

from ..database.connection import DatabaseConnection
from ..utils.logging import get_logger_for_component


def _serialize(vec: List[float]) -> bytes:
    """Serialize a float vector to sqlite-vec's binary format."""
    return struct.pack(f"{len(vec)}f", *vec)


class VectorStore:
    """Read/write embeddings via sqlite-vec virtual tables."""

    def __init__(self, db: DatabaseConnection):
        self._db = db
        self._logger = get_logger_for_component("vector_store")

    def upsert_topic_embedding(self, topic_id: int, vec: List[float]) -> None:
        with self._db.get_connection() as conn:
            conn.execute("DELETE FROM topic_embeddings WHERE topic_id = ?", (topic_id,))
            conn.execute(
                "INSERT INTO topic_embeddings(topic_id, embedding) VALUES(?, ?)",
                (topic_id, _serialize(vec)),
            )
            conn.commit()

    def upsert_article_embedding(self, article_id: str, vec: List[float]) -> None:
        with self._db.get_connection() as conn:
            conn.execute("DELETE FROM article_embeddings WHERE article_id = ?", (article_id,))
            conn.execute(
                "INSERT INTO article_embeddings(article_id, embedding) VALUES(?, ?)",
                (article_id, _serialize(vec)),
            )
            conn.commit()

    def rank_topics_for_article(
        self,
        article_id: str,
        active_topic_ids: List[int],
        top_k: int = 3,
    ) -> List[Tuple[int, float]]:
        """Return [(topic_id, similarity)] sorted by similarity descending.

        Similarity = 1 - cosine_distance, so higher is better.
        """
        if not active_topic_ids:
            return []
        with self._db.get_connection() as conn:
            row = conn.execute(
                "SELECT embedding FROM article_embeddings WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            if row is None:
                return []
            article_vec = row[0]

            placeholders = ",".join("?" * len(active_topic_ids))
            cur = conn.execute(
                f"""
                SELECT topic_id, vec_distance_cosine(embedding, ?) AS dist
                FROM topic_embeddings
                WHERE topic_id IN ({placeholders})
                ORDER BY dist ASC
                LIMIT ?
                """,
                (article_vec, *active_topic_ids, top_k),
            )
            results: List[Tuple[int, float]] = []
            for tid, dist in cur.fetchall():
                # sqlite-vec can return NULL for vec_distance_cosine in
                # degenerate cases (e.g. zero-vector inputs). Skip rather
                # than crash on float(None).
                if dist is None:
                    continue
                results.append((int(tid), 1.0 - float(dist)))
            return results

    def delete_topic_embedding(self, topic_id: int) -> None:
        """Remove a topic's stored embedding row.

        Safe to call when no embedding exists (no-op). Used by topic
        deletion paths to keep ``topic_embeddings`` from accumulating
        orphan rows after a topic is removed.
        """
        with self._db.get_connection() as conn:
            conn.execute(
                "DELETE FROM topic_embeddings WHERE topic_id = ?",
                (topic_id,),
            )
            conn.commit()

    def prune_articles_older_than(self, days: int) -> int:
        """Delete embeddings for articles whose `articles.created_at` is older than `days`."""
        with self._db.get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM article_embeddings
                WHERE article_id IN (
                    SELECT id FROM articles
                    WHERE created_at < datetime('now', ?)
                )
                """,
                (f"-{days} days",),
            )
            conn.commit()
            return cur.rowcount
