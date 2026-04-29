"""Print the full diagnostic chain for an article."""

import json
import sqlite3
from pathlib import Path


def diagnose(db_path: str, article_id: str) -> None:
    """Print the full diagnostic chain for a given article.

    Outputs article metadata followed by all processing_results rows
    (v1 + v2) showing pre_filter_score, embedding_score, llm_decision,
    llm_reasoning, top candidate topics, and delivered flag.

    Args:
        db_path: Path to the SQLite database file.
        article_id: The article ID to diagnose.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        article = conn.execute(
            "SELECT title, url, source_feed FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()

        if not article:
            print(f"Article {article_id!r} not found")
            return

        print(f"Article: {article['title']}")
        print(f"URL:     {article['url']}")
        print(f"Feed:    {article['source_feed']}")
        print()

        rows = conn.execute(
            """
            SELECT topic_name, pipeline_version, pre_filter_score, embedding_score,
                   embedding_top_topics, llm_decision, llm_reasoning, confidence_score,
                   delivered
            FROM processing_results
            WHERE article_id = ?
            ORDER BY pipeline_version, processed_at
            """,
            (article_id,),
        ).fetchall()

        for row in rows:
            topic = row["topic_name"]
            ver = row["pipeline_version"]
            pf = row["pre_filter_score"]
            emb = row["embedding_score"]
            top_topics_json = row["embedding_top_topics"]
            decision = row["llm_decision"]
            reasoning = row["llm_reasoning"]
            conf = row["confidence_score"]
            delivered = row["delivered"]

            print(f"--- {ver} -> topic '{topic}' ---")
            print(f"  pre_filter_score: {pf}")
            print(f"  embedding_score:  {emb}")
            print(f"  llm_decision:     {decision}  (confidence={conf})")
            print(f"  llm_reasoning:    {reasoning}")
            print(f"  delivered:        {bool(delivered)}")

            if top_topics_json:
                top = json.loads(top_topics_json)
                print("  Top 3 candidate topics:")
                for t in top[:3]:
                    score = t.get("score", 0.0)
                    name = t.get("topic_name", "")
                    print(f"    {score:.3f}  {name}")

            print()
    finally:
        conn.close()


def main() -> None:
    """Entry point for standalone CLI invocation."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Print the full diagnostic chain for an article."
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database file")
    parser.add_argument("article_id", help="Article ID to diagnose")
    args = parser.parse_args()

    diagnose(db_path=args.db, article_id=args.article_id)


if __name__ == "__main__":
    main()
