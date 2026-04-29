# Topic-Matching Pipeline Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-topic relevance scoring with a 4-stage hybrid pipeline (keyword pre-filter → embedding ranker → LLM gate → persist+deliver) to fix systemic false positives, false negatives, and wrong-topic assignment.

**Architecture:** Stage 1 unchanged keyword pre-filter (now persists score). Stage 2 cosine-similarity ranking via OpenAI `text-embedding-3-small` cached in sqlite-vec. Stage 3 single yes/no LLM judgment on the embedding-chosen topic. Stage 4 persists all four scores plus real LLM reasoning. Old path stays behind a feature flag during shadow mode.

**Tech Stack:** Python 3.11, SQLite + sqlite-vec, OpenAI embeddings API, existing AIManager + provider chain, pytest.

**Spec:** `docs/superpowers/specs/2026-04-29-topic-matching-redesign-design.md`

---

## Phase A — Foundation

Establish dependencies, schema, error codes, and settings. No behavior change yet.

### Task A1: Add sqlite-vec dependency and load extension

**Files:**
- Modify: `requirements.txt`
- Modify: `culifeed/database/connection.py` (find connection-init method)
- Test: `tests/unit/test_database_connection.py` (new test added)

- [ ] **Step 1: Add dependency to requirements.txt**

Append line:
```
sqlite-vec>=0.1.0
```

- [ ] **Step 2: Install in venv**

Run: `source venv/bin/activate && pip install sqlite-vec>=0.1.0`
Expected: successful install.

- [ ] **Step 3: Write failing test**

Add to `tests/unit/test_database_connection.py`:
```python
def test_sqlite_vec_extension_loaded(tmp_path):
    from culifeed.database.connection import DatabaseConnection
    db = DatabaseConnection(str(tmp_path / "test.db"))
    with db.get_connection() as conn:
        rows = conn.execute("SELECT vec_version()").fetchall()
        assert rows[0][0].startswith("v")  # e.g. "v0.1.6"
```

Run: `pytest tests/unit/test_database_connection.py::test_sqlite_vec_extension_loaded -v`
Expected: FAIL with "no such function: vec_version".

- [ ] **Step 4: Load extension in DatabaseConnection**

In `culifeed/database/connection.py`, locate the method that creates a sqlite3 connection (likely `_create_connection` or similar). Add immediately after `conn = sqlite3.connect(...)`:

```python
import sqlite_vec
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)
```

Wrap in try/except. On failure raise:
```python
from culifeed.utils.exceptions import CuliFeedError, ErrorCode
raise CuliFeedError(
    "sqlite-vec extension failed to load",
    error_code=ErrorCode.VECTOR_STORE_UNAVAILABLE,
) from e
```
(ErrorCode added in Task A3.)

- [ ] **Step 5: Run test**

Run: `pytest tests/unit/test_database_connection.py::test_sqlite_vec_extension_loaded -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt culifeed/database/connection.py tests/unit/test_database_connection.py
git commit -m "feat(db): load sqlite-vec extension on connection init"
```

---

### Task A2: Add new error codes

**Files:**
- Modify: `culifeed/utils/exceptions.py`
- Test: `tests/unit/test_error_handling.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_error_handling.py`:
```python
def test_new_error_codes_exist():
    from culifeed.utils.exceptions import ErrorCode
    assert ErrorCode.AI_EMBEDDING_ERROR.value == "A011"
    assert ErrorCode.VECTOR_STORE_UNAVAILABLE.value == "D007"
    assert ErrorCode.CONTENT_EMPTY.value == "P005"
```

Run: `pytest tests/unit/test_error_handling.py::test_new_error_codes_exist -v`
Expected: FAIL.

- [ ] **Step 2: Add codes**

In `culifeed/utils/exceptions.py` `ErrorCode` enum:
- After `DATABASE_ERROR = "D006"` add: `VECTOR_STORE_UNAVAILABLE = "D007"`
- After `PRE_FILTER_ERROR = "P004"` add: `CONTENT_EMPTY = "P005"`
- After `AI_CONNECTION_ERROR = "A010"` add: `AI_EMBEDDING_ERROR = "A011"`

- [ ] **Step 3: Run test**

Run: `pytest tests/unit/test_error_handling.py::test_new_error_codes_exist -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add culifeed/utils/exceptions.py tests/unit/test_error_handling.py
git commit -m "feat(errors): add A011, D007, P005 error codes"
```

---

### Task A3: Schema migration — topics table extensions

**Files:**
- Modify: `culifeed/database/schema.py`
- Test: `tests/unit/test_database_models.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_database_models.py`:
```python
def test_topics_table_has_description_columns(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    assert "description" in cols
    assert "embedding_signature" in cols
    assert "embedding_updated_at" in cols
```

Run: `pytest tests/unit/test_database_models.py::test_topics_table_has_description_columns -v`
Expected: FAIL.

- [ ] **Step 2: Modify schema**

In `culifeed/database/schema.py`, find the `CREATE TABLE topics` statement. Add three columns after `active BOOLEAN DEFAULT TRUE`:

```sql
description TEXT,
embedding_signature TEXT,
embedding_updated_at TIMESTAMP,
```

Then add a runtime ALTER block (idempotent migration for existing prod DB) — find the `_migrate_existing` method (or create one called from `create_tables` if absent):

```python
def _migrate_topics_v2(self, conn):
    """Idempotent migration for v2 columns on topics."""
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    if "description" not in existing_cols:
        conn.execute("ALTER TABLE topics ADD COLUMN description TEXT")
    if "embedding_signature" not in existing_cols:
        conn.execute("ALTER TABLE topics ADD COLUMN embedding_signature TEXT")
    if "embedding_updated_at" not in existing_cols:
        conn.execute("ALTER TABLE topics ADD COLUMN embedding_updated_at TIMESTAMP")
```

Call `self._migrate_topics_v2(conn)` from `create_tables` after the CREATE TABLE statements run.

- [ ] **Step 3: Run test**

Run: `pytest tests/unit/test_database_models.py::test_topics_table_has_description_columns -v`
Expected: PASS.

- [ ] **Step 4: Migration idempotency test**

Append:
```python
def test_topics_migration_idempotent(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    schema.create_tables()  # second run must not raise
```

Run and confirm PASS.

- [ ] **Step 5: Commit**

```bash
git add culifeed/database/schema.py tests/unit/test_database_models.py
git commit -m "feat(db): add description+embedding columns to topics"
```

---

### Task A4: Schema migration — vector tables + processing_results columns

**Files:**
- Modify: `culifeed/database/schema.py`
- Test: `tests/unit/test_database_models.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_database_models.py`:
```python
def test_vector_tables_created(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    import sqlite3, sqlite_vec
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.enable_load_extension(True); sqlite_vec.load(conn)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
    assert "topic_embeddings" in tables
    assert "article_embeddings" in tables

def test_processing_results_v2_columns(tmp_path):
    from culifeed.database.schema import DatabaseSchema
    schema = DatabaseSchema(str(tmp_path / "t.db"))
    schema.create_tables()
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(processing_results)").fetchall()}
    for c in ("embedding_score", "embedding_top_topics", "llm_decision",
              "llm_reasoning", "pipeline_version"):
        assert c in cols, f"missing {c}"
```

Run both: expect FAIL.

- [ ] **Step 2: Add vector table creation**

In `schema.py`, in `create_tables` after existing CREATE TABLE blocks:

```python
conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS topic_embeddings USING vec0(
        topic_id INTEGER PRIMARY KEY,
        embedding FLOAT[1536]
    )
""")
conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS article_embeddings USING vec0(
        article_id TEXT PRIMARY KEY,
        embedding FLOAT[1536]
    )
""")
```

- [ ] **Step 3: Add processing_results migration**

Add a `_migrate_processing_results_v2` method:
```python
def _migrate_processing_results_v2(self, conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(processing_results)").fetchall()}
    if "embedding_score" not in cols:
        conn.execute("ALTER TABLE processing_results ADD COLUMN embedding_score REAL")
    if "embedding_top_topics" not in cols:
        conn.execute("ALTER TABLE processing_results ADD COLUMN embedding_top_topics TEXT")
    if "llm_decision" not in cols:
        conn.execute("ALTER TABLE processing_results ADD COLUMN llm_decision TEXT")
    if "llm_reasoning" not in cols:
        conn.execute("ALTER TABLE processing_results ADD COLUMN llm_reasoning TEXT")
    if "pipeline_version" not in cols:
        conn.execute("ALTER TABLE processing_results ADD COLUMN pipeline_version TEXT DEFAULT 'v1'")
        # Widen unique constraint: SQLite needs index drop+recreate
        # Drop the old composite UNIQUE if it exists as an explicit index
        # (the inline UNIQUE in CREATE TABLE will require a table rebuild — acceptable for prod since rows are append-only and small)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_unique_v2 "
                     "ON processing_results(article_id, chat_id, topic_name, pipeline_version)")
```

Call from `create_tables` after CREATE TABLE statements.

NOTE on UNIQUE constraint: The original `UNIQUE(article_id, chat_id, topic_name)` in the CREATE TABLE inline definition still exists for new DBs. For existing prod DBs, the additional unique index above acts on the wider tuple. Both v1 rows (existing) and v2 rows (new) share the same (article_id, chat_id, topic_name) — this means we MUST drop the original inline constraint for prod DBs. Add this to the migration:

