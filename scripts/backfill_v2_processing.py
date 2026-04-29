"""
E1: Backfill existing articles through the v2 embedding pipeline.

For each active channel, re-processes articles that do NOT yet have a v2
processing_results row and writes the result with delivered=1 so the
delivery scheduler does not re-send them to Telegram users.

Design notes
------------
- The standard _process_channel_v2 / _get_unprocessed_articles path is
  intentionally bypassed.  Those helpers filter on "no processing_results
  row at all", which excludes any article that already has a v1 row (the
  common case for existing production data).  The backfill uses its own
  SQL that filters on the absence of a *v2* row specifically.

- Rows are written with delivered=1 via the mark_delivered flag added to
  _process_articles_v2 / _persist_v2_result in pipeline.py.

- Idempotent: ON CONFLICT … DO NOTHING in _persist_v2_result means a
  second run skips articles that already have a v2 row.

Usage
-----
    python scripts/backfill_v2_processing.py --db data/culifeed.db

Dry-run (skips embedding / LLM calls, just prints article counts):
    python scripts/backfill_v2_processing.py --db data/culifeed.db --dry-run
"""

import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import List, Optional

# Make sure the project root is importable when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from culifeed.config.settings import get_settings
from culifeed.database.connection import DatabaseConnection
from culifeed.database.models import Article
from culifeed.database.schema import DatabaseSchema
from culifeed.processing.pipeline import ProcessingPipeline
from culifeed.utils.logging import get_logger_for_component

logger = get_logger_for_component("backfill_v2")


# ---------------------------------------------------------------------------
# Article query
# ---------------------------------------------------------------------------

def _get_articles_without_v2_row(
    db: DatabaseConnection, chat_id: str
) -> List[Article]:
    """Return articles for *chat_id* that have no v2 processing_results row.

    Uses an explicit NOT EXISTS subquery keyed on pipeline_version='v2' so
    that articles which already have a v1 row are still included — those are
    exactly the articles the backfill needs to process.

    The query also joins the feeds table so only articles from feeds
    belonging to this channel are returned (matching the logic in
    _get_unprocessed_articles).
    """
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.* FROM articles a
            JOIN feeds f ON a.source_feed = f.url
            WHERE f.chat_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM processing_results pr
                  WHERE pr.article_id = a.id
                    AND pr.chat_id = ?
                    AND pr.pipeline_version = 'v2'
              )
            ORDER BY a.published_at DESC
            """,
            (chat_id, chat_id),
        ).fetchall()

        return [Article(**dict(row)) for row in rows]


# ---------------------------------------------------------------------------
# Active channel query
# ---------------------------------------------------------------------------

def _get_active_channel_ids(db: DatabaseConnection) -> List[str]:
    """Return chat_ids for all channels that have at least one active feed."""
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT chat_id FROM feeds WHERE active = 1"
        ).fetchall()
        return [row["chat_id"] for row in rows]


# ---------------------------------------------------------------------------
# Core backfill coroutine
# ---------------------------------------------------------------------------

async def backfill(
    db_path: str,
    embedding_service=None,
    ai_manager=None,
    dry_run: bool = False,
) -> None:
    """Re-process existing articles through the v2 pipeline.

    Args:
        db_path: Path to the SQLite database file.
        embedding_service: Optional pre-built EmbeddingService (injected by
            tests to avoid real API calls).  When None the pipeline creates
            one from settings.
        ai_manager: Optional pre-built AIManager (injected by tests).  When
            None the pipeline creates one from settings.
        dry_run: If True, print article counts but skip embedding / LLM calls.
    """
    # Ensure schema is up to date (idempotent).
    schema = DatabaseSchema(db_path)
    schema.create_tables()

    db = DatabaseConnection(db_path, pool_size=2)

    # Deep-copy the global settings singleton so we can flip
    # use_embedding_pipeline=True without leaking state to other callers.
    settings = copy.deepcopy(get_settings())
    settings.filtering.use_embedding_pipeline = True
    # Provide a dummy key so lazy EmbeddingService creation does not error
    # before the injected stub (or real key) is in place.
    if not settings.ai.openai_api_key:
        settings.ai.openai_api_key = "backfill-placeholder"

    pipeline = ProcessingPipeline(
        db_connection=db,
        settings=settings,
        ai_manager=ai_manager,
        embedding_service=embedding_service,
    )

    # Ensure v2 services are initialised (embedding, matcher, gate).
    pipeline._ensure_v2_services()

    channel_ids = _get_active_channel_ids(db)
    if not channel_ids:
        logger.info("No active channels found — nothing to backfill.")
        print("No active channels found — nothing to backfill.")
        return

    logger.info(f"Backfilling {len(channel_ids)} channel(s): {channel_ids}")
    print(f"Backfilling {len(channel_ids)} channel(s): {channel_ids}")

    total_processed = 0

    for chat_id in channel_ids:
        topics = pipeline._get_channel_topics(chat_id)
        if not topics:
            logger.info(f"Channel {chat_id}: no active topics — skipping.")
            print(f"  [{chat_id}] no active topics — skipping")
            continue

        # Ensure topic embeddings are computed / up to date.
        await pipeline._topic_matcher.ensure_topic_embeddings(topics)
        pipeline._persist_topic_signatures(topics)

        articles = _get_articles_without_v2_row(db, chat_id)
        if not articles:
            logger.info(f"Channel {chat_id}: no articles without v2 row — skipping.")
            print(f"  [{chat_id}] no articles to backfill — skipping")
            continue

        logger.info(
            f"Channel {chat_id}: {len(articles)} article(s) to backfill "
            f"across {len(topics)} topic(s)"
        )
        print(
            f"  [{chat_id}] {len(articles)} article(s) to backfill "
            f"across {len(topics)} topic(s)"
        )

        if dry_run:
            print(f"  [{chat_id}] dry-run: skipping embedding / LLM calls")
            continue

        # Pre-filter to identify survivors and their scores.
        pre_filter_results = pipeline.pre_filter.filter_articles(articles, topics)
        survivors = [
            (r.article, r.best_match_score)
            for r in pre_filter_results
            if r.best_match_score > 0
        ]

        if not survivors:
            logger.info(
                f"Channel {chat_id}: no articles survived pre-filter — skipping."
            )
            print(f"  [{chat_id}] no articles survived pre-filter — skipping")
            continue

        logger.info(
            f"Channel {chat_id}: {len(survivors)} survivor(s) after pre-filter"
        )
        print(f"  [{chat_id}] {len(survivors)} survivor(s) after pre-filter")

        # Run v2 stages (embed, match, LLM gate, persist).
        # mark_delivered=True prevents the scheduler from re-sending these.
        await pipeline._process_articles_v2(
            chat_id,
            topics,
            survivors,
            mark_delivered=True,
        )

        total_processed += len(survivors)
        print(f"  [{chat_id}] done — {len(survivors)} v2 row(s) written (delivered=1)")

    logger.info(f"Backfill complete. Total articles processed: {total_processed}")
    print(f"\nBackfill complete. Total v2 rows written: {total_processed}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill existing articles through the v2 embedding pipeline."
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to the CuliFeed SQLite database (e.g. data/culifeed.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print article counts but skip embedding / LLM calls",
    )
    args = parser.parse_args()

    asyncio.run(backfill(db_path=args.db, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
