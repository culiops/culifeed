"""Tests for the v2 (embedding) pipeline path in ProcessingPipeline.

These tests cover:
1. Happy-path: pre_filter_score, embedding_score, llm_decision, llm_reasoning,
   and pipeline_version='v2' all land in processing_results.
2. Error isolation: a single LLM-gate failure does not abort the run; the
   failing article is still persisted with decision='skipped' and reasoning
   containing the exception text.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from culifeed.database.connection import DatabaseConnection
from culifeed.database.schema import DatabaseSchema
from culifeed.processing.pipeline import ProcessingPipeline


def _seed_channel_topic_feed(db: DatabaseConnection, *, chat_id="c", topic_name="T",
                             keywords=None, feed_url="http://feed/"):
    keywords = keywords or ["aws", "lambda"]
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO channels(chat_id, chat_title, chat_type) VALUES(?, ?, 'private')",
            (chat_id, "Test Channel"),
        )
        conn.execute(
            "INSERT INTO topics(chat_id, name, keywords, description, active, "
            "confidence_threshold) VALUES(?, ?, ?, 'desc', 1, 0.5)",
            (chat_id, topic_name, json.dumps(keywords)),
        )
        conn.execute(
            "INSERT INTO feeds(chat_id, url, active) VALUES(?, ?, 1)",
            (chat_id, feed_url),
        )
        conn.commit()


def _seed_article(db: DatabaseConnection, *, article_id, title, content,
                  feed_url="http://feed/", url=None):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO articles(id, title, url, content, source_feed, content_hash) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (article_id, title, url or f"http://a/{article_id}", content, feed_url,
             f"hash-{article_id}"),
        )
        conn.commit()


def _build_settings():
    """Build a fully-stubbed settings object covering all attrs the pipeline reads."""
    settings = MagicMock()
    settings.filtering.use_embedding_pipeline = True
    settings.filtering.embedding_min_score = 0.45
    settings.filtering.embedding_fallback_threshold = 0.6
    settings.filtering.embedding_model = "text-embedding-3-small"
    settings.filtering.min_relevance_threshold = 0.05
    settings.filtering.exact_phrase_weight = 1.0
    settings.filtering.partial_word_weight = 0.5
    settings.filtering.single_word_tf_cap = 0.6
    settings.filtering.keyword_match_bonus = 0.2
    settings.filtering.fallback_confidence_cap = 0.7
    settings.filtering.fallback_relevance_threshold = 0.4
    settings.processing.parallel_feeds = 5
    settings.processing.max_articles_per_topic = 10
    settings.processing.ai_summary_threshold = 0.7
    settings.processing.ai_relevance_threshold = 0.5
    settings.limits.request_timeout = 30
    settings.smart_processing.enabled = False
    settings.ai.openai_api_key = "test-key"
    return settings


def _make_db(tmp_path) -> DatabaseConnection:
    p = str(tmp_path / "p.db")
    DatabaseSchema(p).create_tables()
    return DatabaseConnection(p)


@pytest.mark.asyncio
async def test_v2_persists_pre_filter_and_embedding_scores(tmp_path):
    """Regression: pre_filter_score and embedding_score must be non-NULL on v2 rows."""
    db = _make_db(tmp_path)
    _seed_channel_topic_feed(db)
    _seed_article(db, article_id="a1",
                  title="AWS Lambda news",
                  content="aws lambda serverless content")

    settings = _build_settings()

    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    embeddings.embed_batch = AsyncMock(return_value=[[0.1] * 1536])

    ai_manager = MagicMock()
    ai_manager.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content="DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: clearly relevant",
    ))

    pipeline = ProcessingPipeline(
        db, settings=settings,
        ai_manager=ai_manager,
        embedding_service=embeddings,
    )
    await pipeline._process_channel_v2("c")

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT pre_filter_score, embedding_score, llm_decision, "
            "llm_reasoning, pipeline_version "
            "FROM processing_results WHERE pipeline_version='v2'"
        ).fetchall()
    assert len(rows) == 1
    pf, emb, decision, reasoning, version = rows[0]
    assert pf is not None, "REGRESSION: pre_filter_score still NULL"
    assert emb is not None, "REGRESSION: embedding_score is NULL"
    assert decision == "pass"
    assert "clearly relevant" in reasoning
    assert version == "v2"


@pytest.mark.asyncio
async def test_v2_one_article_failure_does_not_abort_run(tmp_path):
    """Three articles. Third LLM call raises — first two pass, third persisted as skipped."""
    db = _make_db(tmp_path)
    _seed_channel_topic_feed(db)
    for i in (1, 2, 3):
        _seed_article(
            db, article_id=f"a{i}",
            title=f"AWS Lambda news {i}",
            content=f"aws lambda serverless content {i}",
        )

    settings = _build_settings()

    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    embeddings.embed_batch = AsyncMock(return_value=[[0.1] * 1536 for _ in range(3)])

    call_count = {"n": 0}

    async def flaky_complete(prompt):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("provider crash")
        return MagicMock(
            success=True,
            content="DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: ok",
        )

    ai_manager = MagicMock()
    ai_manager.complete = AsyncMock(side_effect=flaky_complete)

    pipeline = ProcessingPipeline(
        db, settings=settings,
        ai_manager=ai_manager,
        embedding_service=embeddings,
    )
    await pipeline._process_channel_v2("c")

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT article_id, llm_decision, llm_reasoning "
            "FROM processing_results WHERE pipeline_version='v2' "
            "ORDER BY article_id"
        ).fetchall()

    assert len(rows) == 3, f"Expected all 3 articles persisted, got {len(rows)}"
    decisions = {r[0]: (r[1], r[2]) for r in rows}
    # First two should pass
    assert decisions["a1"][0] == "pass"
    assert decisions["a2"][0] == "pass"
    # Third should be skipped with the crash reason in reasoning
    assert decisions["a3"][0] == "skipped"
    assert "provider crash" in decisions["a3"][1]


# ---------------------------------------------------------------------------
# D3: Article embedding pruning
# ---------------------------------------------------------------------------

def test_prune_articles_older_than_removes_old_rows(tmp_path):
    """VectorStore.prune_articles_older_than deletes stale embeddings and
    returns the count of removed rows, leaving fresh embeddings intact."""
    import struct

    db = _make_db(tmp_path)

    # Seed two articles directly — no channel/topic needed for this unit test.
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO articles(id, title, url, content, source_feed, content_hash) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            ("old-art", "Old", "http://a/old", "old content", "http://f/", "hash-old"),
        )
        conn.execute(
            "INSERT INTO articles(id, title, url, content, source_feed, content_hash) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            ("new-art", "New", "http://a/new", "new content", "http://f/", "hash-new"),
        )
        # Back-date the old article so it falls outside the 30-day window.
        conn.execute(
            "UPDATE articles SET created_at = datetime('now', '-60 days') "
            "WHERE id = 'old-art'"
        )
        conn.commit()

    from culifeed.storage.vector_store import VectorStore

    vs = VectorStore(db)
    dummy_vec = [0.1] * 1536
    vs.upsert_article_embedding("old-art", dummy_vec)
    vs.upsert_article_embedding("new-art", dummy_vec)

    # Verify both embeddings exist before pruning.
    with db.get_connection() as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM article_embeddings"
        ).fetchone()[0]
    assert count_before == 2

    pruned = vs.prune_articles_older_than(30)

    assert pruned == 1, f"Expected 1 pruned row, got {pruned}"

    with db.get_connection() as conn:
        remaining = [
            r[0]
            for r in conn.execute("SELECT article_id FROM article_embeddings").fetchall()
        ]
    assert "old-art" not in remaining
    assert "new-art" in remaining


@pytest.mark.asyncio
async def test_v2_pipeline_calls_prune_after_persist(tmp_path):
    """_process_channel_v2 must call VectorStore.prune_articles_older_than
    after persisting results, using settings.filtering.embedding_retention_days."""
    from unittest.mock import patch, MagicMock as MM

    db = _make_db(tmp_path)
    _seed_channel_topic_feed(db)
    _seed_article(db, article_id="a1", title="AWS Lambda news",
                  content="aws lambda serverless content")

    settings = _build_settings()
    settings.filtering.embedding_retention_days = 42

    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    embeddings.embed_batch = AsyncMock(return_value=[[0.1] * 1536])

    ai_manager = MagicMock()
    ai_manager.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content="DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: ok",
    ))

    pipeline = ProcessingPipeline(
        db, settings=settings,
        ai_manager=ai_manager,
        embedding_service=embeddings,
    )

    prune_calls = []

    original_prune = pipeline._vector_store.prune_articles_older_than

    def recording_prune(days):
        prune_calls.append(days)
        return original_prune(days)

    pipeline._vector_store.prune_articles_older_than = recording_prune

    await pipeline._process_channel_v2("c")

    assert prune_calls, "_process_channel_v2 never called prune_articles_older_than"
    assert prune_calls[-1] == 42, (
        f"Expected prune called with retention_days=42, got {prune_calls[-1]}"
    )
