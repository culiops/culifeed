"""Unit tests for culifeed diagnose CLI command."""

import json
import sqlite3
import pytest


def _seed_db(db_path: str) -> None:
    """Seed a minimal test database with one article and one v2 processing_results row."""
    conn = sqlite3.connect(db_path)
    try:
        # Create minimal required tables (no FK enforcement needed for unit test)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
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
                ai_relevance_score REAL,
                ai_confidence REAL,
                ai_provider TEXT,
                ai_reasoning TEXT
            )
        """)
        conn.execute("""
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
                pipeline_version TEXT DEFAULT 'v1'
            )
        """)
        conn.execute(
            "INSERT INTO articles (id, title, url, source_feed, content_hash) VALUES (?,?,?,?,?)",
            ("a1", "Test Article Title", "https://example.com/test", "https://feed.example.com/rss", "abc123"),
        )
        top_topics = json.dumps([
            {"topic_name": "machine learning", "score": 0.92},
            {"topic_name": "deep learning", "score": 0.85},
            {"topic_name": "neural networks", "score": 0.77},
        ])
        conn.execute(
            """INSERT INTO processing_results
               (article_id, chat_id, topic_name, pre_filter_score, embedding_score,
                embedding_top_topics, llm_decision, llm_reasoning, confidence_score,
                delivered, pipeline_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "a1",
                "chat_test",
                "AI Research",
                0.75,
                0.88,
                top_topics,
                "relevant",
                "Article closely matches AI research topic.",
                0.90,
                1,
                "v2",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_diagnose_prints_full_score_chain(tmp_path, capsys):
    """diagnose() prints article metadata and full v2 score chain."""
    from culifeed.cli.diagnose import diagnose

    db_path = str(tmp_path / "d.db")
    _seed_db(db_path)

    diagnose(db_path=db_path, article_id="a1")

    out = capsys.readouterr().out

    # Article metadata
    assert "Test Article Title" in out
    assert "https://example.com/test" in out
    assert "https://feed.example.com/rss" in out

    # Score chain fields
    assert "pre_filter_score" in out
    assert "embedding_score" in out
    assert "llm_decision" in out
    assert "llm_reasoning" in out

    # Top candidates section
    assert "Top 3 candidate topics" in out
    assert "machine learning" in out
    assert "deep learning" in out

    # Specific values
    assert "0.75" in out
    assert "0.88" in out
    assert "relevant" in out
    assert "v2" in out


def test_diagnose_article_not_found(tmp_path, capsys):
    """diagnose() prints a clear message when article_id does not exist."""
    from culifeed.cli.diagnose import diagnose

    db_path = str(tmp_path / "d.db")
    _seed_db(db_path)

    diagnose(db_path=db_path, article_id="nonexistent")

    out = capsys.readouterr().out
    assert "not found" in out.lower()


def test_diagnose_no_processing_results(tmp_path, capsys):
    """diagnose() handles article with no processing_results rows gracefully."""
    from culifeed.cli.diagnose import diagnose

    db_path = str(tmp_path / "d.db")
    _seed_db(db_path)

    # Insert second article without any processing results
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO articles (id, title, url, source_feed, content_hash) VALUES (?,?,?,?,?)",
        ("a2", "Another Article", "https://example.com/other", "https://feed.example.com/rss", "def456"),
    )
    conn.commit()
    conn.close()

    diagnose(db_path=db_path, article_id="a2")

    out = capsys.readouterr().out
    assert "Another Article" in out
    # No crash, and no score rows printed
    assert "pre_filter_score" not in out
