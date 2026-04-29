"""Topic matching via embedding similarity (v2 pipeline stage 2)."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ..ai.embedding_service import EmbeddingService
from ..database.models import Article, Topic
from ..storage.vector_store import VectorStore
from ..utils.logging import get_logger_for_component


@dataclass
class MatchResult:
    article_id: str
    top_topics: List[Tuple[Topic, float]] = field(default_factory=list)  # top 3, descending
    chosen: Optional[Topic] = None
    chosen_score: float = 0.0


class TopicMatcher:
    """Embedding-based article → topic matcher."""

    def __init__(self, embeddings: EmbeddingService, vectors: VectorStore, settings):
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings
        self._logger = get_logger_for_component("topic_matcher")

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _topic_text(topic: Topic) -> str:
        """Build the string that gets embedded for a topic."""
        keywords_part = ", ".join(topic.keywords) if topic.keywords else ""
        if topic.description:
            return f"{topic.name}. {topic.description}. Keywords: {keywords_part}"
        return f"{topic.name}. Keywords: {keywords_part}"

    @staticmethod
    def _compute_signature(topic: Topic) -> str:
        payload = json.dumps(
            {
                "name": topic.name,
                "description": topic.description or "",
                "keywords": sorted(topic.keywords or []),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _article_text(article: Article) -> str:
        title = article.title or ""
        content = (article.content or "")[:1500]
        return f"{title}\n\n{content}"

    # -- public API ------------------------------------------------------------

    async def ensure_topic_embeddings(self, topics: List[Topic]) -> None:
        """Recompute embeddings for any topic whose signature is stale.

        Mutates topic.embedding_signature and topic.embedding_updated_at on
        each updated topic. Caller is responsible for persisting these
        attributes back to the database.
        """
        stale: List[Topic] = []
        for t in topics:
            sig = self._compute_signature(t)
            if t.embedding_signature != sig:
                stale.append(t)
        if not stale:
            return

        self._logger.info(f"Re-embedding {len(stale)} stale topic(s)")
        texts = [self._topic_text(t) for t in stale]
        vecs = await self._embeddings.embed_batch(texts)
        now = datetime.now(timezone.utc)
        for t, v in zip(stale, vecs):
            self._vectors.upsert_topic_embedding(t.id, v)
            t.embedding_signature = self._compute_signature(t)
            t.embedding_updated_at = now

    async def match(
        self,
        article: Article,
        topics: List[Topic],
        *,
        article_vector: Optional[List[float]] = None,
    ) -> MatchResult:
        """Embed the article (if needed) and rank it against active topics.

        Args:
            article: Article to match.
            topics: Candidate topics.
            article_vector: Optional precomputed embedding. When supplied, the
                embedding API is NOT called and the vector is assumed to have
                already been upserted into the vector store by the caller.
                This avoids the "double embedding" cost when the pipeline
                batch-embeds survivors before per-article matching.
        """
        if not topics:
            return MatchResult(article_id=article.id, top_topics=[], chosen=None, chosen_score=0.0)

        if article_vector is None:
            text = self._article_text(article)
            article_vector = await self._embeddings.embed(text)
            self._vectors.upsert_article_embedding(article.id, article_vector)

        active_ids = [t.id for t in topics if t.active and t.id is not None]
        ranked = self._vectors.rank_topics_for_article(article.id, active_ids, top_k=3)
        topic_by_id = {t.id: t for t in topics}
        top_topics: List[Tuple[Topic, float]] = [
            (topic_by_id[tid], score) for tid, score in ranked if tid in topic_by_id
        ]

        threshold = self._settings.filtering.embedding_min_score
        chosen: Optional[Topic] = None
        chosen_score = 0.0
        if top_topics:
            best_topic, best_score = top_topics[0]
            chosen_score = best_score
            if best_score >= threshold:
                chosen = best_topic

        return MatchResult(
            article_id=article.id,
            top_topics=top_topics,
            chosen=chosen,
            chosen_score=chosen_score,
        )
