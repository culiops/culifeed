import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_backfill_writes_descriptions_for_topics_missing_them(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    from culifeed.database.connection import DatabaseConnection
    from scripts.backfill_topic_descriptions import backfill

    p = str(tmp_path / "b.db")
    DatabaseSchema(p).create_tables()
    db = DatabaseConnection(p)
    with db.get_connection() as conn:
        conn.execute("INSERT INTO channels(chat_id,chat_type,chat_title) VALUES('c','private','Test')")
        conn.execute("INSERT INTO topics(chat_id,name,keywords) "
                     "VALUES('c','T1','[\"k\"]')")
        conn.execute("INSERT INTO topics(chat_id,name,keywords,description) "
                     "VALUES('c','T2','[\"k\"]','already has')")
        conn.commit()

    fake_ai = MagicMock()
    fake_ai.complete = AsyncMock(return_value=MagicMock(
        success=True, content="Generated description"))

    with patch("scripts.backfill_topic_descriptions.AIManager",
               return_value=fake_ai):
        await backfill(db_path=p, dry_run=False)

    with db.get_connection() as conn:
        rows = conn.execute("SELECT name, description FROM topics ORDER BY name").fetchall()
        assert tuple(rows[0]) == ("T1", "Generated description")
        assert tuple(rows[1]) == ("T2", "already has")  # untouched
