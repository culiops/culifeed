"""Tests for TopicMatcher (embedding-based article→topic ranking)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from culifeed.database.models import Article, Topic
from culifeed.processing.topic_matcher import TopicMatcher, MatchResult


def _topic(id, name="t", keywords=None, description=None, signature=None):
    return Topic(
        id=id,
        chat_id="c1",
        name=name,
        keywords=keywords or ["k1"],
        exclude_keywords=[],
        description=description,
        embedding_signature=signature,
        confidence_threshold=0.7,
        active=True,
    )


def _article(id="a1", title="hello", content="world"):
    return Article(
        id=id,
        title=title,
        url=f"http://example.com/{id}",
        content=content,
        source_feed="https://example.com/feed.xml",
        content_hash="h",
    )


def _settings_with_threshold(threshold: float):
    settings = MagicMock()
    settings.filtering.embedding_min_score = threshold
    return settings


@pytest.mark.asyncio
async def test_match_returns_top_topic_above_threshold():
    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    vectors = MagicMock()
    vectors.upsert_article_embedding = MagicMock()
    vectors.rank_topics_for_article = MagicMock(return_value=[(1, 0.9), (2, 0.4)])

    tm = TopicMatcher(embeddings, vectors, _settings_with_threshold(0.45))
    topics = [_topic(1), _topic(2)]
    res = await tm.match(_article(), topics)

    assert isinstance(res, MatchResult)
    assert res.chosen is not None and res.chosen.id == 1
    assert res.chosen_score == 0.9
    assert len(res.top_topics) == 2


@pytest.mark.asyncio
async def test_match_skips_embedding_when_vector_provided():
    """When article_vector is precomputed, match() must NOT call embed/upsert.

    Regression for the v2 pipeline double-embedding bug: pipeline batch-embeds
    survivors before per-article matching, so passing the vector through avoids
    a wasted embedding API call per article.
    """
    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.99] * 1536)
    vectors = MagicMock()
    vectors.upsert_article_embedding = MagicMock()
    vectors.rank_topics_for_article = MagicMock(return_value=[(1, 0.9)])

    tm = TopicMatcher(embeddings, vectors, _settings_with_threshold(0.45))
    precomputed = [0.1] * 1536
    res = await tm.match(_article(), [_topic(1)], article_vector=precomputed)

    embeddings.embed.assert_not_awaited()
    vectors.upsert_article_embedding.assert_not_called()
    assert res.chosen is not None and res.chosen.id == 1


@pytest.mark.asyncio
async def test_match_returns_none_when_below_threshold():
    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    vectors = MagicMock()
    vectors.upsert_article_embedding = MagicMock()
    vectors.rank_topics_for_article = MagicMock(return_value=[(1, 0.3)])

    tm = TopicMatcher(embeddings, vectors, _settings_with_threshold(0.45))
    res = await tm.match(_article(), [_topic(1)])
    assert res.chosen is None
    assert res.chosen_score == 0.3


@pytest.mark.asyncio
async def test_match_returns_empty_when_no_topics():
    embeddings = AsyncMock()
    vectors = MagicMock()
    tm = TopicMatcher(embeddings, vectors, _settings_with_threshold(0.45))
    res = await tm.match(_article(), [])
    assert res.chosen is None
    assert res.top_topics == []
    embeddings.embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_topic_embeddings_only_recomputes_stale():
    embeddings = AsyncMock()
    embeddings.embed_batch = AsyncMock(return_value=[[0.2] * 1536])
    vectors = MagicMock()
    vectors.upsert_topic_embedding = MagicMock()
    settings = MagicMock()
    tm = TopicMatcher(embeddings, vectors, settings)

    # Compute the signature for what topic 1 currently is and store it as fresh
    t_fresh = _topic(1)
    fresh_sig = tm._compute_signature(t_fresh)
    t_fresh.embedding_signature = fresh_sig

    # Topic 2 has a stale (incorrect) signature
    t_stale = _topic(2, name="stale-name")
    t_stale.embedding_signature = "stale-old-hash"

    await tm.ensure_topic_embeddings([t_fresh, t_stale])

    # Only stale topic should have been re-embedded
    assert embeddings.embed_batch.await_count == 1
    args = embeddings.embed_batch.await_args.args[0]
    assert len(args) == 1  # only one stale topic
    vectors.upsert_topic_embedding.assert_called_once()
    # Stale topic should have been updated to the fresh signature
    assert t_stale.embedding_signature == tm._compute_signature(t_stale)


@pytest.mark.asyncio
async def test_ensure_topic_embeddings_noop_when_all_fresh():
    embeddings = AsyncMock()
    embeddings.embed_batch = AsyncMock()
    vectors = MagicMock()
    settings = MagicMock()
    tm = TopicMatcher(embeddings, vectors, settings)
    t = _topic(1)
    t.embedding_signature = tm._compute_signature(t)
    await tm.ensure_topic_embeddings([t])
    embeddings.embed_batch.assert_not_awaited()


def test_compute_signature_is_deterministic_and_order_independent():
    settings = MagicMock()
    tm = TopicMatcher(AsyncMock(), MagicMock(), settings)
    t1 = _topic(1, keywords=["a", "b", "c"])
    t2 = _topic(1, keywords=["c", "b", "a"])
    assert tm._compute_signature(t1) == tm._compute_signature(t2)


def test_topic_text_uses_description_when_present():
    settings = MagicMock()
    tm = TopicMatcher(AsyncMock(), MagicMock(), settings)
    t = _topic(1, name="X", keywords=["k1", "k2"], description="A nice description.")
    text = tm._topic_text(t)
    assert "X" in text
    assert "A nice description." in text
    assert "k1" in text


def test_topic_text_falls_back_when_no_description():
    settings = MagicMock()
    tm = TopicMatcher(AsyncMock(), MagicMock(), settings)
    t = _topic(1, name="X", keywords=["k1", "k2"])
    text = tm._topic_text(t)
    assert "X" in text
    assert "k1" in text
