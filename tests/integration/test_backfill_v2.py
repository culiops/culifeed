"""
E1: Integration test for the v2 processing backfill script.

Scenario:
  - 1 channel, 1 active topic, 1 article, 1 v1 processing_results row.
  - Embedding service and AI manager are fully stubbed (no real API calls).
  - Run backfill() → assert a v2 row was written with delivered=1.
  - Re-run → no duplicate v2 rows (idempotency).
  - Original v1 row is untouched.
"""

import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Stub helpers (copied from test_v2_against_snapshot for consistency)
# ---------------------------------------------------------------------------

def _fake_embed(text: str) -> list:
    """Deterministic non-zero 1536-dim vector derived from text hash."""
    h = hashlib.sha256(text.encode()).digest()
    return [(h[i % len(h)] / 255.0) - 0.5 for i in range(1536)]


def _make_stub_embedding_service():
    svc = MagicMock()

    async def _embed(text):
        return _fake_embed(text)

    async def _embed_batch(texts):
        return [_fake_embed(t) for t in texts]

    svc.embed = AsyncMock(side_effect=_embed)
    svc.embed_batch = AsyncMock(side_effect=_embed_batch)
    return svc


def _make_stub_ai_manager():
    """Return a MagicMock AIManager whose complete() always returns PASS."""
    pass_response = MagicMock()
    pass_response.success = True
    pass_response.content = (
        "DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: Article is on-topic."
    )
    pass_response.error_message = None

    mgr = MagicMock()
    mgr.complete = AsyncMock(return_value=pass_response)
    # generate_summary is reached only if a stubbed article ever clears the
    # embedding similarity threshold and the gate then passes; the random
    # SHA-derived vectors usually fall below it, so this stub is defensive
    # against threshold/config drift.
    mgr.generate_summary = AsyncMock(return_value=None)
    return mgr


# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------

CHAT_ID = "-100backfilltest"
FEED_URL = "https://backfill-test-feed.example.com/rss"
ARTICLE_ID = "art-backfill-001"


def _seed_db(db_path: Path) -> None:
    """Seed the test database with minimal data for the backfill test."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # Channel
        conn.execute(
            "INSERT OR IGNORE INTO channels (chat_id, chat_title, chat_type) VALUES (?, ?, ?)",
            (CHAT_ID, "Backfill Test Channel", "group"),
        )

        # Feed
        conn.execute(
            "INSERT OR IGNORE INTO feeds (chat_id, url, title, active) VALUES (?, ?, ?, 1)",
            (CHAT_ID, FEED_URL, "Backfill Test Feed"),
        )

        # Article
        content = "AI and machine learning trends for 2025"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        conn.execute(
            """INSERT OR IGNORE INTO articles
               (id, title, url, content, source_feed, content_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                ARTICLE_ID,
                "AI Trends 2025",
                "https://backfill-test-feed.example.com/articles/1",
                content,
                FEED_URL,
                content_hash,
            ),
        )

        # Topic — keyword "AI" matches the article content
        conn.execute(
            """INSERT OR IGNORE INTO topics
               (chat_id, name, keywords, exclude_keywords, active, description)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (
                CHAT_ID,
                "Artificial Intelligence",
                json.dumps(["AI", "machine learning"]),
                json.dumps([]),
                "Articles about artificial intelligence and machine learning.",
            ),
        )

        # Pre-existing v1 processing_results row
        conn.execute(
            """INSERT OR IGNORE INTO processing_results
               (article_id, chat_id, topic_name, pre_filter_score,
                ai_relevance_score, confidence_score, delivered, pipeline_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ARTICLE_ID, CHAT_ID, "Artificial Intelligence", 0.7, 0.8, 0.75, 0, "v1"),
        )

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_writes_v2_row_with_delivered_one(tmp_path):
    """Backfill must write a v2 row with delivered=1 and leave the v1 row intact."""
    from culifeed.database.schema import DatabaseSchema
    from scripts.backfill_v2_processing import backfill

    db_path = tmp_path / "backfill_test.db"

    # Create schema then seed
    schema = DatabaseSchema(str(db_path))
    schema.create_tables()
    _seed_db(db_path)

    stub_embedding = _make_stub_embedding_service()
    stub_ai = _make_stub_ai_manager()

    await backfill(
        db_path=str(db_path),
        embedding_service=stub_embedding,
        ai_manager=stub_ai,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        v2_rows = conn.execute(
            "SELECT * FROM processing_results WHERE pipeline_version = 'v2' AND chat_id = ?",
            (CHAT_ID,),
        ).fetchall()

        assert len(v2_rows) >= 1, (
            f"Expected at least 1 v2 row, got {len(v2_rows)}. "
            "The backfill may have exited early or the article was not processed."
        )

        row = v2_rows[0]
        assert row["pre_filter_score"] is not None, "pre_filter_score should not be NULL"
        assert row["embedding_score"] is not None, "embedding_score should not be NULL"
        assert row["delivered"] == 1, (
            f"Backfill rows must have delivered=1, got {row['delivered']}"
        )

        # v1 row must still exist and be untouched
        v1_rows = conn.execute(
            "SELECT * FROM processing_results WHERE pipeline_version = 'v1' AND chat_id = ?",
            (CHAT_ID,),
        ).fetchall()
        assert len(v1_rows) == 1, f"Expected 1 v1 row still present, got {len(v1_rows)}"
        assert v1_rows[0]["article_id"] == ARTICLE_ID

    finally:
        conn.close()


@pytest.mark.asyncio
async def test_backfill_is_idempotent(tmp_path):
    """Running backfill twice must not create duplicate v2 rows."""
    from culifeed.database.schema import DatabaseSchema
    from scripts.backfill_v2_processing import backfill

    db_path = tmp_path / "backfill_idempotent.db"

    schema = DatabaseSchema(str(db_path))
    schema.create_tables()
    _seed_db(db_path)

    stub_embedding = _make_stub_embedding_service()
    stub_ai = _make_stub_ai_manager()

    # First run
    await backfill(
        db_path=str(db_path),
        embedding_service=stub_embedding,
        ai_manager=stub_ai,
    )

    # Second run — fresh stubs to avoid state leaking from first run
    await backfill(
        db_path=str(db_path),
        embedding_service=_make_stub_embedding_service(),
        ai_manager=_make_stub_ai_manager(),
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        v2_rows = conn.execute(
            "SELECT * FROM processing_results WHERE pipeline_version = 'v2' AND chat_id = ?",
            (CHAT_ID,),
        ).fetchall()
        # Each article x topic combo should produce exactly one v2 row
        assert len(v2_rows) == 1, (
            f"Idempotency violation: expected 1 v2 row after two runs, got {len(v2_rows)}"
        )
    finally:
        conn.close()
