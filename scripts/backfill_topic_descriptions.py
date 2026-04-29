"""One-time backfill: generate descriptions for topics that lack one."""

import argparse
import asyncio
import json

from culifeed.ai.ai_manager import AIManager
from culifeed.config.settings import get_settings
from culifeed.database.connection import DatabaseConnection
from culifeed.processing.topic_description_generator import TopicDescriptionGenerator


async def backfill(db_path: str, dry_run: bool = False) -> None:
    db = DatabaseConnection(db_path)
    settings = get_settings()
    ai = AIManager(settings)
    generator = TopicDescriptionGenerator(ai)

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, keywords FROM topics WHERE description IS NULL OR description = ''"
        ).fetchall()

    print(f"Found {len(rows)} topic(s) without descriptions")
    for tid, name, keywords_json in rows:
        keywords = json.loads(keywords_json) if keywords_json else []
        desc = await generator.generate(name=name, keywords=keywords)
        print(f"  topic {tid} '{name}' → {desc[:80]}...")
        if not dry_run:
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE topics SET description = ? WHERE id = ?",
                    (desc, tid))
                conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(backfill(args.db, args.dry_run))


if __name__ == "__main__":
    main()