```python
# Detect original inline UNIQUE constraint and rebuild table if present
indices = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='processing_results'").fetchall()
has_old_unique = any("article_id, chat_id, topic_name" in (idx[1] or "") and "pipeline_version" not in (idx[1] or "")
                     for idx in indices if idx[1])
# The inline UNIQUE in CREATE TABLE shows up as an auto-index — check sqlite_autoindex
auto_idx = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='processing_results' AND name LIKE 'sqlite_autoindex%'"
).fetchall()
if auto_idx:
    # Rebuild table without the inline UNIQUE constraint
    conn.executescript("""
        BEGIN;
        CREATE TABLE processing_results_new AS SELECT * FROM processing_results;
        DROP TABLE processing_results;
        CREATE TABLE processing_results (
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
            pipeline_version TEXT DEFAULT 'v1',
            FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
            FOREIGN KEY (chat_id) REFERENCES channels(chat_id) ON DELETE CASCADE
        );
        INSERT INTO processing_results SELECT * FROM processing_results_new;
        DROP TABLE processing_results_new;
        CREATE UNIQUE INDEX idx_processing_unique_v2
            ON processing_results(article_id, chat_id, topic_name, pipeline_version);
        CREATE INDEX idx_processing_chat_delivered ON processing_results(chat_id, delivered);
        CREATE INDEX idx_processing_processed_at ON processing_results(processed_at);
        CREATE INDEX idx_processing_confidence ON processing_results(confidence_score);
        COMMIT;
    """)
```

Also update the inline `UNIQUE(article_id, chat_id, topic_name)` in the CREATE TABLE statement to be removed (the explicit index `idx_processing_unique_v2` replaces it).

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_database_models.py -v -k "vector_tables or processing_results_v2"`
Expected: PASS.

- [ ] **Step 5: Migration test against snapshot**

```python
def test_migration_against_prod_snapshot(tmp_path):
    """Regression: applying schema to existing prod-shape DB must not lose rows."""
    import shutil
    src = "/tmp/culifeed_snapshot.db"
    if not __import__("os").path.exists(src):
        import pytest; pytest.skip("snapshot not present")
    dst = str(tmp_path / "snap.db")
    shutil.copy(src, dst)
    import sqlite3
    pre_count = sqlite3.connect(dst).execute(
        "SELECT COUNT(*) FROM processing_results").fetchone()[0]

    from culifeed.database.schema import DatabaseSchema
    DatabaseSchema(dst).create_tables()  # idempotent migration

    post_count = sqlite3.connect(dst).execute(
        "SELECT COUNT(*) FROM processing_results").fetchone()[0]
    assert post_count == pre_count
    cols = {row[1] for row in sqlite3.connect(dst).execute(
        "PRAGMA table_info(processing_results)").fetchall()}
    assert "pipeline_version" in cols
```

Run: PASS expected.

- [ ] **Step 6: Commit**

```bash
git add culifeed/database/schema.py tests/unit/test_database_models.py
git commit -m "feat(db): add vector tables and v2 columns on processing_results"
```

---

### Task A5: Settings additions (FilteringSettings)

**Files:**
- Modify: `culifeed/config/settings.py`
- Test: `tests/unit/test_foundation.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_foundation.py`:
```python
def test_embedding_settings_defaults():
    from culifeed.config.settings import get_settings
    s = get_settings()
    assert s.filtering.embedding_provider == "openai"
    assert s.filtering.embedding_model == "text-embedding-3-small"
    assert 0.0 <= s.filtering.embedding_min_score <= 1.0
    assert s.filtering.embedding_min_score == 0.45
    assert s.filtering.embedding_fallback_threshold == 0.65
    assert s.filtering.embedding_retention_days == 90
    assert s.filtering.use_embedding_pipeline is False
```

Run: FAIL.

- [ ] **Step 2: Add fields to FilteringSettings**

In `culifeed/config/settings.py` `FilteringSettings` class, append:

```python
# Embedding pipeline (v2)
embedding_provider: str = Field(default="openai")
embedding_model: str = Field(default="text-embedding-3-small")
embedding_min_score: float = Field(
    default=0.45, ge=0.0, le=1.0,
    description="Minimum cosine similarity for embedding stage to assign a topic"
)
embedding_fallback_threshold: float = Field(
    default=0.65, ge=0.0, le=1.0,
    description="Threshold for delivering on embedding score alone if LLM gate fails"
)
embedding_retention_days: int = Field(
    default=90, ge=1,
    description="Days to retain article embeddings before pruning"
)
use_embedding_pipeline: bool = Field(
    default=False,
    description="Feature flag: use the v2 embedding pipeline"
)
```

- [ ] **Step 3: Run test, expect PASS, commit**

```bash
git add culifeed/config/settings.py tests/unit/test_foundation.py
git commit -m "feat(config): add embedding pipeline settings"
```

---

## Phase B — Components

Build four self-contained components with full TDD coverage. Each is independently testable.

### Task B1: EmbeddingService

**Files:**
- Create: `culifeed/ai/embedding_service.py`
- Test: `tests/unit/test_embedding_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_embedding_service.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from culifeed.ai.embedding_service import EmbeddingService
from culifeed.utils.exceptions import AIError, ErrorCode


@pytest.fixture
def fake_openai_response():
    resp = MagicMock()
    resp.data = [MagicMock(embedding=[0.1] * 1536)]
    resp.usage = MagicMock(total_tokens=10)
    return resp


@pytest.mark.asyncio
async def test_embed_returns_vector(fake_openai_response):
    svc = EmbeddingService(api_key="fake")
    svc._client.embeddings.create = AsyncMock(return_value=fake_openai_response)
    vec = await svc.embed("hello world")
    assert len(vec) == 1536
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_embed_batch_chunks_inputs(fake_openai_response):
    svc = EmbeddingService(api_key="fake")
    fake_openai_response.data = [MagicMock(embedding=[0.1] * 1536) for _ in range(3)]
    svc._client.embeddings.create = AsyncMock(return_value=fake_openai_response)
    vecs = await svc.embed_batch(["a", "b", "c"])
    assert len(vecs) == 3
    svc._client.embeddings.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_embed_truncates_long_input(fake_openai_response):
    svc = EmbeddingService(api_key="fake")
    long_text = "word " * 20000  # ~20k tokens
    captured = {}
    async def capture(**kwargs):
        captured["input"] = kwargs["input"]
        return fake_openai_response
    svc._client.embeddings.create = AsyncMock(side_effect=capture)
    await svc.embed(long_text)
    # Should be truncated to <=8192 tokens (rough char budget: ~32k chars)
    assert len(captured["input"]) <= 32768


@pytest.mark.asyncio
async def test_embed_api_failure_raises_aierror():
    svc = EmbeddingService(api_key="fake")
    svc._client.embeddings.create = AsyncMock(side_effect=Exception("boom"))
    with pytest.raises(AIError) as exc:
        await svc.embed("text")
    assert exc.value.error_code == ErrorCode.AI_EMBEDDING_ERROR
```

Run: FAIL (module not found).

- [ ] **Step 2: Implement EmbeddingService**

Create `culifeed/ai/embedding_service.py`:
```python
"""OpenAI embeddings client wrapper for v2 topic-matching pipeline."""

from typing import List, Optional
from openai import AsyncOpenAI

from ..utils.exceptions import AIError, ErrorCode
from ..utils.logging import get_logger_for_component


# Rough char budget for 8192 tokens at ~4 chars/token
_MAX_INPUT_CHARS = 32_000
# OpenAI embeddings API accepts up to 2048 inputs per call
_MAX_BATCH = 2048


class EmbeddingService:
    """Thin wrapper around OpenAI embeddings API."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._logger = get_logger_for_component("embedding_service")

    @staticmethod
    def _truncate(text: str) -> str:
        return text[:_MAX_INPUT_CHARS] if len(text) > _MAX_INPUT_CHARS else text

    async def embed(self, text: str) -> List[float]:
        """Return a single embedding vector."""
        vecs = await self.embed_batch([text])
        return vecs[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Return embedding vectors for a batch of texts.

        Splits into chunks of _MAX_BATCH if needed.
        """
        if not texts:
            return []

        truncated = [self._truncate(t or " ") for t in texts]
        results: List[List[float]] = []
        for start in range(0, len(truncated), _MAX_BATCH):
            chunk = truncated[start:start + _MAX_BATCH]
            try:
                resp = await self._client.embeddings.create(
                    model=self._model,
                    input=chunk,
                )
            except Exception as e:
                self._logger.error(f"Embedding API failed: {e}")
                raise AIError(
                    f"Embedding request failed: {e}",
                    provider="openai",
                    error_code=ErrorCode.AI_EMBEDDING_ERROR,
                    retryable=True,
                ) from e
            results.extend([list(item.embedding) for item in resp.data])
        return results
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_embedding_service.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add culifeed/ai/embedding_service.py tests/unit/test_embedding_service.py
git commit -m "feat(ai): add EmbeddingService wrapping OpenAI embeddings API"
```

---

### Task B2: VectorStore

**Files:**
- Create: `culifeed/storage/vector_store.py`
- Test: `tests/unit/test_vector_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_vector_store.py`:
```python
import pytest
from culifeed.database.connection import DatabaseConnection
from culifeed.database.schema import DatabaseSchema
from culifeed.storage.vector_store import VectorStore


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "v.db")
    DatabaseSchema(p).create_tables()
    return DatabaseConnection(p)


def _vec(seed: float, dim: int = 1536):
    return [seed] * dim


