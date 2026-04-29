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
