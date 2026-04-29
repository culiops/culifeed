"""Repository-layer tests for v2 description column."""
from culifeed.database.connection import DatabaseConnection
from culifeed.database.schema import DatabaseSchema
from culifeed.database.models import Topic
from culifeed.storage.topic_repository import TopicRepository


def _setup(tmp_path):
    p = str(tmp_path / "tr.db")
    DatabaseSchema(p).create_tables()
    db = DatabaseConnection(p)
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO channels(chat_id, chat_title, chat_type) VALUES('c1', 'X', 'private')"
        )
        conn.commit()
    return TopicRepository(db), db


def test_create_topic_persists_description(tmp_path):
    repo, db = _setup(tmp_path)
    topic = Topic(
        chat_id="c1", name="T1",
        keywords=["k1"], exclude_keywords=[],
        description="A great topic.", active=True,
        confidence_threshold=0.6,
    )
    tid = repo.create_topic(topic)
    assert tid is not None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT description FROM topics WHERE id = ?", (tid,)
        ).fetchone()
        assert row[0] == "A great topic."


def test_create_topic_with_no_description_persists_null(tmp_path):
    repo, db = _setup(tmp_path)
    topic = Topic(
        chat_id="c1", name="T2",
        keywords=["k1"], exclude_keywords=[],
        active=True, confidence_threshold=0.6,
    )
    tid = repo.create_topic(topic)
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT description FROM topics WHERE id = ?", (tid,)
        ).fetchone()
        assert row[0] is None