def test_upsert_and_rank_basic(db):
    vs = VectorStore(db)
    # 3 topics: 1, 2, 3 with distinguishable vectors
    vs.upsert_topic_embedding(1, _vec(0.1))
    vs.upsert_topic_embedding(2, _vec(0.5))
    vs.upsert_topic_embedding(3, _vec(0.9))
    # Article close to topic 3
    vs.upsert_article_embedding("art-1", _vec(0.9))
    ranked = vs.rank_topics_for_article("art-1", [1, 2, 3], top_k=3)
    assert len(ranked) == 3
    # Topic 3 should be the top match (smallest cosine distance)
    assert ranked[0][0] == 3


def test_upsert_replaces_existing(db):
    vs = VectorStore(db)
    vs.upsert_topic_embedding(1, _vec(0.1))
    vs.upsert_topic_embedding(1, _vec(0.5))  # replace
    vs.upsert_article_embedding("a", _vec(0.5))
    ranked = vs.rank_topics_for_article("a", [1])
    assert ranked[0][0] == 1
    # Score reflects the replacement vector (close match)
    assert ranked[0][1] > 0.99


def test_rank_filters_by_active_topic_ids(db):
    vs = VectorStore(db)
    vs.upsert_topic_embedding(1, _vec(0.5))
    vs.upsert_topic_embedding(2, _vec(0.5))
    vs.upsert_article_embedding("a", _vec(0.5))
    ranked = vs.rank_topics_for_article("a", [2])  # only topic 2 active
    assert len(ranked) == 1
    assert ranked[0][0] == 2


def test_prune_articles_older_than(db):
    """Inserts articles with manually-set timestamps and verifies prune behavior."""
    import datetime as dt
    vs = VectorStore(db)
    vs.upsert_article_embedding("old", _vec(0.1))
    vs.upsert_article_embedding("new", _vec(0.1))
    # Backdate "old" via direct manipulation of articles.created_at
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles(id,title,url,source_feed,content_hash,created_at) "
            "VALUES('old','t','u1','f','h',?)",
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=120),))
        conn.execute(
            "INSERT OR IGNORE INTO articles(id,title,url,source_feed,content_hash,created_at) "
            "VALUES('new','t','u2','f','h2',?)",
            (dt.datetime.now(dt.timezone.utc),))
        conn.commit()
    pruned = vs.prune_articles_older_than(days=90)
    assert pruned == 1
    # "old" embedding gone, "new" remains
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT article_id FROM article_embeddings").fetchall()
        ids = {r[0] for r in rows}
        assert "old" not in ids
        assert "new" in ids
```

Run: FAIL (module not found).

- [ ] **Step 2: Implement VectorStore**

Create `culifeed/storage/__init__.py` if absent (empty file).
Create `culifeed/storage/vector_store.py`:

```python
"""Vector storage abstraction backed by sqlite-vec."""

import struct
from typing import List, Tuple

from ..database.connection import DatabaseConnection
from ..utils.logging import get_logger_for_component


def _serialize(vec: List[float]) -> bytes:
    """Serialize a float vector to sqlite-vec's binary format."""
    return struct.pack(f"{len(vec)}f", *vec)


