"""Integration tests for the eval_matching harness.

Tests metric computation (precision, recall, F1, accuracy, confusion matrix)
against a seeded in-memory-style SQLite database.
"""

import asyncio
import csv
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure project root is on path regardless of invocation context.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite database with the schema needed by evaluate()."""
    db_path = tmp_path / "eval_test.db"

    import sqlite_vec

    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = OFF")  # simplify seeding

    # Minimal tables — no foreign key enforcement so we can skip channels/topics
    conn.execute("""
        CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            content TEXT,
            source_feed TEXT NOT NULL DEFAULT 'test',
            content_hash TEXT NOT NULL DEFAULT 'hash'
        )
    """)
    conn.execute("""
        CREATE TABLE processing_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL,
            chat_id TEXT NOT NULL DEFAULT 'test_chat',
            topic_name TEXT NOT NULL,
            pipeline_version TEXT DEFAULT 'v1'
        )
    """)

    # Seed 3 articles
    articles = [
        ("art001", "Article One", "https://example.com/1"),
        ("art002", "Article Two", "https://example.com/2"),
        ("art003", "Article Three", "https://example.com/3"),
    ]
    conn.executemany(
        "INSERT INTO articles (id, title, url) VALUES (?, ?, ?)", articles
    )

    # Seed 3 v2 processing_results:
    #   art001 → topic_a  (matches expected)
    #   art002 → topic_b  (matches expected)
    #   art003 → topic_c  (expected topic_b — MISMATCH)
    results = [
        ("art001", "topic_a", "v2"),
        ("art002", "topic_b", "v2"),
        ("art003", "topic_c", "v2"),  # mismatch: expected topic_b
    ]
    conn.executemany(
        "INSERT INTO processing_results (article_id, topic_name, pipeline_version) VALUES (?, ?, ?)",
        results,
    )
    conn.commit()
    conn.close()

    return db_path


def _create_csv(tmp_path: Path) -> Path:
    """Write a labeled CSV: 2 correct, 1 wrong (art003 expected topic_b)."""
    csv_path = tmp_path / "labeled.csv"
    rows = [
        {"article_id": "art001", "expected_topic_name": "topic_a"},
        {"article_id": "art002", "expected_topic_name": "topic_b"},
        {"article_id": "art003", "expected_topic_name": "topic_b"},  # actual is topic_c
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["article_id", "expected_topic_name"])
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEvalMatchingMetrics:
    """Test metric computation in the eval_matching harness."""

    def test_top1_accuracy_two_of_three(self, tmp_path, capsys):
        """66.7% accuracy when 2 of 3 articles match expected topic."""
        from scripts.eval_matching import evaluate

        db_path = _create_db(tmp_path)
        csv_path = _create_csv(tmp_path)

        asyncio.run(evaluate(str(csv_path), str(db_path)))
        captured = capsys.readouterr()

        assert "66.7%" in captured.out, (
            f"Expected '66.7%' in output, got:\n{captured.out}"
        )

    def test_per_topic_metrics_present(self, tmp_path, capsys):
        """Per-topic precision, recall, and F1 lines appear in output."""
        from scripts.eval_matching import evaluate

        db_path = _create_db(tmp_path)
        csv_path = _create_csv(tmp_path)

        asyncio.run(evaluate(str(csv_path), str(db_path)))
        captured = capsys.readouterr()

        # Header line should be present
        assert "Prec" in captured.out
        assert "Rec" in captured.out
        assert "F1" in captured.out

        # All three topics should appear in the per-topic table
        assert "topic_a" in captured.out
        assert "topic_b" in captured.out

    def test_confusion_matrix_shows_mismatch(self, tmp_path, capsys):
        """The mismatch (topic_b → topic_c) appears in the confusion output."""
        from scripts.eval_matching import evaluate

        db_path = _create_db(tmp_path)
        csv_path = _create_csv(tmp_path)

        asyncio.run(evaluate(str(csv_path), str(db_path)))
        captured = capsys.readouterr()

        assert "Confusion" in captured.out
        # art003: expected topic_b, got topic_c
        assert "topic_b" in captured.out
        assert "topic_c" in captured.out

    def test_empty_csv_does_not_crash(self, tmp_path, capsys):
        """An empty CSV (header only) runs without error and reports 0 articles."""
        from scripts.eval_matching import evaluate

        db_path = _create_db(tmp_path)
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("article_id,expected_topic_name\n")

        asyncio.run(evaluate(str(csv_path), str(db_path)))
        captured = capsys.readouterr()

        assert "Articles labeled: 0" in captured.out

    def test_article_count_line_present(self, tmp_path, capsys):
        """Output includes labeled and scored article counts."""
        from scripts.eval_matching import evaluate

        db_path = _create_db(tmp_path)
        csv_path = _create_csv(tmp_path)

        asyncio.run(evaluate(str(csv_path), str(db_path)))
        captured = capsys.readouterr()

        assert "Articles labeled: 3" in captured.out
        assert "scored: 3" in captured.out

    def test_perfect_accuracy_score(self, tmp_path, capsys):
        """Reports 100.0% when all articles match their expected topic."""
        from scripts.eval_matching import evaluate

        db_path = _create_db(tmp_path)

        # CSV where art003 expects topic_c — matching the actual v2 result
        csv_path = tmp_path / "perfect.csv"
        rows = [
            {"article_id": "art001", "expected_topic_name": "topic_a"},
            {"article_id": "art002", "expected_topic_name": "topic_b"},
            {"article_id": "art003", "expected_topic_name": "topic_c"},
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["article_id", "expected_topic_name"])
            writer.writeheader()
            writer.writerows(rows)

        asyncio.run(evaluate(str(csv_path), str(db_path)))
        captured = capsys.readouterr()

        assert "100.0%" in captured.out
