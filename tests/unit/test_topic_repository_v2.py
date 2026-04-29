"""Repository-layer tests for v2 description column."""
from culifeed.database.connection import DatabaseConnection
from culifeed.database.schema import DatabaseSchema
from culifeed.database.models import Topic
from culifeed.storage.topic_repository import TopicRepository
from culifeed.storage.vector_store import VectorStore


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


def test_delete_topic_removes_vector_embedding(tmp_path):
    """delete_topic should also drop the topic's row in topic_embeddings
    when a VectorStore is wired into the repo."""
    p = str(tmp_path / "tr_del.db")
    DatabaseSchema(p).create_tables()
    db = DatabaseConnection(p)
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO channels(chat_id, chat_title, chat_type) VALUES('c1', 'X', 'private')"
        )
        conn.commit()

    vs = VectorStore(db)
    repo = TopicRepository(db, vector_store=vs)

    topic = Topic(
        chat_id="c1", name="T-del",
        keywords=["k"], exclude_keywords=[],
        active=True, confidence_threshold=0.6,
    )
    tid = repo.create_topic(topic)
    vs.upsert_topic_embedding(tid, [0.1] * 1536)

    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM topic_embeddings WHERE topic_id = ?", (tid,)
        ).fetchone()[0]
        assert n == 1, "embedding should exist before delete"

    assert repo.delete_topic(tid) is True

    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM topic_embeddings WHERE topic_id = ?", (tid,)
        ).fetchone()[0]
        assert n == 0, "embedding row should be removed by delete_topic"


def test_delete_topic_without_vector_store_still_works(tmp_path):
    """Backward compat: delete_topic with no vector_store wired must not raise."""
    repo, db = _setup(tmp_path)
    topic = Topic(
        chat_id="c1", name="T-bc",
        keywords=["k"], exclude_keywords=[],
        active=True, confidence_threshold=0.6,
    )
    tid = repo.create_topic(topic)
    assert repo.delete_topic(tid) is True
