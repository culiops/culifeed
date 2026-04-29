"""Tests for VectorStore (sqlite-vec abstraction)."""
import datetime as dt
from typing import List

import pytest

from culifeed.database.connection import DatabaseConnection
from culifeed.database.schema import DatabaseSchema
from culifeed.storage.vector_store import VectorStore


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "v.db")
    DatabaseSchema(p).create_tables()
    return DatabaseConnection(p)


def _vec(seed: float, dim: int = 1536):
    return [seed] * dim


def _vec_unit(dim: int = 1536, hot_index: int = 0) -> List[float]:
    """Return a unit vector with 1.0 only at hot_index (one-hot style)."""
    v = [0.0] * dim
    v[hot_index] = 1.0
    return v


def test_upsert_and_rank_basic(db):
    vs = VectorStore(db)
    # Use orthogonal unit vectors so cosine distances are meaningfully different
    vs.upsert_topic_embedding(1, _vec_unit(hot_index=0))   # dimension 0
    vs.upsert_topic_embedding(2, _vec_unit(hot_index=1))   # dimension 1
    vs.upsert_topic_embedding(3, _vec_unit(hot_index=2))   # dimension 2
    vs.upsert_article_embedding("art-1", _vec_unit(hot_index=2))  # identical to topic 3
    ranked = vs.rank_topics_for_article("art-1", [1, 2, 3], top_k=3)
    assert len(ranked) == 3
    # Topic 3 is identical direction to article → cosine distance 0 → similarity 1.0
    assert ranked[0][0] == 3
    # Top score should be ~1.0 (cosine similarity of identical-direction vectors)
    assert ranked[0][1] > 0.99


def test_upsert_replaces_existing(db):
    vs = VectorStore(db)
    vs.upsert_topic_embedding(1, _vec(0.1))
    vs.upsert_topic_embedding(1, _vec(0.5))  # replace
    vs.upsert_article_embedding("a", _vec(0.5))
    ranked = vs.rank_topics_for_article("a", [1])
    assert len(ranked) == 1
    assert ranked[0][0] == 1
    assert ranked[0][1] > 0.99


def test_rank_filters_by_active_topic_ids(db):
    vs = VectorStore(db)
    vs.upsert_topic_embedding(1, _vec(0.5))
    vs.upsert_topic_embedding(2, _vec(0.5))
    vs.upsert_article_embedding("a", _vec(0.5))
    ranked = vs.rank_topics_for_article("a", [2])  # only topic 2 active
    assert len(ranked) == 1
    assert ranked[0][0] == 2


def test_rank_returns_empty_when_article_missing(db):
    vs = VectorStore(db)
    vs.upsert_topic_embedding(1, _vec(0.5))
    ranked = vs.rank_topics_for_article("does-not-exist", [1])
    assert ranked == []


def test_rank_returns_empty_when_no_active_topics(db):
    vs = VectorStore(db)
    vs.upsert_article_embedding("a", _vec(0.5))
    assert vs.rank_topics_for_article("a", []) == []


def test_prune_articles_older_than(db):
    vs = VectorStore(db)
    vs.upsert_article_embedding("old", _vec(0.1))
    vs.upsert_article_embedding("new", _vec(0.1))

    # Seed articles rows so the JOIN finds them
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO articles(id,title,url,source_feed,content_hash,created_at) "
            "VALUES('old','t','u1','f','h',?)",
            ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=120)).isoformat(),),
        )
        conn.execute(
            "INSERT INTO articles(id,title,url,source_feed,content_hash,created_at) "
            "VALUES('new','t','u2','f','h2',?)",
            (dt.datetime.now(dt.timezone.utc).isoformat(),),
        )
        conn.commit()

    pruned = vs.prune_articles_older_than(days=90)
    assert pruned == 1
    with db.get_connection() as conn:
        ids = {r[0] for r in conn.execute("SELECT article_id FROM article_embeddings").fetchall()}
        assert "old" not in ids
        assert "new" in ids