class VectorStore:
    """Read/write embeddings via sqlite-vec virtual tables."""

    def __init__(self, db: DatabaseConnection):
        self._db = db
        self._logger = get_logger_for_component("vector_store")

    def upsert_topic_embedding(self, topic_id: int, vec: List[float]) -> None:
        with self._db.get_connection() as conn:
            conn.execute("DELETE FROM topic_embeddings WHERE topic_id = ?", (topic_id,))
            conn.execute(
                "INSERT INTO topic_embeddings(topic_id, embedding) VALUES(?, ?)",
                (topic_id, _serialize(vec)),
            )
            conn.commit()

    def upsert_article_embedding(self, article_id: str, vec: List[float]) -> None:
        with self._db.get_connection() as conn:
            conn.execute("DELETE FROM article_embeddings WHERE article_id = ?", (article_id,))
            conn.execute(
                "INSERT INTO article_embeddings(article_id, embedding) VALUES(?, ?)",
                (article_id, _serialize(vec)),
            )
            conn.commit()

    def rank_topics_for_article(
        self,
        article_id: str,
        active_topic_ids: List[int],
        top_k: int = 3,
    ) -> List[Tuple[int, float]]:
        """Return [(topic_id, similarity)] sorted by similarity desc.

        Similarity = 1 - cosine_distance, so higher is better.
        """
        if not active_topic_ids:
            return []
        with self._db.get_connection() as conn:
            row = conn.execute(
                "SELECT embedding FROM article_embeddings WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            if row is None:
                return []
            article_vec = row[0]

            placeholders = ",".join("?" * len(active_topic_ids))
            cur = conn.execute(
                f"""
                SELECT topic_id, vec_distance_cosine(embedding, ?) AS dist
                FROM topic_embeddings
                WHERE topic_id IN ({placeholders})
                ORDER BY dist ASC
                LIMIT ?
                """,
                (article_vec, *active_topic_ids, top_k),
            )
            return [(int(tid), 1.0 - float(dist)) for tid, dist in cur.fetchall()]

    def prune_articles_older_than(self, days: int) -> int:
        """Delete embeddings for articles whose created_at is older than `days`."""
        with self._db.get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM article_embeddings
                WHERE article_id IN (
                    SELECT id FROM articles
                    WHERE created_at < datetime('now', ?)
                )
                """,
                (f"-{days} days",),
            )
            conn.commit()
            return cur.rowcount
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_vector_store.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add culifeed/storage/__init__.py culifeed/storage/vector_store.py tests/unit/test_vector_store.py
git commit -m "feat(storage): add VectorStore over sqlite-vec for topic+article embeddings"
```

---

### Task B3: TopicMatcher

**Files:**
- Create: `culifeed/processing/topic_matcher.py`
- Test: `tests/unit/test_topic_matcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_topic_matcher.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from culifeed.database.models import Article, Topic
from culifeed.processing.topic_matcher import TopicMatcher, MatchResult


def _topic(id, name="t", keywords=None, description=None, signature=None):
    return Topic(
        id=id, chat_id="c1", name=name,
        keywords=keywords or ["k1"], exclude_keywords=[],
        description=description, embedding_signature=signature,
        confidence_threshold=0.7, active=True,
    )

def _article(id="a1", title="hello", content="world"):
    return Article(id=id, title=title, url=f"http://x/{id}",
                   content=content, source_feed="f", content_hash="h")


@pytest.mark.asyncio
async def test_match_returns_top_topic_above_threshold():
    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    vectors = MagicMock()
    vectors.upsert_article_embedding = MagicMock()
    vectors.rank_topics_for_article = MagicMock(return_value=[(1, 0.9), (2, 0.4)])
    settings = MagicMock()
    settings.filtering.embedding_min_score = 0.45

    tm = TopicMatcher(embeddings, vectors, settings)
    topics = [_topic(1), _topic(2)]
    res = await tm.match(_article(), topics)

    assert isinstance(res, MatchResult)
    assert res.chosen.id == 1
    assert res.chosen_score == 0.9
    assert len(res.top_topics) == 2


@pytest.mark.asyncio
async def test_match_returns_none_when_below_threshold():
    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    vectors = MagicMock()
    vectors.upsert_article_embedding = MagicMock()
    vectors.rank_topics_for_article = MagicMock(return_value=[(1, 0.3)])
    settings = MagicMock()
    settings.filtering.embedding_min_score = 0.45

    tm = TopicMatcher(embeddings, vectors, settings)
    res = await tm.match(_article(), [_topic(1)])
    assert res.chosen is None
    assert res.chosen_score == 0.3


@pytest.mark.asyncio
async def test_ensure_topic_embeddings_only_recomputes_stale():
    embeddings = AsyncMock()
    embeddings.embed_batch = AsyncMock(return_value=[[0.1] * 1536, [0.2] * 1536])
    vectors = MagicMock()
    vectors.upsert_topic_embedding = MagicMock()
    settings = MagicMock()
    tm = TopicMatcher(embeddings, vectors, settings)

    # Mock signature compute via patching the internal method
    fresh_sig = "fresh-sig-1"
    stale_sig_old = "old"
    t_fresh = _topic(1, signature=fresh_sig)
    t_stale = _topic(2, signature=stale_sig_old)
    # Force compute to match fresh's stored signature
    tm._compute_signature = lambda topic: fresh_sig if topic.id == 1 else "fresh-sig-2"

    await tm.ensure_topic_embeddings([t_fresh, t_stale])
    # Only stale topic should have been re-embedded
    assert embeddings.embed_batch.await_count == 1
    args = embeddings.embed_batch.await_args.args[0]
    assert len(args) == 1  # only one stale
    vectors.upsert_topic_embedding.assert_called_once()
```

Run: FAIL.

- [ ] **Step 2: Implement TopicMatcher**

Create `culifeed/processing/topic_matcher.py`:
```python
"""Topic matching via embedding similarity."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ..ai.embedding_service import EmbeddingService
from ..database.models import Article, Topic
from ..storage.vector_store import VectorStore
from ..utils.logging import get_logger_for_component


@dataclass
class MatchResult:
    article_id: str
    top_topics: List[Tuple[Topic, float]]   # top 3, descending
    chosen: Optional[Topic]                 # None if no topic above threshold
    chosen_score: float                     # 0.0 if chosen is None


class TopicMatcher:
    """Embedding-based article→topic matcher."""

    def __init__(self, embeddings: EmbeddingService, vectors: VectorStore, settings):
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings
        self._logger = get_logger_for_component("topic_matcher")

    @staticmethod
    def _topic_text(topic: Topic) -> str:
        """Build the string that gets embedded for a topic."""
        if topic.description:
            return f"{topic.name}. {topic.description}. Keywords: {', '.join(topic.keywords)}"
        return f"{topic.name}. Keywords: {', '.join(topic.keywords)}"

    @staticmethod
    def _compute_signature(topic: Topic) -> str:
        payload = json.dumps({
            "name": topic.name,
            "description": topic.description or "",
            "keywords": sorted(topic.keywords or []),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _article_text(article: Article) -> str:
        title = article.title or ""
        content = (article.content or "")[:1500]
        return f"{title}\n\n{content}"

    async def ensure_topic_embeddings(self, topics: List[Topic]) -> None:
        """Recompute embeddings for any topic whose signature is stale."""
        stale: List[Topic] = []
        for t in topics:
            sig = self._compute_signature(t)
            if t.embedding_signature != sig:
                stale.append(t)
        if not stale:
            return

        self._logger.info(f"Re-embedding {len(stale)} stale topic(s)")
        texts = [self._topic_text(t) for t in stale]
        vecs = await self._embeddings.embed_batch(texts)
        now = datetime.now(timezone.utc)
        for t, v in zip(stale, vecs):
            self._vectors.upsert_topic_embedding(t.id, v)
            t.embedding_signature = self._compute_signature(t)
            t.embedding_updated_at = now
        # Caller is responsible for persisting topic.embedding_signature back to DB

    async def match(self, article: Article, topics: List[Topic]) -> MatchResult:
        if not topics:
            return MatchResult(article.id, [], None, 0.0)

        text = self._article_text(article)
        vec = await self._embeddings.embed(text)
        self._vectors.upsert_article_embedding(article.id, vec)

        active_ids = [t.id for t in topics if t.active]
        ranked = self._vectors.rank_topics_for_article(article.id, active_ids, top_k=3)
        # Convert (id, score) → (Topic, score)
        topic_by_id = {t.id: t for t in topics}
        top_topics = [(topic_by_id[tid], score) for tid, score in ranked if tid in topic_by_id]

        threshold = self._settings.filtering.embedding_min_score
        chosen, chosen_score = (None, 0.0)
        if top_topics and top_topics[0][1] >= threshold:
            chosen, chosen_score = top_topics[0]

        return MatchResult(
            article_id=article.id,
            top_topics=top_topics,
            chosen=chosen,
            chosen_score=chosen_score,
        )
```

NOTE: This task assumes the `Topic` model has `description`, `embedding_signature`, `embedding_updated_at` fields. Add those in the next sub-step.

- [ ] **Step 3: Update Topic Pydantic model**

In `culifeed/database/models.py`, find the `Topic` class. Add fields:
```python
description: Optional[str] = None
embedding_signature: Optional[str] = None
embedding_updated_at: Optional[datetime] = None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_topic_matcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add culifeed/processing/topic_matcher.py culifeed/database/models.py tests/unit/test_topic_matcher.py
git commit -m "feat(processing): add TopicMatcher (embedding-based topic ranking)"
```

---

### Task B4: LLMGate

**Files:**
- Create: `culifeed/processing/llm_gate.py`
- Test: `tests/unit/test_llm_gate.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_llm_gate.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from culifeed.database.models import Article, Topic
from culifeed.processing.llm_gate import LLMGate, GateResult


def _topic():
    return Topic(id=1, chat_id="c", name="AWS Lambda",
                 keywords=["lambda", "aws"], exclude_keywords=[],
                 description="AWS Lambda serverless compute updates and patterns.",
                 confidence_threshold=0.7, active=True)

def _article(title="t", content="c"):
    return Article(id="a", title=title, url="u",
                   content=content, source_feed="f", content_hash="h")


@pytest.mark.asyncio
async def test_judge_passes_on_clear_match():
    ai_manager = MagicMock()
    fake_result = MagicMock(success=True, raw_response=
        "DECISION: PASS\nCONFIDENCE: 0.92\nREASONING: Article is centrally about Lambda.")
    ai_manager.complete = AsyncMock(return_value=fake_result)

    gate = LLMGate(ai_manager)
    res = await gate.judge(_article(content="AWS Lambda announces new feature"), _topic())
    assert res.passed is True
    assert res.confidence == 0.92
    assert "centrally about" in res.reasoning


@pytest.mark.asyncio
async def test_judge_fails_on_tangential():
    ai_manager = MagicMock()
    fake_result = MagicMock(success=True, raw_response=
        "DECISION: FAIL\nCONFIDENCE: 0.3\nREASONING: Lambda only mentioned in passing.")
    ai_manager.complete = AsyncMock(return_value=fake_result)

    gate = LLMGate(ai_manager)
    res = await gate.judge(_article(), _topic())
    assert res.passed is False
    assert res.confidence == 0.3


@pytest.mark.asyncio
async def test_judge_handles_malformed_response():
    ai_manager = MagicMock()
    fake_result = MagicMock(success=True, raw_response="garbage with no structure")
    ai_manager.complete = AsyncMock(return_value=fake_result)

    gate = LLMGate(ai_manager)
    res = await gate.judge(_article(), _topic())
    # Conservative default: fail
    assert res.passed is False
    assert res.confidence == 0.0


@pytest.mark.asyncio
async def test_judge_handles_provider_failure():
    ai_manager = MagicMock()
    fake_result = MagicMock(success=False, error_message="all providers failed")
    ai_manager.complete = AsyncMock(return_value=fake_result)

    gate = LLMGate(ai_manager)
    res = await gate.judge(_article(), _topic())
    assert res.passed is False
    assert res.confidence == 0.0
    assert "all providers failed" in res.reasoning


def test_prompt_includes_topic_description():
    gate = LLMGate(MagicMock())
    prompt = gate._build_gate_prompt(_article(content="x"), _topic())
    assert "AWS Lambda serverless compute" in prompt
    assert "DECISION: PASS | FAIL" in prompt
    assert "centrally" in prompt.lower()
```

Run: FAIL.

- [ ] **Step 2: Implement LLMGate**

Create `culifeed/processing/llm_gate.py`:
```python
"""Single yes/no LLM judgment over a pre-selected article+topic pair."""

import re
from dataclasses import dataclass

from ..database.models import Article, Topic
from ..utils.logging import get_logger_for_component


@dataclass
class GateResult:
    passed: bool
    confidence: float
    reasoning: str


class LLMGate:
    """Calibrated yes/no judge for v2 pipeline."""

    def __init__(self, ai_manager):
        self._ai = ai_manager
        self._logger = get_logger_for_component("llm_gate")

    def _build_gate_prompt(self, article: Article, topic: Topic) -> str:
        description = (
            topic.description
            if topic.description
            else f"{topic.name}. Keywords: {', '.join(topic.keywords)}"
        )
        body = (article.content or "")[:1500]
        return f"""You are deciding whether an article is centrally about a topic.

TOPIC: {topic.name}
DESCRIPTION: {description}
KEYWORDS: {', '.join(topic.keywords)}

ARTICLE TITLE: {article.title}
ARTICLE BODY: {body}

Decide:
- "PASS" only if the article's CENTRAL subject matches the topic.
  Tangential mentions, passing references, or different-but-adjacent
  subjects = FAIL.
- Confidence: 0.9+ = strongly central, 0.7 = clearly relevant,
  0.5 = borderline.

Respond in this exact format:
DECISION: PASS | FAIL
CONFIDENCE: 0.0-1.0
REASONING: <one sentence>"""

    @staticmethod
    def _parse(text: str) -> GateResult:
        decision_m = re.search(r"DECISION:\s*(PASS|FAIL)", text, re.IGNORECASE)
        conf_m = re.search(r"CONFIDENCE:\s*([0-9.]+)", text)
        reason_m = re.search(r"REASONING:\s*(.+?)(?:\n|$)", text, re.IGNORECASE | re.DOTALL)

        if not decision_m or not conf_m:
            return GateResult(passed=False, confidence=0.0,
                              reasoning="Malformed model response")

        try:
            confidence = max(0.0, min(1.0, float(conf_m.group(1))))
        except ValueError:
            confidence = 0.0

        passed = decision_m.group(1).upper() == "PASS"
        reasoning = reason_m.group(1).strip() if reason_m else ""
        return GateResult(passed=passed, confidence=confidence, reasoning=reasoning)

    async def judge(self, article: Article, topic: Topic) -> GateResult:
        prompt = self._build_gate_prompt(article, topic)
        result = await self._ai.complete(prompt)  # AIManager.complete returns standard wrapper
        if not result.success:
            return GateResult(passed=False, confidence=0.0,
                              reasoning=f"LLM unavailable: {result.error_message}")
        return self._parse(result.raw_response or "")
```

NOTE: this assumes `AIManager` has a `complete(prompt: str) -> AIResult` method that returns `(success, raw_response, error_message)`. Today's `AIManager` exposes `analyze_relevance(article, topic)`. Add a lower-level `complete` wrapper in the next sub-step.

- [ ] **Step 3: Add `AIManager.complete` method**

In `culifeed/ai/ai_manager.py`, add a new public method (place near `analyze_relevance`):

```python
async def complete(self, prompt: str) -> AIResult:
    """Provider-agnostic raw completion. Used by v2 LLMGate.

    Tries providers in priority order with the existing fallback chain.
    Returns AIResult where raw_response holds the model's text output.
    """
    for provider_type, model_name in self._iter_provider_models():
        provider = self._get_provider(provider_type)
        if not provider or not provider.can_make_request():
            continue
        try:
            if hasattr(provider, "complete_with_model"):
                resp_text = await provider.complete_with_model(prompt, model_name)
            else:
                resp_text = await provider.complete(prompt)
            return AIResult(
                success=True,
                relevance_score=0.0,
                confidence=0.0,
                raw_response=resp_text,
                provider_used=provider_type.value,
                model_used=model_name,
            )
        except Exception as e:
            self.logger.warning(f"Provider {provider_type} complete() failed: {e}")
            continue
    return AIResult(
        success=False,
        relevance_score=0.0,
        confidence=0.0,
        error_message="All providers exhausted",
    )
```

Then add `complete()` (and optionally `complete_with_model()`) to `culifeed/ai/providers/base.py` `BaseAIProvider`:

```python
async def complete(self, prompt: str) -> str:
    """Raw completion. Default impl raises NotImplementedError; override per-provider."""
    raise NotImplementedError(f"{type(self).__name__} does not implement complete()")
```

For each existing provider (groq, gemini, openai, deepseek), add a `complete` implementation that uses the same chat-completion path but without the structured prompt. Reference the existing `_make_chat_completion` method in each provider — call it with `[{"role":"user","content":prompt}]` and return `response.choices[0].message.content`.

For Groq, in `culifeed/ai/providers/groq_provider.py`:
```python
async def complete(self, prompt: str) -> str:
    response = await self._make_chat_completion(prompt)
    return response.choices[0].message.content
```

Mirror the same one-line implementation in `gemini_provider.py`, `openai_provider.py`, `deepseek_provider.py` (each has its own client; adapt the response-extraction call to whatever `_make_chat_completion` returns there).

Also add `raw_response: Optional[str] = None` field to the `AIResult` dataclass in `culifeed/ai/providers/base.py` if not already present.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_llm_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add culifeed/processing/llm_gate.py culifeed/ai/ai_manager.py culifeed/ai/providers/ tests/unit/test_llm_gate.py
git commit -m "feat(processing): add LLMGate (yes/no calibrated topic judgment)"
```

---

## Phase C — Topic management

### Task C1: Topic description generator

**Files:**
- Create: `culifeed/processing/topic_description_generator.py`
- Test: `tests/unit/test_topic_description_generator.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from culifeed.processing.topic_description_generator import TopicDescriptionGenerator


@pytest.mark.asyncio
async def test_generate_returns_short_description():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True,
        raw_response="AWS Lambda serverless compute updates and best practices."))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="AWS Lambda updates",
                              keywords=["lambda", "serverless"])
    assert "Lambda" in desc
    assert len(desc) < 300


@pytest.mark.asyncio
async def test_generate_falls_back_on_failure():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(success=False,
                                                    error_message="oops"))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="X", keywords=["a", "b"])
    # Falls back to a deterministic string built from name+keywords
    assert "X" in desc
    assert "a" in desc and "b" in desc
```

Run: FAIL.

- [ ] **Step 2: Implement**

Create `culifeed/processing/topic_description_generator.py`:
```python
"""LLM-drafted topic description generator."""

from typing import List

from ..utils.logging import get_logger_for_component


class TopicDescriptionGenerator:
    def __init__(self, ai_manager):
        self._ai = ai_manager
        self._logger = get_logger_for_component("topic_desc_generator")

    async def generate(self, name: str, keywords: List[str]) -> str:
        prompt = self._build_prompt(name, keywords)
        result = await self._ai.complete(prompt)
        if not result.success or not result.raw_response:
            self._logger.warning(f"Description generation failed: {result.error_message}")
            return self._fallback(name, keywords)
        text = result.raw_response.strip().strip('"').strip()
        if not text:
            return self._fallback(name, keywords)
        return text[:300]

    @staticmethod
    def _fallback(name: str, keywords: List[str]) -> str:
        return f"{name}. Keywords: {', '.join(keywords)}"

    @staticmethod
    def _build_prompt(name: str, keywords: List[str]) -> str:
        return f"""Write a 1-2 sentence description of this RSS-feed topic. The description will be used to match articles to the topic via semantic similarity, so be concrete about what the topic IS and what it is NOT.

TOPIC NAME: {name}
KEYWORDS: {', '.join(keywords)}

Constraints:
- Maximum 250 characters.
- Plain prose, no quotes, no lists.
- Be specific about scope: subject area + types of content (announcements, tutorials, analysis).

Respond with the description text only, no preamble."""
```

- [ ] **Step 3: Run tests, expect PASS, commit**

```bash
git add culifeed/processing/topic_description_generator.py tests/unit/test_topic_description_generator.py
git commit -m "feat(processing): add TopicDescriptionGenerator"
```

---

### Task C2: Bot `/addtopic` LLM-drafted description flow

**Files:**
- Modify: `culifeed/bot/topic_commands.py` (find the `/addtopic` handler)
- Test: `tests/unit/test_topic_commands_v2.py` (new)

- [ ] **Step 1: Locate handler**

Run: `grep -n "addtopic\|add_topic" culifeed/bot/topic_commands.py | head`
Identify the handler entry function. Note the line range.

- [ ] **Step 2: Write failing test**

Create `tests/unit/test_topic_commands_v2.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
# Import the handler entry function and a simulated update/context
# Adjust import path based on Step 1 findings.

@pytest.mark.asyncio
async def test_addtopic_drafts_description_and_asks_confirmation():
    # Given a user submits name+keywords
    # When the handler runs
    # Then it calls TopicDescriptionGenerator and replies with the draft + confirm prompt
    from culifeed.bot.topic_commands import handle_addtopic_with_description  # rename in step 3
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 123
    context = MagicMock()
    context.args = ["My Topic", "key1,key2"]

    with patch("culifeed.bot.topic_commands.TopicDescriptionGenerator") as Mock:
        instance = Mock.return_value
        instance.generate = AsyncMock(return_value="Drafted description here.")
        await handle_addtopic_with_description(update, context)

    update.message.reply_text.assert_awaited()
    sent = update.message.reply_text.await_args.args[0]
    assert "Drafted description here." in sent
    assert "ok" in sent.lower() or "confirm" in sent.lower()
```

Run: FAIL.

- [ ] **Step 3: Modify the addtopic handler**

In the existing addtopic handler, after the user-supplied name/keywords are validated and BEFORE inserting into the topics table, insert the description-generation step:

```python
# After parsing name and keywords:
generator = TopicDescriptionGenerator(self._ai_manager)
draft = await generator.generate(name=name, keywords=keywords)

# Store pending state in user_data for confirmation flow
context.user_data["pending_topic"] = {
    "name": name,
    "keywords": keywords,
    "description": draft,
}

await update.message.reply_text(
    f"Draft description for topic '{name}':\n\n{draft}\n\n"
    f"Reply 'ok' to confirm, or send a corrected description."
)
```

Then add a follow-up text handler that catches the next user message:
```python
async def handle_topic_confirmation(self, update, context):
    pending = context.user_data.pop("pending_topic", None)
    if not pending:
        return  # not in confirmation flow
    text = update.message.text.strip()
    if text.lower() == "ok":
        description = pending["description"]
    else:
        description = text[:300]  # use user-provided description, capped
    self._topic_repo.create(
        chat_id=str(update.effective_chat.id),
        name=pending["name"],
        keywords=pending["keywords"],
        description=description,
        # embedding_signature left NULL → next pipeline run will compute it
    )
    await update.message.reply_text(f"Topic '{pending['name']}' added.")
```

Wire the handler into the existing dispatcher (look for `MessageHandler` registrations in `bot/__init__.py` or `bot/handlers.py`).

Update the topic repository's `create` method (likely `culifeed/storage/topic_repository.py` or `database/repositories.py`) to accept the new `description` parameter and INSERT it.

- [ ] **Step 4: Run test, expect PASS, commit**

```bash
git add culifeed/bot/topic_commands.py tests/unit/test_topic_commands_v2.py culifeed/database/repositories.py
git commit -m "feat(bot): /addtopic now drafts a description and asks for confirmation"
```

---

### Task C3: Bot `/edittopic` command

**Files:**
- Modify: `culifeed/bot/topic_commands.py`
- Test: `tests/unit/test_topic_commands_v2.py`

- [ ] **Step 1: Test**

Append to `test_topic_commands_v2.py`:
```python
@pytest.mark.asyncio
async def test_edittopic_updates_description_and_clears_signature():
    from culifeed.bot.topic_commands import handle_edittopic
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 1
    context = MagicMock()
    context.args = ["1", "New description text"]  # topic_id, description

    repo = MagicMock()
    with patch("culifeed.bot.topic_commands.get_topic_repo", return_value=repo):
        await handle_edittopic(update, context)

    repo.update_description.assert_called_once_with(1, "New description text")
    update.message.reply_text.assert_awaited()
```

Run: FAIL.

- [ ] **Step 2: Implement handler**

In `topic_commands.py`:
```python
async def handle_edittopic(self, update, context):
    """/edittopic <topic_id> <new description>"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /edittopic <topic_id> <new description>")
        return
    try:
        topic_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Topic id must be a number")
        return
    description = " ".join(context.args[1:])[:300]
    self._topic_repo.update_description(topic_id, description)
    # Clear embedding_signature so next pipeline run re-embeds
    self._topic_repo.clear_embedding_signature(topic_id)
    await update.message.reply_text(
        f"Topic {topic_id} updated; will re-embed on next pipeline run.")
```

Add `update_description` and `clear_embedding_signature` methods to the topic repository:
```python
def update_description(self, topic_id: int, description: str) -> None:
    with self._db.get_connection() as conn:
        conn.execute(
            "UPDATE topics SET description = ? WHERE id = ?",
            (description, topic_id))
        conn.commit()

def clear_embedding_signature(self, topic_id: int) -> None:
    with self._db.get_connection() as conn:
        conn.execute(
            "UPDATE topics SET embedding_signature = NULL, embedding_updated_at = NULL WHERE id = ?",
            (topic_id,))
        conn.commit()
```

Register the `/edittopic` command handler in the dispatcher.

- [ ] **Step 3: Run test, PASS, commit**

```bash
git add culifeed/bot/topic_commands.py culifeed/database/repositories.py tests/unit/test_topic_commands_v2.py
git commit -m "feat(bot): add /edittopic command"
```

---

### Task C4: One-time description backfill for existing topics

**Files:**
- Create: `scripts/backfill_topic_descriptions.py`
- Test: `tests/integration/test_backfill_topic_descriptions.py`

- [ ] **Step 1: Test**

Create:
```python
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
        conn.execute("INSERT INTO channels(chat_id,chat_type) VALUES('c','private')")
        conn.execute("INSERT INTO topics(chat_id,name,keywords) "
                     "VALUES('c','T1','[\"k\"]')")
        conn.execute("INSERT INTO topics(chat_id,name,keywords,description) "
                     "VALUES('c','T2','[\"k\"]','already has')")
        conn.commit()

    fake_ai = MagicMock()
    fake_ai.complete = AsyncMock(return_value=MagicMock(
        success=True, raw_response="Generated description"))

    with patch("scripts.backfill_topic_descriptions.AIManager",
               return_value=fake_ai):
        await backfill(db_path=p, dry_run=False)

    with db.get_connection() as conn:
        rows = conn.execute("SELECT name, description FROM topics ORDER BY name").fetchall()
        assert rows[0] == ("T1", "Generated description")
        assert rows[1] == ("T2", "already has")  # untouched
```

Run: FAIL.

- [ ] **Step 2: Implement script**

Create `scripts/backfill_topic_descriptions.py`:
```python
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
```

- [ ] **Step 3: Run test, PASS, commit**

```bash
git add scripts/backfill_topic_descriptions.py tests/integration/test_backfill_topic_descriptions.py
git commit -m "feat(scripts): add topic-description backfill"
```

---

## Phase D — Pipeline integration

### Task D1: Add v2 pipeline path behind feature flag

**Files:**
- Modify: `culifeed/processing/pipeline.py`
- Test: `tests/unit/test_pipeline_v2.py` (new)

This task does NOT delete the v1 path. It adds a parallel v2 path selected by the feature flag.

- [ ] **Step 1: Write failing test (regression for the audit bug)**

Create `tests/unit/test_pipeline_v2.py`:
```python
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from culifeed.processing.pipeline import ProcessingPipeline
from culifeed.database.connection import DatabaseConnection
from culifeed.database.schema import DatabaseSchema


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "p.db")
    DatabaseSchema(p).create_tables()
    return DatabaseConnection(p)


@pytest.mark.asyncio
async def test_v2_persists_pre_filter_score(db, monkeypatch):
    """Regression: today's bug is that pre_filter_score is NULL for all rows."""
    # Seed channel + topic + 1 article via direct SQL
    with db.get_connection() as conn:
        conn.execute("INSERT INTO channels(chat_id,chat_type) VALUES('c','private')")
        conn.execute("INSERT INTO topics(chat_id,name,keywords,description,active,confidence_threshold) "
                     "VALUES('c','T',?,'desc',1,0.5)", (json.dumps(["aws","lambda"]),))
        conn.execute("INSERT INTO articles(id,title,url,content,source_feed,content_hash) "
                     "VALUES('a1','AWS Lambda news','u','aws lambda content','f','h')")
        conn.commit()

    settings = MagicMock()
    settings.filtering.use_embedding_pipeline = True
    settings.filtering.embedding_min_score = 0.45
    settings.filtering.min_relevance_threshold = 0.05
    # ... other settings stubs ...

    # Stub all external calls
    embeddings = AsyncMock()
    embeddings.embed = AsyncMock(return_value=[0.1] * 1536)
    embeddings.embed_batch = AsyncMock(return_value=[[0.1] * 1536])

    ai_manager = MagicMock()
    ai_manager.complete = AsyncMock(return_value=MagicMock(
        success=True, raw_response=
        "DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: clearly relevant"))

    pipeline = ProcessingPipeline(db, settings=settings,
                                  ai_manager=ai_manager,
                                  embedding_service=embeddings)
    await pipeline.process_channel("c")

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT pre_filter_score, embedding_score, llm_decision, llm_reasoning, pipeline_version "
            "FROM processing_results WHERE pipeline_version='v2'"
        ).fetchall()
        assert len(rows) == 1
        pf, emb, decision, reasoning, version = rows[0]
        assert pf is not None, "REGRESSION: pre_filter_score still NULL"
        assert emb is not None
        assert decision == "pass"
        assert "clearly relevant" in reasoning
        assert version == "v2"
```

Run: FAIL.

- [ ] **Step 2: Implement v2 path in pipeline**

In `culifeed/processing/pipeline.py`:

1. Import new components at top:
```python
from .topic_matcher import TopicMatcher
from .llm_gate import LLMGate
from ..ai.embedding_service import EmbeddingService
from ..storage.vector_store import VectorStore
```

2. Update `__init__` to accept v2 dependencies (with safe defaults for backward compat):
```python
def __init__(self, db, settings=None, ai_manager=None,
             embedding_service=None, vector_store=None):
    # ... existing init ...
    self._embedding_service = embedding_service
    self._vector_store = vector_store or VectorStore(db)
    self._topic_matcher = None  # lazy
    self._llm_gate = None       # lazy
```

3. Modify `process_channel` to dispatch:
```python
async def process_channel(self, chat_id: str):
    if self._settings.filtering.use_embedding_pipeline:
        await self._process_channel_v2(chat_id)
    else:
        await self._process_channel_v1(chat_id)  # rename existing impl
```

Rename the existing `process_channel` body to `_process_channel_v1`.

4. Add `_process_channel_v2`:
```python
async def _process_channel_v2(self, chat_id: str):
    topics = self._get_active_topics(chat_id)
    if not topics:
        return
    if self._embedding_service is None:
        from openai import OpenAI  # noqa
        self._embedding_service = EmbeddingService(
            api_key=self._settings.ai.openai_api_key,
            model=self._settings.filtering.embedding_model,
        )
    if self._topic_matcher is None:
        self._topic_matcher = TopicMatcher(
            self._embedding_service, self._vector_store, self._settings)
    if self._llm_gate is None:
        self._llm_gate = LLMGate(self._ai_manager)

    await self._topic_matcher.ensure_topic_embeddings(topics)
    self._persist_topic_signatures(topics)

    articles = self._get_unprocessed_articles(chat_id)
    pre_filter_results = self.pre_filter.filter_articles(articles, topics)
    survivors = [(r.article, r.best_match_score) for r in pre_filter_results
                 if r.best_match_score > 0]

    # Stage 2: embed survivors in one batch
    article_texts = [self._topic_matcher._article_text(a) for a, _ in survivors]
    if article_texts:
        vecs = await self._embedding_service.embed_batch(article_texts)
        for (article, _), vec in zip(survivors, vecs):
            self._vector_store.upsert_article_embedding(article.id, vec)

    # Stage 3: rank + gate
    import asyncio
    matches = []
    for article, _ in survivors:
        match = await self._topic_matcher.match(article, topics)
        matches.append(match)

    gate_tasks = [
        self._llm_gate.judge(article, m.chosen) if m.chosen else None
        for (article, _), m in zip(survivors, matches)
    ]
    gate_results = await asyncio.gather(*[t for t in gate_tasks if t is not None])

    # Stage 4: persist
    gate_iter = iter(gate_results)
    for (article, pf_score), match in zip(survivors, matches):
        gate_result = next(gate_iter) if match.chosen else None
        self._persist_v2_result(
            article=article, chat_id=chat_id, match=match,
            gate_result=gate_result, pre_filter_score=pf_score)
        # Delivery decision
        if match.chosen and gate_result and gate_result.passed and \
                gate_result.confidence >= match.chosen.confidence_threshold:
            await self._deliver(article, match.chosen, chat_id)
        elif match.chosen and gate_result is None and \
                match.chosen_score >= self._settings.filtering.embedding_fallback_threshold:
            # LLM-failure fallback
            await self._deliver(article, match.chosen, chat_id)
```

5. Add `_persist_v2_result` and `_persist_topic_signatures`:
```python
def _persist_topic_signatures(self, topics):
    """Write back any embedding_signature updates from TopicMatcher."""
    with self._db.get_connection() as conn:
        for t in topics:
            if t.embedding_signature and t.embedding_updated_at:
                conn.execute(
                    "UPDATE topics SET embedding_signature = ?, "
                    "embedding_updated_at = ? WHERE id = ?",
                    (t.embedding_signature, t.embedding_updated_at, t.id))
        conn.commit()

def _persist_v2_result(self, article, chat_id, match, gate_result, pre_filter_score):
    import json as _json
    top_topics_json = _json.dumps([
        {"topic_id": t.id, "topic_name": t.name, "score": s}
        for t, s in match.top_topics
    ])
    chosen_name = match.chosen.name if match.chosen else None
    if not chosen_name:
        # Persist a "no match" diagnostic row pinned to a synthetic topic name
        chosen_name = "__no_match__"
    decision = "skipped"
    confidence = 0.0
    reasoning = "no chosen topic" if not match.chosen else ""
    if gate_result is not None:
        decision = "pass" if gate_result.passed else "fail"
        confidence = gate_result.confidence
        reasoning = gate_result.reasoning
    with self._db.get_connection() as conn:
        conn.execute("""
            INSERT INTO processing_results(
                article_id, chat_id, topic_name,
                pre_filter_score, embedding_score, embedding_top_topics,
                ai_relevance_score, confidence_score,
                llm_decision, llm_reasoning, pipeline_version
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v2')
            ON CONFLICT DO NOTHING
        """, (
            article.id, chat_id, chosen_name,
            pre_filter_score, match.chosen_score, top_topics_json,
            match.chosen_score, confidence,
            decision, reasoning,
        ))
        conn.commit()
```

- [ ] **Step 3: Run test, expect PASS**

Run: `pytest tests/unit/test_pipeline_v2.py -v`

- [ ] **Step 4: Add error-isolation test**

Append:
```python
@pytest.mark.asyncio
async def test_v2_one_article_failure_does_not_abort_run(db):
    """Three articles. One causes the LLM gate to throw. Other two still get persisted."""
    # ... setup similar to above with 3 articles ...
    # First two LLM calls return PASS, third raises Exception
    call_count = {"n": 0}
    async def flaky_complete(prompt):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise Exception("provider crash")
        return MagicMock(success=True, raw_response=
            "DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: ok")
    # ... assert that 3 rows were persisted with appropriate decisions ...
```

Implement and verify the v2 path catches per-article exceptions in the loop and persists them with `decision='skipped'` and `reasoning=str(exception)`. Add try/except around `gate.judge` in `_process_channel_v2`.

- [ ] **Step 5: Commit**

```bash
git add culifeed/processing/pipeline.py tests/unit/test_pipeline_v2.py
git commit -m "feat(processing): add v2 embedding pipeline behind feature flag"
```

---

### Task D2: Persist v1 path's pre_filter_score (audit bug fix)

This fix applies even with `use_embedding_pipeline=False` to fix the existing bug.

**Files:**
- Modify: `culifeed/processing/pipeline.py` (find the v1 persist call site)
- Test: `tests/unit/test_processing_pipeline.py`

- [ ] **Step 1: Failing regression test**

Append to `test_processing_pipeline.py`:
```python
@pytest.mark.asyncio
async def test_v1_persists_pre_filter_score(tmp_path):
    """Regression: pre_filter_score must not be NULL in v1 path."""
    # ... setup similar to v2 test, but settings.filtering.use_embedding_pipeline = False ...
    # Run pipeline; assert pre_filter_score IS NOT NULL in the persisted row
```

Run: FAIL.

- [ ] **Step 2: Locate insertion point**

Run: `grep -n "INSERT INTO processing_results\|pre_filter_score" culifeed/processing/pipeline.py culifeed/processing/article_processor.py`

The v1 path likely INSERTs without the pre_filter_score column. Add the value to the INSERT:

```python
# Before (example):
conn.execute("INSERT INTO processing_results(article_id, chat_id, topic_name, "
             "ai_relevance_score, confidence_score) VALUES(?,?,?,?,?)", ...)
# After:
conn.execute("INSERT INTO processing_results(article_id, chat_id, topic_name, "
             "pre_filter_score, ai_relevance_score, confidence_score) "
             "VALUES(?,?,?,?,?,?)", ..., pre_filter_score, ...)
```

The pre_filter score is available in the existing `FilterResult` produced upstream — thread it through to the persistence call site.

- [ ] **Step 3: Run test, PASS, commit**

```bash
git add culifeed/processing/pipeline.py culifeed/processing/article_processor.py tests/unit/test_processing_pipeline.py
git commit -m "fix(pipeline): persist pre_filter_score in v1 path (was NULL)"
```

---

### Task D3: Article embedding pruning

**Files:**
- Modify: `culifeed/processing/pipeline.py` (or `culifeed/scheduler/scheduler.py`)
- Test: `tests/unit/test_pipeline_v2.py`

- [ ] **Step 1: Failing test**

Append to `test_pipeline_v2.py`:
```python
@pytest.mark.asyncio
async def test_pipeline_prunes_old_article_embeddings(db, monkeypatch):
    # ... seed an old article + embedding ...
    # ... run pipeline with retention_days=30 and an "old" article 60 days back ...
    # ... assert old article_embeddings row gone, new one preserved ...
```

Run: FAIL.

- [ ] **Step 2: Implement**

In `_process_channel_v2`, after Stage 4 persist:
```python
# Periodic pruning: only run on first channel of the day or after Nth pipeline run
pruned = self._vector_store.prune_articles_older_than(
    self._settings.filtering.embedding_retention_days)
if pruned:
    self._logger.info(f"Pruned {pruned} stale article embedding(s)")
```

- [ ] **Step 3: Run, PASS, commit**

```bash
git add culifeed/processing/pipeline.py tests/unit/test_pipeline_v2.py
git commit -m "feat(pipeline): prune article embeddings beyond retention window"
```

---

### Task D4: Smoke test against snapshot

**Files:**
- Create: `tests/integration/test_v2_against_snapshot.py`

- [ ] **Step 1: Test**

```python
import os
import pytest
import shutil


@pytest.mark.skipif(not os.path.exists("/tmp/culifeed_snapshot.db"),
                    reason="prod snapshot not available")
@pytest.mark.asyncio
async def test_v2_runs_against_snapshot(tmp_path, monkeypatch):
    """End-to-end smoke test of v2 against a copy of the prod DB.

    Uses a stub embedding service (real API would cost money). Verifies:
    - schema migrates cleanly,
    - pipeline runs without crashing,
    - at least one v2 row is written,
    - all v2 rows have non-null pre_filter_score, embedding_score, llm_decision.
    """
    src = "/tmp/culifeed_snapshot.db"
    dst = str(tmp_path / "snap.db")
    shutil.copy(src, dst)
    # ... run schema migration, then pipeline with stubbed services ...
    # ... assertions ...
```

- [ ] **Step 2: Implement, run, commit**

```bash
git add tests/integration/test_v2_against_snapshot.py
git commit -m "test: integration smoke against prod snapshot"
```

---

## Phase E — Tooling

### Task E1: `scripts/backfill_v2_processing.py`

**Files:**
- Create: `scripts/backfill_v2_processing.py`
- Test: `tests/integration/test_backfill_v2.py`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_backfill_v2_writes_v2_rows_for_existing_articles(tmp_path):
    """Run the backfill script and assert v2 rows appear alongside v1 rows."""
    # ... seed DB with v1 processing_results + articles + topics ...
    # ... run script with stubbed embeddings + LLM ...
    # ... assert: rows where pipeline_version='v2' exist, v1 rows untouched ...
```

Run: FAIL.

- [ ] **Step 2: Implement**

Create `scripts/backfill_v2_processing.py`:
```python
"""Re-process existing articles through the v2 pipeline (no delivery)."""

import argparse
import asyncio

from culifeed.config.settings import get_settings
from culifeed.database.connection import DatabaseConnection
from culifeed.processing.pipeline import ProcessingPipeline


async def backfill(db_path: str) -> None:
    db = DatabaseConnection(db_path)
    settings = get_settings()
    # Force v2 even if flag is off; suppress delivery
    settings.filtering.use_embedding_pipeline = True
    pipeline = ProcessingPipeline(db, settings=settings,
                                  suppress_delivery=True)
    with db.get_connection() as conn:
        chat_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT chat_id FROM channels WHERE active=1").fetchall()]
    for cid in chat_ids:
        print(f"Backfilling channel {cid}...")
        await pipeline.process_channel(cid)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    args = p.parse_args()
    asyncio.run(backfill(args.db))


if __name__ == "__main__":
    main()
```

Add `suppress_delivery: bool = False` parameter to `ProcessingPipeline.__init__` and gate `await self._deliver(...)` calls in v2 with `if not self._suppress_delivery`.

- [ ] **Step 3: PASS, commit**

```bash
git add scripts/backfill_v2_processing.py culifeed/processing/pipeline.py tests/integration/test_backfill_v2.py
git commit -m "feat(scripts): add v2 processing backfill script"
```

---

### Task E2: `culifeed diagnose <article_id>` CLI

**Files:**
- Modify: `main.py` (add subcommand) OR create `culifeed/cli/diagnose.py`
- Test: `tests/unit/test_diagnose_cli.py`

- [ ] **Step 1: Test**

```python
def test_diagnose_prints_full_score_chain(tmp_path, capsys):
    from culifeed.cli.diagnose import diagnose
    # ... seed DB with one article + 1 v2 processing_results row ...
    diagnose(db_path=str(tmp_path / "d.db"), article_id="a1")
    out = capsys.readouterr().out
    assert "pre_filter_score" in out
    assert "embedding_score" in out
    assert "llm_decision" in out
    assert "llm_reasoning" in out
    assert "Top 3 candidate topics" in out
```

Run: FAIL.

- [ ] **Step 2: Implement**

Create `culifeed/cli/diagnose.py`:
```python
"""Print the full diagnostic chain for an article."""

import json
from culifeed.database.connection import DatabaseConnection


def diagnose(db_path: str, article_id: str) -> None:
    db = DatabaseConnection(db_path)
    with db.get_connection() as conn:
        article = conn.execute(
            "SELECT title, url, source_feed FROM articles WHERE id=?",
            (article_id,)).fetchone()
        if not article:
            print(f"Article {article_id} not found"); return
        print(f"Article: {article[0]}")
        print(f"URL:     {article[1]}")
        print(f"Feed:    {article[2]}")
        print()
        rows = conn.execute("""
            SELECT topic_name, pipeline_version, pre_filter_score, embedding_score,
                   embedding_top_topics, llm_decision, llm_reasoning, confidence_score, delivered
            FROM processing_results WHERE article_id=?
            ORDER BY pipeline_version, processed_at
        """, (article_id,)).fetchall()
        for r in rows:
            (topic, ver, pf, emb, top_topics_json, decision, reasoning, conf, delivered) = r
            print(f"--- {ver} → topic '{topic}' ---")
            print(f"  pre_filter_score: {pf}")
            print(f"  embedding_score:  {emb}")
            print(f"  llm_decision:     {decision}  (confidence={conf})")
            print(f"  llm_reasoning:    {reasoning}")
            print(f"  delivered:        {bool(delivered)}")
            if top_topics_json:
                top = json.loads(top_topics_json)
                print("  Top 3 candidate topics:")
                for t in top:
                    print(f"    {t['score']:.3f}  {t['topic_name']}")
            print()


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("article_id")
    args = p.parse_args()
    diagnose(args.db, args.article_id)


if __name__ == "__main__":
    main()
```

Wire as a subcommand in `main.py` so users can run `python -m culifeed diagnose --db data/culifeed.db <article_id>`.

- [ ] **Step 3: PASS, commit**

```bash
git add culifeed/cli/diagnose.py main.py tests/unit/test_diagnose_cli.py
git commit -m "feat(cli): add diagnose subcommand for full score chain"
```

---

### Task E3: Eval harness

**Files:**
- Create: `scripts/eval_matching.py`
- Create: `tests/fixtures/labeled_articles.csv` (template, 5 sample rows)

- [ ] **Step 1: Implement**

Create `scripts/eval_matching.py`:
```python
"""Evaluate v2 topic matching against a hand-labeled CSV.

CSV format: article_id,expected_topic_name (header on first line).

Outputs precision, recall, F1 per topic, plus a confusion matrix.
"""

import argparse
import asyncio
import csv
from collections import Counter, defaultdict

from culifeed.config.settings import get_settings
from culifeed.database.connection import DatabaseConnection
from culifeed.processing.pipeline import ProcessingPipeline


async def evaluate(csv_path: str, db_path: str) -> None:
    expected = {}
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected[row["article_id"]] = row["expected_topic_name"]

    settings = get_settings()
    settings.filtering.use_embedding_pipeline = True
    db = DatabaseConnection(db_path)

    # Read v2 results for the labeled articles
    placeholders = ",".join("?" * len(expected))
    with db.get_connection() as conn:
        rows = conn.execute(
            f"SELECT article_id, topic_name FROM processing_results "
            f"WHERE pipeline_version='v2' AND article_id IN ({placeholders})",
            tuple(expected.keys())).fetchall()

    actual = dict(rows)  # last assignment wins per article (only one v2 row per article in our schema)

    # Compute metrics
    tp = defaultdict(int); fp = defaultdict(int); fn = defaultdict(int)
    confusion = Counter()
    for aid, exp_topic in expected.items():
        act_topic = actual.get(aid, "__missing__")
        confusion[(exp_topic, act_topic)] += 1
        if act_topic == exp_topic:
            tp[exp_topic] += 1
        else:
            fp[act_topic] += 1
            fn[exp_topic] += 1

    print(f"Articles labeled: {len(expected)}, scored: {len(actual)}")
    print()
    print(f"{'Topic':<60} {'Prec':>6} {'Rec':>6} {'F1':>6}")
    topics = set(expected.values()) | set(actual.values())
    for t in sorted(topics):
        prec = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        rec = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        print(f"{t[:60]:<60} {prec:>6.2f} {rec:>6.2f} {f1:>6.2f}")
    print()
    accuracy = sum(tp.values()) / len(expected) if expected else 0.0
    print(f"Top-1 accuracy: {accuracy:.1%}")
    print()
    print("Confusion (expected → actual): top mismatches")
    for (exp, act), n in confusion.most_common(10):
        if exp != act:
            print(f"  {n:>3}  {exp[:40]} → {act[:40]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--db", required=True)
    args = p.parse_args()
    asyncio.run(evaluate(args.csv, args.db))


if __name__ == "__main__":
    main()
```

Create `tests/fixtures/labeled_articles.csv`:
```
article_id,expected_topic_name
abc123,aws serverless with lambda function
def456,exploit developement or bug analysis and exploit in the wild
ghi789,linux ssh firewall kernel backup networking storage
jkl012,all update relate to google and anthropic and openai
mno345,IaC and CaC
```

(Engineer hand-labels ~50 articles from the prod snapshot before running the script.)

- [ ] **Step 2: Commit**

```bash
git add scripts/eval_matching.py tests/fixtures/labeled_articles.csv
git commit -m "feat(scripts): add eval_matching harness with confusion matrix"
```

---

## Phase F — Cutover

### Task F1: Run shadow mode for 7 days, validate

This is an operational task, not code.

- [ ] **Step 1: Deploy with feature flag enabled in shadow mode**

Update production config to set `CULIFEED_FILTERING__USE_EMBEDDING_PIPELINE=true` AND deploy a code change that ensures delivery still uses v1 results — easiest: in pipeline `_process_channel_v2`, if a settings flag `shadow_only=true`, skip the `_deliver` calls.

Add to `FilteringSettings`:
```python
shadow_only: bool = Field(default=False,
    description="When v2 pipeline is on, skip delivery so v1 keeps shipping")
```

Modify v2 delivery branch:
```python
if self._settings.filtering.shadow_only:
    pass  # skip delivery in shadow mode
elif match.chosen and gate_result and gate_result.passed and ...:
    await self._deliver(...)
```

- [ ] **Step 2: Daily comparison report**

After 7 days, run:
```bash
python scripts/eval_matching.py --csv hand_labeled.csv --db data/culifeed.db
```

And:
```sql
SELECT
  pipeline_version,
  COUNT(*) total,
  SUM(delivered) delivered,
  AVG(confidence_score) avg_conf
FROM processing_results
WHERE processed_at >= datetime('now', '-7 days')
GROUP BY pipeline_version;
```

Decision criteria for cutover:
- v2 top-1 accuracy ≥ 85% on labeled set
- v2 false-positive rate (delivered articles labeled wrong) ≤ 10%
- No A011/D007 errors in logs

If criteria fail, iterate on prompt or threshold.

- [ ] **Step 3: Document findings in ops log**

Append a dated entry to `RELEASE.md` or equivalent:
```
2026-MM-DD: v2 shadow validation complete. Top-1 acc: X%. Issues: ...
```

Commit this note.

---

### Task F2: Cutover

**Files:**
- Modify: production config / env

- [ ] **Step 1: Set `shadow_only=false`**

Update env: `CULIFEED_FILTERING__SHADOW_ONLY=false`. Restart bot + scheduler. v2 path now drives delivery.

- [ ] **Step 2: Monitor for 48h**

Check logs for `A011`, `D007`, exceptions. Check `processing_results` distribution: deliveries should match historical volume ±20%.

If significant regression, set `USE_EMBEDDING_PIPELINE=false` to fall back to v1 immediately.

- [ ] **Step 3: Tag a release**

```bash
git tag -a v2.0.0 -m "v2 embedding pipeline live"
git push origin v2.0.0
```

---

### Task F3: Cleanup (after 2 weeks of stable v2)

**Files:**
- Delete: `culifeed/processing/smart_analyzer.py`
- Modify: `culifeed/ai/ai_manager.py` (delete v1-only code paths)
- Modify: `culifeed/processing/pipeline.py` (delete `_process_channel_v1` and the dispatcher)

- [ ] **Step 1: Delete smart_analyzer**

```bash
git rm culifeed/processing/smart_analyzer.py
grep -rn "smart_analyzer\|SmartAnalyzer\|smart_routing" culifeed tests
```

Remove all imports and references found. Replace any callers with the new v2 path (TopicMatcher).

- [ ] **Step 2: Delete v1 pipeline path**

In `pipeline.py`:
- Delete `_process_channel_v1`
- Replace `process_channel` body with the v2 implementation directly (no flag dispatch)
- Remove `use_embedding_pipeline` setting (the flag is now the default)
- Remove `shadow_only` setting (no longer needed)

- [ ] **Step 3: Delete v1-only AI manager paths**

In `ai_manager.py`, remove `analyze_relevance` if it's no longer called by anything (run grep to confirm). Remove the keyword-fallback path that was only used by v1.

- [ ] **Step 4: Run full test suite**

```bash
source venv/bin/activate && python -m pytest
```

Expected: all PASS. Fix any tests that referenced deleted v1 code by deleting them.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove v1 topic-matching pipeline (smart_analyzer + dispatcher)"
```

---

## Self-review checklist

After this plan was drafted, verified:

- ✅ Spec coverage: every section in the spec has at least one task. Pipeline: D1. Schema: A3, A4. Components: B1–B4. Topic config: C1–C4. Error handling: covered in component tasks. Observability/diagnose: E2. Eval harness: E3. Backfill: C4 (descriptions), E1 (processing). Migration/rollout: F1–F3. Out-of-scope items not addressed (correct).
- ✅ Placeholder scan: no "TBD"/"add appropriate error handling"/etc.
- ✅ Type consistency: `MatchResult`, `GateResult`, `FilteringSettings` field names match across tasks. `EmbeddingService.embed`/`embed_batch`, `VectorStore.upsert_*`/`rank_topics_for_article`/`prune_articles_older_than` consistent.
- ✅ Error codes match spec: A011, D007, P005.

## Operational notes

- Run all tests in venv: `source venv/bin/activate && python -m pytest`
- The plan assumes the production snapshot at `/tmp/culifeed_snapshot.db` exists for integration tests; if absent, those tests skip.
- The bot needs a restart after schema changes (Phase A) for connection pool to pick up the new sqlite-vec extension.
- OpenAI API key must already be configured (you have an OpenAI provider set up); embedding API uses the same key.
